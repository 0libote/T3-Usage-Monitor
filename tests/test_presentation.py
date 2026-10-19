"""Compact defaults and a non-destructive upgrade for existing installations."""

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from custom_components.t3_code.const import DOMAIN

from .test_integration import create_entry


async def test_compact_sensor_defaults(hass, t3_server):
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    primary = [entity for entity in entities if entity.entity_category is None]
    assert {entity.unique_id for entity in primary} == {
        "test-server/usage/all/today/tokens",
        "test-server/usage/all/today/cost",
        "test-server/usage/all/month/tokens",
        "test-server/usage/all/month/cost",
        "test-server/limit/codex/primary/remaining",
    }
    assert all(entity.disabled_by is None for entity in primary)
    for key in (
        "usage/codex/today/cost",
        "usage/all/today/cached_tokens",
        "limit/codex/primary/used",
        "limit/codex/primary/reset",
        "limit/codex/credits",
    ):
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"test-server/{key}")
        entity = registry.async_get(entity_id)
        assert entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        assert entity.entity_category is EntityCategory.DIAGNOSTIC
        assert hass.states.get(entity_id) is None
    token_id = registry.async_get_entity_id("sensor", DOMAIN, "test-server/usage/all/today/tokens")
    assert hass.states.get(token_id).state == "370"
    assert hass.states.get(token_id).attributes["cache_read_tokens"] == 200
    cost_id = registry.async_get_entity_id("sensor", DOMAIN, "test-server/usage/all/today/cost")
    assert registry.async_get(cost_id).options["sensor"]["suggested_display_precision"] == 2
    remaining_id = registry.async_get_entity_id(
        "sensor", DOMAIN, "test-server/limit/codex/primary/remaining"
    )
    assert registry.async_get(remaining_id).options["sensor"]["suggested_display_precision"] == 0
    assert hass.states.get(remaining_id).attributes["resets_at"] == "2099-01-01T00:00:00Z"


async def test_upgrade_preserves_enabled_details_and_user_settings(hass, t3_server):
    entry = create_entry(hass, t3_server)
    registry = er.async_get(hass)
    primary = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/usage/all/today/tokens",
        config_entry=entry,
        suggested_object_id="development_today_tokens",
        original_name="Today tokens",
    )
    legacy = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/usage/all/today/cached_tokens",
        config_entry=entry,
        suggested_object_id="my_existing_cache_tokens",
    )
    registry.async_update_entity(legacy.entity_id, name="My cache counter")
    registry.async_update_entity_options(legacy.entity_id, "sensor", {"display_precision": 6})
    user_disabled = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/usage/codex/today/cost",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    user_hidden = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "test-server/limit/codex/primary/used",
        config_entry=entry,
        hidden_by=er.RegistryEntryHider.USER,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    upgraded = registry.async_get(legacy.entity_id)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, "test-server/usage/all/today/tokens")
        == primary.entity_id
    )
    assert hass.states.get(primary.entity_id).state == "370"
    assert upgraded.disabled_by is None
    assert upgraded.hidden_by is er.RegistryEntryHider.INTEGRATION
    assert upgraded.name == "My cache counter"
    assert upgraded.options["sensor"]["display_precision"] == 6
    assert hass.states.get(legacy.entity_id).state == "200"
    assert upgraded.entity_category is EntityCategory.DIAGNOSTIC
    assert registry.async_get(user_disabled.entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert registry.async_get(user_hidden.entity_id).hidden_by is er.RegistryEntryHider.USER
    # Once upgraded, a user's choice to unhide it survives subsequent reloads.
    registry.async_update_entity(legacy.entity_id, hidden_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(legacy.entity_id).hidden_by is None


async def test_optional_detail_can_be_enabled(hass, t3_server):
    entry = create_entry(hass, t3_server)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, "test-server/usage/codex/today/cost")
    registry.async_update_entity(entity_id, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "0.01"
