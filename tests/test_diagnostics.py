"""Diagnostics downloads expose health information without private server data."""

import json

from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.t3_code.diagnostics import async_get_config_entry_diagnostics

from .test_integration import create_entry


async def test_diagnostics_download_redacts_private_fields(hass, t3_server, hass_client):
    t3_server["descriptor"].update(
        environmentId="private-environment-id", label="private-server-name", secret="private-secret"
    )
    provider = t3_server["providers"][0]
    provider.update(instanceId="private-account-id", displayName="private@example.test")
    provider["usageLimits"]["unavailable"] = {
        "reason": "probeFailed",
        "message": "private-error-message",
    }
    t3_server["summary"]["sources"][0]["fingerprint"] = {"resolvedHomePath": "/private/home/path"}
    entry = create_entry(hass, t3_server)
    hass.config_entries.async_update_entry(entry, unique_id="private-environment-id")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    data = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert data["server"]["orchestration_protocol"] == 2
    assert data["usage"]["contract_version"] == 6
    assert data["usage"]["sources"] == [{"status": "ok", "refreshing": False}]
    assert data["limits"]["status"] == "failed"
    assert data["limits"]["accounts"][0]["reason"] == "probeFailed"
    serialized = json.dumps(data)
    for secret in ("private", "session-secret", "pair-secret", t3_server["url"], entry.entry_id):
        assert secret not in serialized


async def test_diagnostics_without_loaded_coordinator(hass, t3_server):
    entry = create_entry(hass, t3_server)
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["entry_state"] == "not_loaded"
    assert "server" not in data


async def test_diagnostics_does_not_export_exception_text(hass, t3_server):
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data.last_exception = ValueError("private-credential-in-exception")
    data = await async_get_config_entry_diagnostics(hass, entry)
    assert data["last_error_type"] == "ValueError"
    assert "private-credential" not in json.dumps(data, default=str)
