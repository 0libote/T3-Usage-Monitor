"""T3 Usage Monitor integration for Home Assistant."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import T3Client
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
from .coordinator import T3Coordinator
from .dashboard import async_register_dashboard
from .presentation import async_prepare_registry

type T3ConfigEntry = ConfigEntry[T3Coordinator]
PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: T3ConfigEntry) -> bool:
    """Connect, validate, and forward sensors."""
    client = T3Client(async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN])
    coordinator = T3Coordinator(
        hass, entry, client, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    async_prepare_registry(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_dashboard(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: T3ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: T3ConfigEntry) -> bool:
    """Unload entities and their coordinator listeners."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
