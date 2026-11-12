"""A generated, native Lovelace dashboard; no custom cards or YAML setup."""

import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from homeassistant.components import frontend
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.components.lovelace.dashboard import LovelaceConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_bytes, json_fragment

from .const import CONF_SHOW_DASHBOARD, DOMAIN, NAME

LOGGER = logging.getLogger(__name__)

UNAVAILABLE_ENTITY = "sensor.t3_usage_monitor_missing"


def _lovelace_dashboards(hass: HomeAssistant) -> dict | None:
    """Return the Lovelace dashboard registry, or None when missing/reshaped.

    This is the single access point for the private Lovelace registry shape
    (``hass.data[LOVELACE_DATA].dashboards``). A refactor across the
    supported releases degrades to "no sidebar dashboard" instead of
    breaking setup.
    """
    try:
        data = hass.data.get(LOVELACE_DATA)
    except (AttributeError, TypeError):
        return None
    dashboards = getattr(data, "dashboards", None)
    if not isinstance(dashboards, dict):
        return None
    return dashboards


def _frontend_panels(hass: HomeAssistant) -> dict:
    """Return registered frontend panels, tolerating frontend refactors."""
    key = getattr(frontend, "DATA_PANELS", "frontend_panels")
    try:
        panels = hass.data.get(key, {})
    except (AttributeError, TypeError):
        return {}
    return panels if isinstance(panels, dict) else {}


def _notify_dashboard_updated(dashboard: LovelaceConfig) -> None:
    """Refresh the dashboard without relying on the private method name."""
    notify = getattr(dashboard, "_config_updated", None)
    if callable(notify):
        notify()


