"""A generated, native Lovelace dashboard; no custom cards or YAML setup."""

import logging
from collections import defaultdict

from homeassistant.components import frontend
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.dashboard import LovelaceConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import cached_json_fragment

from .const import CONF_SHOW_DASHBOARD, DOMAIN, NAME

LOGGER = logging.getLogger(__name__)

FORMAT_TOKENS = """{% macro tokens(value) -%}
{% if not is_number(value) %}Unavailable
{% elif value | float >= 1000000000 %}{{ '%.1f' | format(value | float / 1000000000) }}B tokens
{% elif value | float >= 1000000 %}{{ '%.1f' | format(value | float / 1000000) }}M tokens
{% elif value | float >= 1000 %}{{ '%.1f' | format(value | float / 1000) }}K tokens
{% else %}{{ value | int }} tokens{% endif %}
{%- endmacro %}"""


def summary_card(period: str, entities: dict[str, str]) -> dict:
    """Use templates for readable numbers, keeping raw sensor units unchanged."""
    cost = entities.get(f"usage/all/{period}/cost", "sensor.t3_unavailable")
    tokens = entities.get(f"usage/all/{period}/tokens", "sensor.t3_unavailable")
    title = "Today" if period == "today" else "This month"
    return {
        "type": "markdown",
        "content": FORMAT_TOKENS
        + "\n"
        + f"## {title}\n"
        + "{% set cost = states('"
        + cost
        + "') %}\n"
        + "# {% if is_number(cost) %}${{ '%.2f' | format(cost | float) }}"
        + "{% else %}Unavailable{% endif %}\n"
        + "{{ tokens(states('"
        + tokens
        + "')) }}\n\nEstimated API cost",
        "entity_id": [cost, tokens],
    }


def build_dashboard(hass: HomeAssistant, entry) -> dict:
    """Resolve actual entity IDs, including those renamed by the user."""
    registry = er.async_get(hass)
    prefix = f"{entry.unique_id}/"
    entities = {
        item.unique_id.removeprefix(prefix): item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.platform == DOMAIN and item.unique_id.startswith(prefix) and not item.disabled
    }
    cards = [
        {
            "type": "horizontal-stack",
            "cards": [
                summary_card("today", entities),
                summary_card("month", entities),
            ],
        }
    ]
    # Warnings use templates so a failed refresh cannot leave a good-looking
    # cached total on the page without an explanation.
    status = entities.get("usage_status", "sensor.t3_unavailable")
    limits_status = entities.get("limits_status", "sensor.t3_unavailable")
    cost = entities.get("usage/all/month/cost", "sensor.t3_unavailable")
    cards.append(
        {
            "type": "markdown",
            "content": "{% if states('" + status + "') == 'partial' %}"
            "Some usage history is missing. Totals may be incomplete.\n\n{% endif %}"
            "{% if (state_attr('" + cost + "', 'unpriced_records') or 0) > 0 %}"
            "Some records have no price. Cost estimates are incomplete.\n\n{% endif %}"
            "{% if states('" + limits_status + "') == 'failed' %}"
            "Quota refresh failed. Previous limits are unavailable.\n\n{% endif %}"
            "{% if states('" + status + "') in ['unknown', 'unavailable'] %}"
            "Waiting for current usage readings.\n\n{% endif %}"
            "Readings refresh every five minutes by default.",
            "entity_id": [status, limits_status, cost],
        }
    )
    groups = defaultdict(list)
    for key, reading in entry.runtime_data.data.readings.items():
        if key.startswith("limit/") and key.endswith("/remaining") and key in entities:
            groups[reading.attributes["instance_id"]].append((key, reading))
    for readings in groups.values():
        provider_cards = []
        resets = []
        label = readings[0][1].attributes["provider_label"]
        for key, reading in readings:
            entity_id = entities[key]
            window = reading.attributes["window_label"]
            provider_cards.append(
                {
                    "type": "gauge",
                    "entity": entity_id,
                    "name": window,
                    "min": 0,
                    "max": 100,
                    "unit": "% left",
                    "needle": True,
                    "severity": {"red": 0, "yellow": 20, "green": 50},
                }
            )
            # Read the timestamp attribute at render time, so countdowns keep
            # working between server polls. Labels come from the entity, not
            # interpolated server text that could be interpreted as Jinja.
            resets.append(
                "{% set stamp = as_datetime(state_attr('"
                + entity_id
                + "', 'resets_at'), none) %}\n"
                "{{ state_attr('" + entity_id + "', 'window_label') }}: "
                "{% if states('" + entity_id + "') in ['unknown', 'unavailable'] %}Unavailable"
                "{% elif stamp and stamp > now() %}resets in {{ time_until(stamp, 1) }}"
                "{% elif stamp %}waiting for a new window"
                "{% else %}reset time not reported{% endif %}"
            )
        cards.append(
            {
                "type": "vertical-stack",
                "cards": [
                    {"type": "heading", "heading": label, "icon": "mdi:gauge"},
                    {
                        "type": "grid",
                        "columns": min(len(provider_cards), 3),
                        "square": False,
                        "cards": provider_cards,
                    },
                    {"type": "markdown", "content": "\n\n".join(resets)},
                ],
            }
        )
    if not groups:
        cards.append({"type": "markdown", "content": "No subscription limits reported yet."})
    # Keep the overview across the top and accounts in their own sections.
    # Plain masonry would distribute the warning and totals among the gauges.
    sections = [
        {
            "type": "grid",
            "column_span": 2,
            "cards": [{**card, "grid_options": {"columns": "full"}} for card in cards[:2]],
        }
    ]
    for card in cards[2:]:
        group_cards = card["cards"] if card["type"] == "vertical-stack" else [card]
        sections.append(
            {
                "type": "grid",
                "cards": [{**item, "grid_options": {"columns": "full"}} for item in group_cards],
            }
        )
    return {
        "title": NAME,
        "views": [
            {
                "title": "Overview",
                "path": "overview",
                "icon": "mdi:chart-box-outline",
                "type": "sections",
                "max_columns": 2,
                "sections": sections,
            }
        ],
    }


