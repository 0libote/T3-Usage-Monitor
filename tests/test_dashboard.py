"""Exercise the generated dashboard and render its templates in Home Assistant."""

import pytest
from homeassistant.components import frontend
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import template

from custom_components.t3_code.const import CONF_SHOW_DASHBOARD, DOMAIN, NAME
from custom_components.t3_code.dashboard import build_dashboard, summary_card

from .test_integration import create_entry


def markdown_cards(config):
    if isinstance(config, dict):
        if config.get("type") == "markdown":
            yield config
        for value in config.values():
            yield from markdown_cards(value)
    elif isinstance(config, list):
        for item in config:
            yield from markdown_cards(item)


async def test_dashboard_lifecycle_and_templates(hass, t3_server):
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    path = f"t3-code-{entry.entry_id}"
    assert path in hass.data[frontend.DATA_PANELS]
    dashboard = hass.data[LOVELACE_DATA].dashboards[path]
    config = await dashboard.async_load(False)
    assert config["title"] == NAME
    assert dashboard.config["title"] == f"{NAME} · Development"
    assert await dashboard.async_get_info() == {"mode": "yaml", "views": 1}
    assert await dashboard.async_json(False) is not None
    rendered = [
        template.Template(card["content"], hass).async_render(parse_result=False)
        for card in markdown_cards(config)
    ]
    assert "$0.01" in rendered[0]
    assert "370 tokens" in rendered[0]
    assert "5 hour: resets in" in rendered[-1]
    assert "Unavailable" not in rendered[-1]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert path not in hass.data[frontend.DATA_PANELS]
    assert path not in hass.data[LOVELACE_DATA].dashboards


@pytest.mark.parametrize(
    ("value", "formatted"),
    [
        (54842006, "54.8M tokens"),
        (574361642, "574.4M tokens"),
        (1200, "1.2K tokens"),
        (1500000000, "1.5B tokens"),
        (0, "0 tokens"),
        ("unavailable", "Unavailable"),
    ],
)
async def test_readable_numbers_keep_raw_units(hass, value, formatted):
    hass.states.async_set("sensor.cost", 10.9569)
    hass.states.async_set("sensor.tokens", value)
    card = summary_card(
        "today", {"usage/all/today/cost": "sensor.cost", "usage/all/today/tokens": "sensor.tokens"}
    )
    rendered = template.Template(card["content"], hass).async_render(parse_result=False)
    assert "$10.96" in rendered
    assert formatted in rendered
    assert hass.states.get("sensor.tokens").state == str(value)


async def test_unavailable_is_not_zero(hass):
    card = summary_card("today", {})
    rendered = template.Template(card["content"], hass).async_render(parse_result=False)
    assert "Unavailable" in rendered
    assert "$0.00" not in rendered


async def test_dashboard_uses_renamed_entity_ids(hass, t3_server):
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, "test-server/usage/all/today/cost")
    registry.async_update_entity(entity_id, new_entity_id="sensor.my_t3_cost")
    await hass.async_block_till_done()
    config = build_dashboard(hass, entry)
    assert "sensor.my_t3_cost" in next(markdown_cards(config))["entity_id"]


async def test_new_window_updates_dashboard_after_entity_creation(hass, t3_server):
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    updates = []

    @callback
    def updated(event):
        updates.append(event.data["url_path"])

    remove_listener = hass.bus.async_listen("lovelace_updated", updated)
    t3_server["providers"][0]["usageLimits"]["windows"].append(
        {
            "id": "weekly",
            "kind": "weekly",
            "label": "Weekly",
            "usedPercent": 39,
        }
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    dashboard = hass.data[LOVELACE_DATA].dashboards[f"t3-code-{entry.entry_id}"]
    config = await dashboard.async_load(False)
    provider = config["views"][0]["sections"][1]
    assert [card["name"] for card in provider["cards"][1]["cards"]] == ["5 hour", "Weekly"]
    assert f"t3-code-{entry.entry_id}" in updates
    remove_listener()


async def test_no_quota_windows_gives_an_explanation(hass, t3_server):
    t3_server["providers"] = []
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    config = build_dashboard(hass, entry)
    assert list(markdown_cards(config))[-1]["content"] == "No subscription limits reported yet."


async def test_dashboard_can_be_turned_off(hass, t3_server):
    entry = create_entry(hass, t3_server)
    hass.config_entries.async_update_entry(entry, options={CONF_SHOW_DASHBOARD: False})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert f"t3-code-{entry.entry_id}" not in hass.data.get(frontend.DATA_PANELS, {})


async def test_partial_and_failed_data_visible(hass, t3_server):
    t3_server["summary"]["sources"][0]["status"] = "partial"
    t3_server["refresh_failure"] = True
    t3_server["summary"]["buckets"][0]["unpricedRecords"] = 2
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    config = build_dashboard(hass, entry)
    rendered = [
        template.Template(card["content"], hass).async_render(parse_result=False)
        for card in markdown_cards(config)
    ]
    assert "Some usage history is missing" in rendered[2]
    assert "Some records have no price" in rendered[2]
    assert "Quota refresh failed" in rendered[2]
    assert "Unavailable" in rendered[-1]


@pytest.mark.parametrize(
    ("reason", "message"),
    [("probeFailed", "quota probe failed"), ("unsupported", "limits are unsupported")],
)
async def test_account_failure_explanation_without_gauges(hass, t3_server, reason, message):
    limits = t3_server["providers"][0]["usageLimits"]
    limits["windows"] = []
    limits["unavailable"] = {"reason": reason}
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    config = build_dashboard(hass, entry)
    warning = list(markdown_cards(config))[2]
    rendered = template.Template(warning["content"], hass).async_render(parse_result=False)
    assert "Codex" in rendered
    assert message in rendered


async def test_malformed_account_has_an_explanation(hass, t3_server):
    t3_server["providers"][0]["usageLimits"]["windows"][0]["usedPercent"] = 101
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    warning = list(markdown_cards(build_dashboard(hass, entry)))[2]
    rendered = template.Template(warning["content"], hass).async_render(parse_result=False)
    assert "Codex: quota response was invalid." in rendered
