"""Usage, cost, quota, and reset sensors."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import T3ConfigEntry
from .const import DOMAIN
from .coordinator import T3Coordinator
from .presentation import is_diagnostic, is_primary

# T3 may revise estimates downward. TOTAL + a calendar last_reset handles
# that without inventing consumption on a counter decrease.
SENSOR_DESCRIPTIONS: dict[str, SensorEntityDescription] = {
    "money": SensorEntityDescription(
        key="money",
        translation_key="cost",
        icon="mdi:currency-usd",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="USD",
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL,
    ),
    "tokens": SensorEntityDescription(
        key="tokens",
        translation_key="tokens",
        icon="mdi:counter",
        native_unit_of_measurement="tokens",
        state_class=SensorStateClass.TOTAL,
    ),
    "percent": SensorEntityDescription(
        key="percent",
        translation_key="remaining",
        icon="mdi:gauge",
        native_unit_of_measurement="%",
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "timestamp": SensorEntityDescription(
        key="timestamp",
        translation_key="reset",
        icon="mdi:code-braces",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    "count": SensorEntityDescription(
        key="count",
        translation_key="reset_credits",
        icon="mdi:code-braces",
    ),
    "status": SensorEntityDescription(
        key="status",
        translation_key="status",
        icon="mdi:code-braces",
    ),
}

PARALLEL_UPDATES = 0


async def async_setup_entry(
    _: HomeAssistant, entry: T3ConfigEntry, async_add_entities: AddEntitiesCallback
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
        self._attr_entity_registry_enabled_default = is_primary(key) or is_diagnostic(key)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, environment["environmentId"])},
            name=environment.get("label", "T3 Code"),
            manufacturer="T3 Code",
            model="Server",
            sw_version=environment.get("serverVersion"),
            configuration_url=coordinator.client.url,
        )
        description = SENSOR_DESCRIPTIONS.get(reading.kind)
        if description is None:
            description = SensorEntityDescription(key=reading.kind, icon="mdi:code-braces")
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_icon = description.icon
        if description.device_class is not None:
            self._attr_device_class = description.device_class
        if description.native_unit_of_measurement is not None:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        if description.state_class is not None:
            self._attr_state_class = description.state_class
        if description.suggested_display_precision is not None:
            self._attr_suggested_display_precision = description.suggested_display_precision
        if not is_primary(key):
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
