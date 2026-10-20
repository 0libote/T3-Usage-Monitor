"""Quota expiry must update HA states between network polls and stop on unload."""

from datetime import timedelta

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.t3_code.const import CONF_SCAN_INTERVAL, DOMAIN, MAX_QUOTA_AGE
from custom_components.t3_code.models import normalize

from .test_integration import create_entry


@pytest.mark.parametrize("age", [MAX_QUOTA_AGE, MAX_QUOTA_AGE + timedelta(days=30)])
def test_checked_at_expires_without_reset(descriptor, summary, providers, now, age):
    limits = providers[0]["usageLimits"]
    limits["checkedAt"] = (now - age).isoformat()
    limits["windows"][0].pop("resetsAt")
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    assert readings["limit/codex/primary/remaining"].value is None
    assert readings["limit/codex/credits"].value is None
    assert readings["limits_status"].value == "stale"


@pytest.mark.parametrize("stamp", [None, "invalid", "2027-01-01T00:00:00Z"])
def test_untrustworthy_check_time_is_unavailable(descriptor, summary, providers, now, stamp):
    providers[0]["usageLimits"]["checkedAt"] = stamp
    readings = normalize(descriptor, summary, providers, now.date(), "UTC", now).readings
    assert readings["limit/codex/primary/remaining"].value is None
    assert readings["limits_status"].value == "stale"


@pytest.mark.parametrize("expiry", ["reset", "age"])
async def test_expiry_updates_states_without_polling(hass, t3_server, freezer, now, expiry):
    limits = t3_server["providers"][0]["usageLimits"]
    if expiry == "reset":
        limits["windows"][0]["resetsAt"] = (now + timedelta(minutes=1)).isoformat()
    else:
        limits["checkedAt"] = (now - MAX_QUOTA_AGE + timedelta(minutes=1)).isoformat()
        limits["windows"][0].pop("resetsAt")
    entry = create_entry(hass, t3_server)
    hass.config_entries.async_update_entry(entry, options={CONF_SCAN_INTERVAL: 3600})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, "test-server/limit/codex/primary/remaining"
    )
    assert hass.states.get(entity_id).state == "75"
    calls = len(t3_server["calls"])
    poll_cancel = entry.runtime_data._unsub_refresh
    async_fire_time_changed(hass, now + timedelta(minutes=2))
    freezer.move_to(now + timedelta(minutes=2))
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    assert entry.runtime_data.data.readings["limit/codex/primary/remaining"].attributes["stale"]
    assert len(t3_server["calls"]) == calls
    assert entry.runtime_data.last_update_success
    assert entry.runtime_data._unsub_refresh is poll_cancel
    assert entry.runtime_data.data.readings["limits_status"].value == "stale"
    # The original poll remains scheduled; a fresh server window recovers.
    limits["checkedAt"] = (now + timedelta(minutes=3)).isoformat()
    limits["windows"][0]["resetsAt"] = (now + timedelta(hours=3)).isoformat()
    freezer.move_to(now + timedelta(minutes=3))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(t3_server["calls"]) > calls
    assert hass.states.get(entity_id).state == "75"
    coordinator = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert coordinator._cancel_expiry is None


async def test_expiry_does_not_clear_connection_failure(hass, t3_server, freezer, now):
    t3_server["providers"][0]["usageLimits"]["windows"][0]["resetsAt"] = (
        now + timedelta(minutes=1)
    ).isoformat()
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    t3_server["auth_failure"] = True
    await entry.runtime_data.async_refresh()
    assert not entry.runtime_data.last_update_success
    async_fire_time_changed(hass, now + timedelta(minutes=2))
    freezer.move_to(now + timedelta(minutes=2))
    await hass.async_block_till_done()
    assert not entry.runtime_data.last_update_success
