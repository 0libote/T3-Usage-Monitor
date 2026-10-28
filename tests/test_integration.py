"""Exercise actual Home Assistant setup, entities, reauth, and unload."""

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.t3_code.const import (
    CONF_CREDENTIAL,
    CONF_CREDENTIAL_TYPE,
    CONF_SCAN_INTERVAL,
    CONF_SHOW_DASHBOARD,
    DOMAIN,
)


def create_entry(hass, server, token="session-secret"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-server",
        title="Development",
        data={CONF_URL: server["url"], CONF_TOKEN: token},
    )
    entry.add_to_hass(hass)
    return entry


def credentials(server, credential_type="pairing", credential="pair-secret"):
    return {
        CONF_URL: server["url"],
        CONF_CREDENTIAL_TYPE: credential_type,
        CONF_CREDENTIAL: credential,
    }


async def test_setup_entities_dynamic_limits_and_unload(hass, t3_server):
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    token_id = registry.async_get_entity_id("sensor", DOMAIN, "test-server/usage/all/today/tokens")
    cost_id = registry.async_get_entity_id("sensor", DOMAIN, "test-server/usage/all/today/cost")
    assert hass.states.get(token_id).state == "370"
    assert hass.states.get(token_id).attributes["state_class"] == "total"
    assert hass.states.get(cost_id).attributes["unit_of_measurement"] == "USD"
    t3_server["providers"][0]["usageLimits"]["windows"].append(
        {
            "id": "weekly",
            "kind": "weekly",
            "label": "Weekly",
            "usedPercent": 60,
        }
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    new_id = registry.async_get_entity_id(
        "sensor", DOMAIN, "test-server/limit/codex/weekly/remaining"
    )
    assert hass.states.get(new_id).state == "40"
    t3_server["providers"] = []
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(new_id).state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_pairing_flow_stores_only_session(hass, t3_server):
    with patch("custom_components.t3_code.async_setup_entry", return_value=True):
        form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            user_input={
                CONF_URL: t3_server["url"] + "/pair#token=pair-secret",
                CONF_CREDENTIAL_TYPE: "pairing",
            },
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: t3_server["url"], CONF_TOKEN: "session-secret"}
    assert result["result"].unique_id == "test-server"


async def test_duplicate_does_not_consume_pair_token(hass, t3_server):
    create_entry(hass, t3_server)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=credentials(t3_server)
    )
    assert result["reason"] == "already_configured"
    assert t3_server["pair_count"] == 0


async def test_probe_retry_does_not_reuse_single_use_token(hass, t3_server):
    t3_server["summary_failure"] = True
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=credentials(t3_server)
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert t3_server["pair_count"] == 1
    t3_server["summary_failure"] = False
    with patch("custom_components.t3_code.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], credentials(t3_server)
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert t3_server["pair_count"] == 1


async def test_probe_retry_rejects_changed_server_before_reusing_session(hass, t3_server):
    t3_server["summary_failure"] = True
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=credentials(t3_server)
    )
    assert result["errors"] == {"base": "cannot_connect"}
    calls = len(t3_server["calls"])
    t3_server["descriptor"]["environmentId"] = "different-server"
    t3_server["summary_failure"] = False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], credentials(t3_server)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_server"
    assert len(t3_server["calls"]) == calls
    assert t3_server["pair_count"] == 1


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({CONF_URL: "bad", CONF_CREDENTIAL_TYPE: "pairing"}, "invalid_connection"),
        ({CONF_CREDENTIAL_TYPE: "bearer", CONF_CREDENTIAL: "wrong"}, "invalid_auth"),
    ],
)
async def test_setup_errors(hass, t3_server, data, error):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data={CONF_URL: t3_server["url"], **data}
    )
    assert result["errors"] == {"base": error}


async def test_expired_token_triggers_reauth(hass, t3_server):
    entry = create_entry(hass, t3_server, "expired")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH
    with patch("custom_components.t3_code.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            flows[0]["flow_id"], credentials(t3_server, "bearer", "session-secret")
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_TOKEN] == "session-secret"


async def test_wrong_identity_rejected_in_reconfigure(hass, t3_server):
    entry = create_entry(hass, t3_server)
    t3_server["descriptor"]["environmentId"] = "another-server"
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
        data=credentials(t3_server),
    )
    assert result["reason"] == "wrong_server"
    assert t3_server["pair_count"] == 0


async def test_options(hass, t3_server):
    entry = create_entry(hass, t3_server)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 600}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 600, CONF_SHOW_DASHBOARD: True}


async def test_pairing_rejects_null_usage_response(hass, t3_server):
    t3_server["summary"] = None
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}, data=credentials(t3_server)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unsupported_server"}
    assert t3_server["pair_count"] == 1
