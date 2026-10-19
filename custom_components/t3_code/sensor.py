"""Usage, cost, quota, and reset sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import T3ConfigEntry
from .const import DOMAIN
from .coordinator import T3Coordinator

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: T3ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add new provider/window entities when they appear after setup."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_missing() -> None:
        new = set(coordinator.data.readings) - known
        known.update(new)
        async_add_entities(T3Sensor(coordinator, key) for key in sorted(new))

    add_missing()
    entry.async_on_unload(coordinator.async_add_listener(add_missing))


class T3Sensor(CoordinatorEntity[T3Coordinator], SensorEntity):
    """A single normalized metric belonging to a T3 server device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: T3Coordinator, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        reading = coordinator.data.readings[key]
        environment = coordinator.data.environment
        self._attr_unique_id = f"{environment['environmentId']}/{key}"
        self._attr_name = reading.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, environment["environmentId"])},
            name=environment.get("label", "T3 Code"),
            manufacturer="T3 Code",
            model="Server",
            sw_version=environment.get("serverVersion"),
            configuration_url=coordinator.client.url,
        )
        self._attr_icon = "mdi:code-braces"
        if reading.kind in ("money", "tokens"):
            # T3 may revise estimates downward. TOTAL + a calendar last_reset
            # handles that without inventing consumption on a counter decrease.
            self._attr_state_class = SensorStateClass.TOTAL
            self._attr_native_unit_of_measurement = "USD" if reading.kind == "money" else "tokens"
            if reading.kind == "money":
                self._attr_device_class = SensorDeviceClass.MONETARY
                self._attr_suggested_display_precision = 4
        elif reading.kind == "percent":
            self._attr_native_unit_of_measurement = "%"
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 1
        elif reading.kind == "timestamp":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        if reading.kind == "status" or key == "last_read":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        reading = self.coordinator.data.readings.get(self.key)
        return super().available and reading is not None and reading.value is not None

    @property
    def native_value(self):
        reading = self.coordinator.data.readings.get(self.key)
        return reading.value if reading else None

    @property
    def last_reset(self):
        reading = self.coordinator.data.readings.get(self.key)
        return reading.last_reset if reading else None

    @property
    def extra_state_attributes(self):
        reading = self.coordinator.data.readings.get(self.key)
        return reading.attributes if reading else {}