def _md(value: str) -> str:
    """Escape markdown link syntax in server-controlled labels.

    Headings and gauge names are safe, but warning lines and reset lists are
    markdown: without this, a display name like ``[Click](https://evil)``
    would render as a real link. Only brackets and parens are escaped, so
    plain labels (including ``#``) render unchanged.
    """
    return (
        value.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


FORMAT_TOKENS = """{% macro tokens(value) -%}
{% if not is_number(value) %}Unavailable
{% elif value | float >= 1000000000 %}{{ '%.1f' | format(value | float / 1000000000) }}B tokens
{% elif value | float >= 1000000 %}{{ '%.1f' | format(value | float / 1000000) }}M tokens
{% elif value | float >= 1000 %}{{ '%.1f' | format(value | float / 1000) }}K tokens
{% else %}{{ value | int }} tokens{% endif %}
{%- endmacro %}"""


def summary_card(period: str, entities: dict[str, str]) -> dict:
    """Use templates for readable numbers, keeping raw sensor units unchanged."""
    cost = entities.get(f"usage/all/{period}/cost", UNAVAILABLE_ENTITY)
    tokens = entities.get(f"usage/all/{period}/tokens", UNAVAILABLE_ENTITY)
    title = "Today" if period == "today" else "This month"
    return {
        "type": "markdown",
        "content": FORMAT_TOKENS
        + "\n"
        + f"## {title}\n"
        + "{% set cost = states('"
        + cost
        + "') %}\n"
        + "# {% if has_value('"
        + cost
        + "') and is_number(states('"
        + cost
        + "')) %}${{ '%.2f' | format(states('"
        + cost
        + "') | float) }}"
        + "{% else %}Unavailable{% endif %}\n"
        + "{{ tokens(states('"
        + tokens
        + "')) }}\n\nEstimated API cost",
        "entity_id": [cost, tokens],
    }


def _waiting_view() -> dict:
    """Show while Lovelace asks before the first refresh or during unload."""
    return {
        "title": NAME,
        "views": [
            {
                "title": "Overview",
                "path": "overview",
                "icon": "mdi:chart-box-outline",
                "type": "sections",
                "max_columns": 2,
                "sections": [
                    {
                        "type": "grid",
                        "cards": [
                            {
                                "type": "markdown",
                                "content": "Waiting for current usage readings.",
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _group_card(label: str, group: list, entities: dict[str, str]) -> dict:
    """Build one account's gauges plus its reset countdown lines."""
    provider_cards = []
    resets = []
    for key, reading in group:
        entity_id = entities[key]
        window = _md(str(reading.attributes.get("window_label", "?")))
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
            "{% set stamp = as_datetime(state_attr('" + entity_id + "', 'resets_at'), none) %}\n"
            "{{ state_attr('" + entity_id + "', 'window_label') }}: "
            "{% if states('" + entity_id + "') in ['unknown', 'unavailable'] %}Unavailable"
            "{% elif stamp and stamp > now() %}resets in {{ time_until(stamp, 1) }}"
            "{% elif stamp %}waiting for a new window"
            "{% else %}reset time not reported{% endif %}"
        )
    return {
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


def _registry_entities(hass: HomeAssistant, entry) -> dict[str, str]:
    """Map reading keys to entity IDs, including user-renamed entities."""
    registry = er.async_get(hass)
    prefix = f"{entry.unique_id}/"
    return {
        item.unique_id.removeprefix(prefix): item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.platform == DOMAIN and item.unique_id.startswith(prefix) and not item.disabled
    }


def _warning_card(entities: dict[str, str]) -> dict:
    """Explain failed, expired, and unsupported accounts with templates."""
    # Templates keep a failed refresh from leaving a good-looking cached
    # total on the page without an explanation.
    status = entities.get("usage_status", UNAVAILABLE_ENTITY)
    limits_status = entities.get("limits_status", UNAVAILABLE_ENTITY)
    cost = entities.get("usage/all/month/cost", UNAVAILABLE_ENTITY)
    return {
        "type": "markdown",
        "content": "{% if states('" + status + "') == 'partial' %}"
        "Some usage history is missing. Totals may be incomplete.\n\n{% endif %}"
        "{% if states('" + status + "') == 'failed' %}"
        "Usage refresh failed. Totals are unavailable.\n\n{% endif %}"
        "{% if (state_attr('" + cost + "', 'unpriced_records') or 0) > 0 %}"
        "Some records have no price. Cost estimates are incomplete.\n\n{% endif %}"
        "{% if states('" + limits_status + "') == 'failed' %}"
        "Quota refresh failed. Previous limits are unavailable.\n\n{% endif %}"
        "{% for account in state_attr('" + limits_status + "', 'accounts') or [] %}"
        "{% set account_label = account.label | replace('[', '(') | replace(']', ')') %}"
        "{% if account.reason == 'invalid_response' %}"
        "{{ account_label }}: quota response was invalid.\n\n"
        "{% elif account.status == 'failed' %}"
        "{{ account_label }}: quota probe failed.\n\n"
        "{% elif account.status == 'stale' %}"
        "{{ account_label }}: quota reading expired.\n\n"
        "{% elif account.status == 'unsupported' %}"
        "{{ account_label }}: limits are unsupported for this account.\n\n"
        "{% endif %}{% endfor %}"
        "{% if states('" + status + "') in ['unknown', 'unavailable'] %}"
        "Waiting for current usage readings.\n\n{% endif %}"
        "Readings refresh every five minutes by default.",
        "entity_id": [status, limits_status, cost],
    }


def _assemble_sections(cards: list) -> list:
    """Keep the overview across the top and accounts in their own sections."""
    # Plain masonry would distribute the warning and totals among the gauges.
    sections = [
        {
            "type": "grid",
            "column_span": 2,
            "cards": [{**card, "grid_options": {"columns": "full"}} for card in cards[:2]],
        }
    ]
    for card in cards[2:]:
        group_cards: Sequence[Any] = card["cards"] if card["type"] == "vertical-stack" else [card]
        sections.append(
            {
                "type": "grid",
                "cards": [{**item, "grid_options": {"columns": "full"}} for item in group_cards],
            }
        )
    return sections


def build_dashboard(hass: HomeAssistant, entry) -> dict:
    """Resolve actual entity IDs, including those renamed by the user."""
    entities = _registry_entities(hass, entry)
    cards = [
        {
            "type": "horizontal-stack",
            "cards": [
                summary_card("today", entities),
                summary_card("month", entities),
            ],
        },
        _warning_card(entities),
    ]
    readings = getattr(getattr(entry, "runtime_data", None), "data", None)
    if readings is None:
        # Lovelace can ask for the page while the entry unloads or before
        # the first refresh; show a waiting view instead of failing.
        return _waiting_view()
    groups: dict[str, list] = defaultdict(list)
    for key, reading in readings.readings.items():
        if key.startswith("limit/") and key.endswith("/remaining") and key in entities:
            instance_id = reading.attributes.get("instance_id")
            if instance_id is None:
                continue
            groups[instance_id].append((key, reading))
    for group in groups.values():
        label = group[0][1].attributes.get("provider_label", "?")
        cards.append(_group_card(label, group, entities))
    if not groups:
        cards.append({"type": "markdown", "content": "No subscription limits reported yet."})
    return {
        "title": NAME,
        "views": [
            {
                "title": "Overview",
                "path": "overview",
                "icon": "mdi:chart-box-outline",
                "type": "sections",
                "max_columns": 2,
                "sections": _assemble_sections(cards),
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
        return json_fragment(json_bytes(await self.async_load(force)))

    async def async_get_info(self):
        return {"mode": self.mode, "views": 1}


@callback
def async_register_dashboard(hass: HomeAssistant, entry) -> None:
    """Register only our own route; never replace a user's dashboard."""
    if not entry.options.get(CONF_SHOW_DASHBOARD, True):
        return
    dashboards = _lovelace_dashboards(hass)
    if dashboards is None:
        LOGGER.warning("T3 dashboard is unavailable because Lovelace is not loaded")
        return
    path = f"t3-code-{entry.entry_id}"
    if path in dashboards or path in _frontend_panels(hass):
        LOGGER.warning("T3 dashboard URL is already in use: %s", path)
        return
    try:
        dashboard = T3Dashboard(hass, path, entry)
    except (AttributeError, TypeError, ValueError):
        LOGGER.warning("T3 dashboard is unavailable because Lovelace is not loaded")
        return
    try:
        frontend.async_register_built_in_panel(
            hass,
            "lovelace",
            sidebar_title=f"{NAME} · {entry.title}",
            sidebar_icon="mdi:chart-box-outline",
            frontend_url_path=path,
            config={"mode": "yaml"},
        )
    except (AttributeError, TypeError, ValueError):
        LOGGER.warning("T3 dashboard is unavailable because Lovelace is not loaded")
        return
    try:
        dashboards[path] = dashboard
    except (AttributeError, TypeError):
        try:
            frontend.async_remove_panel(hass, path)
        except (AttributeError, TypeError, ValueError):
            pass
        LOGGER.warning("T3 dashboard is unavailable because Lovelace is not loaded")
        return

    @callback
    def cleanup():
        if dashboards.get(path) is dashboard:
            dashboards.pop(path)
            try:
                frontend.async_remove_panel(hass, path)
            except (AttributeError, TypeError, ValueError):
                pass

    entry.async_on_unload(cleanup)

    @callback
    def _refresh() -> None:
        _notify_dashboard_updated(dashboard)

    entry.async_on_unload(entry.runtime_data.async_add_listener(_refresh))

    @callback
    def registry_changed(event):
        # Renames and newly discovered windows must refresh the layout as soon
        # as the registry knows their IDs, rather than waiting for the next poll.
        entity = er.async_get(hass).async_get(event.data["entity_id"])
        if entity and entity.config_entry_id == entry.entry_id:
            _notify_dashboard_updated(dashboard)

    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_changed))
