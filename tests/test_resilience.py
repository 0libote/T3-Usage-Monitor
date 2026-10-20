"""Independent data failures, network recovery, and supported protocol contracts."""

import json
from pathlib import Path

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.t3_code.const import DOMAIN

from .test_integration import create_entry


@pytest.mark.parametrize("protocol", [1, 2])
@pytest.mark.parametrize("contract", [4, 5, 6])
async def test_supported_contracts_over_websocket(hass, t3_server, protocol, contract):
    t3_server["descriptor"]["orchestrationProtocolVersion"] = protocol
    path = Path(__file__).parent / "fixtures" / f"usage_v{contract}.json"
    t3_server["summary"] = json.loads(path.read_text())
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    readings = entry.runtime_data.data.readings
    assert readings["usage/all/today/tokens"].value == 370
    assert readings["usage/all/today/cost"].value == 0.01
    assert readings["limit/codex/primary/remaining"].value == 75


@pytest.mark.parametrize("failure", ["rpc", "closed", "malformed", "unsupported_contract"])
async def test_usage_failure_keeps_quotas_and_recovers(hass, t3_server, failure):
    if failure == "rpc":
        t3_server["summary_failure"] = True
    elif failure == "closed":
        t3_server["closed_methods"].add("server.getUsageSummary")
    elif failure == "malformed":
        t3_server["summary"]["buckets"] = None
    else:
        t3_server["summary"]["contractVersion"] = 99
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.last_update_success
    assert coordinator.data.readings["usage_status"].value == "failed"
    assert coordinator.data.readings["usage/all/month/tokens"].value is None
    assert coordinator.data.readings["limit/codex/primary/remaining"].value == 75
    t3_server["summary_failure"] = False
    t3_server["closed_methods"].clear()
    t3_server["summary"] = json.loads(
        (Path(__file__).parent / "fixtures" / "usage_v6.json").read_text()
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data.readings["usage_status"].value == "ok"
    assert coordinator.data.readings["usage/all/month/tokens"].value == 370


@pytest.mark.parametrize("failure", ["rpc", "closed", "config", "malformed"])
async def test_quota_failure_keeps_usage_and_recovers(hass, t3_server, failure):
    if failure == "closed":
        t3_server["closed_methods"].add("server.refreshProviders")
    elif failure == "malformed":
        t3_server["providers"][0]["usageLimits"]["windows"][0]["usedPercent"] = 101
    else:
        t3_server["refresh_failure"] = True
        if failure == "config":
            t3_server["failed_methods"].add("server.getConfig")
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.data.readings["usage/all/month/tokens"].value == 370
    assert coordinator.data.readings["limits_status"].value == "failed"
    assert all(
        reading.value is None
        for key, reading in coordinator.data.readings.items()
        if key.startswith("limit/")
    )
    t3_server["refresh_failure"] = False
    t3_server["closed_methods"].clear()
    t3_server["failed_methods"].clear()
    t3_server["providers"][0]["usageLimits"]["windows"][0]["usedPercent"] = 25
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data.readings["limits_status"].value == "ok"
    assert coordinator.data.readings["limit/codex/primary/remaining"].value == 75


async def test_server_outage_and_recovery(hass, t3_server):
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, "test-server/usage/all/today/tokens")
    t3_server["descriptor_failure"] = True
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    t3_server["descriptor_failure"] = False
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "370"