class T3Dashboard(LovelaceConfig):
    """An integration-owned view that is rebuilt from the entity registry."""

    def __init__(self, hass: HomeAssistant, path: str, entry) -> None:
        super().__init__(
            hass,
            path,
            {
                "id": path,
                "title": f"{NAME} · {entry.title}",
                "mode": "yaml",
                "show_in_sidebar": True,
                "require_admin": False,
            },
        )
        self.entry = entry

    @property
    def mode(self):
        return "yaml"

    async def async_load(self, force):
        return build_dashboard(self.hass, self.entry)

    async def async_json(self, force):
        return cached_json_fragment(await self.async_load(force))

    async def async_get_info(self):
        return {"mode": self.mode, "views": 1}


@callback
def async_register_dashboard(hass: HomeAssistant, entry) -> None:
    """Register only our own route; never replace a user's dashboard."""
    if not entry.options.get(CONF_SHOW_DASHBOARD, True):
        return
    data = hass.data.get(LOVELACE_DATA)
    if data is None:
        LOGGER.warning("T3 dashboard is unavailable because Lovelace is not loaded")
        return
    path = f"t3-code-{entry.entry_id}"
    if path in data.dashboards or frontend.async_panel_exists(hass, path):
        LOGGER.warning("T3 dashboard URL is already in use: %s", path)
        return
    dashboard = T3Dashboard(hass, path, entry)
    frontend.async_register_built_in_panel(
        hass,
        "lovelace",
        sidebar_title=f"{NAME} · {entry.title}",
        sidebar_icon="mdi:chart-box-outline",
        frontend_url_path=path,
        config={"mode": "yaml"},
    )
    data.dashboards[path] = dashboard

    @callback
    def cleanup():
        if data.dashboards.get(path) is dashboard:
            data.dashboards.pop(path)
            frontend.async_remove_panel(hass, path)

    entry.async_on_unload(cleanup)
    entry.async_on_unload(entry.runtime_data.async_add_listener(dashboard._config_updated))

    @callback
    def registry_changed(event):
        # Renames and newly discovered windows must refresh the layout as soon
        # as the registry knows their IDs, rather than waiting for the next poll.
        entity = er.async_get(hass).async_get(event.data["entity_id"])
        if entity and entity.config_entry_id == entry.entry_id:
            dashboard._config_updated()

    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_changed))
