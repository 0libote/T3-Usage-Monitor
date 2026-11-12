"""Sensor descriptions resolve to the same metadata as the old if/elif."""

import json
from pathlib import Path

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.t3_code.const import DOMAIN
from custom_components.t3_code.models import Reading
from custom_components.t3_code.sensor import SENSOR_DESCRIPTIONS, T3Sensor

EXPECTED = {
    "money": {
        "icon": "mdi:currency-usd",
        "device_class": SensorDeviceClass.MONETARY,
        "unit": "USD",
        "precision": 2,
        "state_class": SensorStateClass.TOTAL,
    },
    "tokens": {
        "icon": "mdi:counter",
        "device_class": None,
        "unit": "tokens",
        "precision": None,
        "state_class": SensorStateClass.TOTAL,
    },
    "percent": {
        "icon": "mdi:gauge",
        "device_class": None,
        "unit": "%",
        "precision": 0,
        "state_class": SensorStateClass.MEASUREMENT,
    },
    "timestamp": {
        "icon": "mdi:code-braces",
        "device_class": SensorDeviceClass.TIMESTAMP,
        "unit": None,
        "precision": None,
        "state_class": None,
    },
    "count": {
        "icon": "mdi:code-braces",
        "device_class": None,
        "unit": None,
        "precision": None,
        "state_class": None,
    },
    "status": {
        "icon": "mdi:code-braces",
        "device_class": None,
        "unit": None,
        "precision": None,
        "state_class": None,
    },
}


class _StubClient:
    url = "http://example/"


class _StubData:
    def __init__(self, readings, environment):
        self.readings = readings
        self.environment = environment


class _StubCoordinator:
    def __init__(self, readings, environment):
        self.data = _StubData(readings, environment)
        self.client = _StubClient()


def _environment():
    return {
        "environmentId": "test-server",
        "label": "Development",
        "serverVersion": "0.1.0",
    }


def _make_sensor(key, name, kind):
    readings = {key: Reading(name, 1, kind)}
    coordinator = _StubCoordinator(readings, _environment())
    return T3Sensor(coordinator, key)


def test_descriptions_cover_every_kind():
    assert set(SENSOR_DESCRIPTIONS) == set(EXPECTED)
    for kind, description in SENSOR_DESCRIPTIONS.items():
        assert description.key == kind
        assert description.translation_key


def test_each_kind_resolves_to_legacy_metadata():
    for kind, expected in EXPECTED.items():
        sensor = _make_sensor("usage/codex/today/detail", f"Dynamic {kind} label", kind)
        assert sensor._attr_name == f"Dynamic {kind} label"
        assert sensor._attr_icon == expected["icon"]
        assert getattr(sensor, "_attr_device_class", None) == expected["device_class"]
        assert getattr(sensor, "_attr_native_unit_of_measurement", None) == expected["unit"]
        assert getattr(sensor, "_attr_state_class", None) == expected["state_class"]
        assert getattr(sensor, "_attr_suggested_display_precision", None) == expected["precision"]
        assert sensor.entity_description is SENSOR_DESCRIPTIONS[kind]
        # Non-primary detail entities stay diagnostic.
        assert sensor._attr_entity_category is EntityCategory.DIAGNOSTIC


def test_primary_keys_have_no_entity_category():
    for key in (
        "usage/all/today/tokens",
        "usage/all/today/cost",
        "limit/codex/primary/remaining",
    ):
        kind = "percent" if key.endswith("remaining") else ("money" if "cost" in key else "tokens")
        sensor = _make_sensor(key, f"Dynamic {kind} label", kind)
        assert getattr(sensor, "_attr_entity_category", None) is None


def test_dynamic_provider_window_labels_stay_as_is():
    cases = [
        ("usage/codex/today/cost", "Codex Estimated cost today", "money"),
        ("limit/codex/primary/remaining", "Codex 5 hour remaining", "percent"),
        ("limit/codex/primary/reset", "Codex 5 hour reset", "timestamp"),
        ("limit/codex/credits", "Codex reset credits", "count"),
    ]
    for key, name, kind in cases:
        assert _make_sensor(key, name, kind)._attr_name == name


def test_translation_keys_exist_in_strings():
    root = Path(__file__).resolve().parent.parent / "custom_components" / "t3_code"
    strings = json.loads((root / "strings.json").read_text())
    sensors = strings["entity"]["sensor"]
    for description in SENSOR_DESCRIPTIONS.values():
        assert description.translation_key in sensors
        assert sensors[description.translation_key]["name"]


def test_en_mirrors_strings_entity():
    root = Path(__file__).resolve().parent.parent / "custom_components" / "t3_code"
    strings = json.loads((root / "strings.json").read_text())
    english = json.loads((root / "translations" / "en.json").read_text())
    assert english["entity"] == strings["entity"]


async def test_integration_effective_metadata_unchanged(hass, t3_server):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-server",
        title="Development",
        data={"url": t3_server["url"], "token": "session-secret"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    checks = {
        "test-server/usage/all/today/tokens": {
            "unit": "tokens",
            "state_class": "total",
            "friendly": "Tokens today",
        },
        "test-server/usage/all/today/cost": {
            "unit": "USD",
            "state_class": "total",
            "friendly": "Estimated cost today",
        },
        "test-server/limit/codex/primary/remaining": {
            "unit": "%",
            "state_class": "measurement",
            "friendly": "remaining",
        },
    }
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    for unique_id, expected in checks.items():
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        state = hass.states.get(entity_id)
        assert state.attributes["unit_of_measurement"] == expected["unit"]
        assert state.attributes["state_class"] == expected["state_class"]
        assert expected["friendly"] in state.attributes["friendly_name"]
