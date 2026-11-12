"""Migration from setup-time presentation flag to config entry minor version."""

from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.t3_code import async_migrate_entry
from custom_components.t3_code.const import DOMAIN


def make_entry(hass, server, **kwargs):
    data = {CONF_URL: server["url"], CONF_TOKEN: "session-secret"}
    data.update(kwargs.pop("data_extra", {}))
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-server",
        title="Development",
        data=data,
        **kwargs,
    )
    entry.add_to_hass(hass)
    return entry


async def test_legacy_migration_hides_detail_once(hass, t3_server):
    entry = make_entry(hass, t3_server, minor_version=1)
    registry = er.async_get(hass)
    detail = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/usage/codex/today/cost",
        config_entry=entry,
    )
    primary = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/usage/all/today/tokens",
        config_entry=entry,
    )
    assert detail.hidden_by is None
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 2
    assert "presentation_version" not in entry.data
    assert registry.async_get(detail.entity_id).hidden_by is er.RegistryEntryHider.INTEGRATION
    assert registry.async_get(primary.entity_id).hidden_by is None
    # Second migration is a no-op: new details stay visible.
    fresh = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/usage/all/today/cached_tokens",
        config_entry=entry,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 2
    assert registry.async_get(fresh.entity_id).hidden_by is None
    assert registry.async_get(detail.entity_id).hidden_by is er.RegistryEntryHider.INTEGRATION


async def test_legacy_flag_treated_as_migrated(hass, t3_server):
    entry = make_entry(
        hass,
        t3_server,
        minor_version=1,
        data_extra={"presentation_version": 1},
    )
    registry = er.async_get(hass)
    detail = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/usage/codex/today/cost",
        config_entry=entry,
    )
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 2
    assert "presentation_version" not in entry.data
    assert registry.async_get(detail.entity_id).hidden_by is None


async def test_already_migrated_is_noop(hass, t3_server):
    entry = make_entry(hass, t3_server, minor_version=2)
    registry = er.async_get(hass)
    detail = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/usage/codex/today/cost",
        config_entry=entry,
    )
    data_before = dict(entry.data)
    assert await async_migrate_entry(hass, entry) is True
    assert entry.minor_version == 2
    assert dict(entry.data) == data_before
    assert registry.async_get(detail.entity_id).hidden_by is None


async def test_manager_setup_migrates_then_loads_and_unloads(hass, t3_server):
    """A minor-1 entry migrates through the manager before setup runs."""
    entry = make_entry(hass, t3_server, minor_version=1)
    registry = er.async_get(hass)
    detail = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/usage/codex/today/cost",
        config_entry=entry,
    )
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    assert entry.minor_version == 2
    assert registry.async_get(detail.entity_id).hidden_by is er.RegistryEntryHider.INTEGRATION
    assert entry.runtime_data is not None
    assert await hass.config_entries.async_unload(entry.entry_id) is True
