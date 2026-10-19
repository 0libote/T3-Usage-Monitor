"""Keep everyday readings separate from optional accounting details."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


def is_primary(key: str) -> bool:
    """Only totals and remaining quota belong in the main sensor list."""
    return key in {
        "usage/all/today/tokens",
        "usage/all/today/cost",
        "usage/all/month/tokens",
        "usage/all/month/cost",
    } or (key.startswith("limit/") and key.endswith("/remaining"))


def is_diagnostic(key: str) -> bool:
    return key in {"usage_status", "limits_status", "last_read"}


def async_prepare_registry(hass: HomeAssistant, entry) -> None:
    """Hide legacy detail entities once without disabling or renaming them.

    Existing entities keep their states, statistics, custom names, and user
    settings. Newly discovered details are disabled by default by the platform.
    """
    if entry.data.get("presentation_version", 0) >= 1:
        return
    registry = er.async_get(hass)
    prefix = f"{entry.unique_id}/"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN or not entity.unique_id.startswith(prefix):
            continue
        key = entity.unique_id.removeprefix(prefix)
        if not is_primary(key) and not is_diagnostic(key) and entity.hidden_by is None:
            registry.async_update_entity(
                entity.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
            )
    hass.config_entries.async_update_entry(entry, data={**entry.data, "presentation_version": 1})
