"""Offline smoke tests for the complete Home Assistant integration surface."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "custom_components" / "zentraly"


def _module(name: str, *, package: bool = False) -> types.ModuleType:
    module = types.ModuleType(name)
    if package:
        module.__path__ = []
    sys.modules[name] = module
    return module


def _load_module(name: str, filename: str):
    path = PACKAGE_PATH / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _install_dependency_stubs() -> None:
    """Install only the external surfaces imported while modules are loaded."""
    voluptuous = _module("voluptuous")

    @dataclass(frozen=True)
    class Marker:
        kind: str
        key: object

    class Schema:
        def __init__(self, value) -> None:
            self.value = value

    voluptuous.Schema = Schema
    voluptuous.Optional = lambda key: Marker("optional", key)
    voluptuous.Required = lambda key: Marker("required", key)
    voluptuous.All = lambda *validators: ("all", validators)
    voluptuous.Coerce = lambda value: ("coerce", value)
    voluptuous.Range = lambda **limits: ("range", tuple(limits.items()))
    voluptuous.Boolean = lambda: ("boolean",)
    voluptuous.In = lambda values: ("in", tuple(values))

    aiohttp = _module("aiohttp")
    aiohttp.ClientSession = object
    aiohttp.ClientError = type("ClientError", (Exception,), {})

    homeassistant = _module("homeassistant", package=True)
    components = _module("homeassistant.components", package=True)
    homeassistant.components = components

    config_entries = _module("homeassistant.config_entries")

    class ConfigEntry:
        def __init__(self, entry_id: str = "entry-1") -> None:
            self.entry_id = entry_id
            self.version = 5
            self.data = {}

    config_entries.ConfigEntry = ConfigEntry

    class ConfigFlow:
        def __init_subclass__(cls, **kwargs) -> None:
            pass

        async def async_set_unique_id(self, unique_id) -> None:
            self.unique_id = unique_id

        def _abort_if_unique_id_configured(self) -> None:
            pass

        def async_create_entry(self, *, title, data):
            return {"title": title, "data": data}

    config_entries.ConfigFlow = ConfigFlow
    homeassistant.config_entries = config_entries
    _module("homeassistant.data_entry_flow").FlowResult = dict

    const = _module("homeassistant.const")
    const.ATTR_DEVICE_ID = "device_id"
    const.ATTR_TEMPERATURE = "temperature"
    const.CONF_EMAIL = "email"
    const.CONF_PASSWORD = "password"
    const.PERCENTAGE = "%"
    const.Platform = types.SimpleNamespace(
        CLIMATE="climate",
        SENSOR="sensor",
        BINARY_SENSOR="binary_sensor",
        NUMBER="number",
        SELECT="select",
        LOCK="lock",
        BUTTON="button",
    )
    const.UnitOfTemperature = types.SimpleNamespace(CELSIUS="°C")
    const.UnitOfTime = types.SimpleNamespace(HOURS="h")

    core = _module("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})

    class ServiceCall:
        def __init__(self, data: dict | None = None) -> None:
            self.data = data or {}

    core.ServiceCall = ServiceCall

    exceptions = _module("homeassistant.exceptions")
    exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})

    helpers = _module("homeassistant.helpers", package=True)
    homeassistant.helpers = helpers

    aiohttp_client = _module("homeassistant.helpers.aiohttp_client")
    aiohttp_client.async_get_clientsession = lambda hass: object()
    helpers.aiohttp_client = aiohttp_client

    device_registry = _module("homeassistant.helpers.device_registry")
    device_registry.async_get = lambda hass: None
    helpers.device_registry = device_registry

    entity_registry = _module("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: None
    entity_registry.async_entries_for_config_entry = lambda registry, entry_id: []
    helpers.entity_registry = entity_registry

    entity = _module("homeassistant.helpers.entity")
    entity.EntityCategory = types.SimpleNamespace(DIAGNOSTIC="diagnostic")

    entity_platform = _module("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object

    update_coordinator = _module("homeassistant.helpers.update_coordinator")

    class CoordinatorEntity:
        def __init__(self, coordinator) -> None:
            self.coordinator = coordinator

        @property
        def available(self) -> bool:
            return bool(getattr(self.coordinator, "last_update_success", True))

    update_coordinator.CoordinatorEntity = CoordinatorEntity
    update_coordinator.DataUpdateCoordinator = object
    update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})

    @dataclass(frozen=True, kw_only=True)
    class EntityDescription:
        key: str
        translation_key: str | None = None
        entity_category: object | None = None
        entity_registry_enabled_default: bool = True

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription(EntityDescription):
        native_unit_of_measurement: object | None = None
        device_class: object | None = None
        state_class: object | None = None

    @dataclass(frozen=True, kw_only=True)
    class BinarySensorEntityDescription(EntityDescription):
        device_class: object | None = None

    @dataclass(frozen=True, kw_only=True)
    class NumberEntityDescription(EntityDescription):
        native_min_value: float | None = None
        native_max_value: float | None = None
        native_step: float | None = None
        native_unit_of_measurement: object | None = None

    @dataclass(frozen=True, kw_only=True)
    class SelectEntityDescription(EntityDescription):
        pass

    @dataclass(frozen=True, kw_only=True)
    class ButtonEntityDescription(EntityDescription):
        pass

    climate = _module("homeassistant.components.climate")
    climate.ClimateEntity = type("ClimateEntity", (), {})
    climate.ClimateEntityFeature = types.SimpleNamespace(
        TARGET_TEMPERATURE=1,
        TURN_ON=2,
        TURN_OFF=4,
        PRESET_MODE=8,
    )

    class HVACMode(Enum):
        HEAT = "heat"
        AUTO = "auto"
        OFF = "off"

    class HVACAction(Enum):
        HEATING = "heating"
        IDLE = "idle"
        OFF = "off"

    climate.HVACMode = HVACMode
    climate.HVACAction = HVACAction

    sensor = _module("homeassistant.components.sensor")
    sensor.SensorEntity = type("SensorEntity", (), {})
    sensor.SensorEntityDescription = SensorEntityDescription
    sensor.SensorDeviceClass = types.SimpleNamespace(
        TEMPERATURE="temperature",
        HUMIDITY="humidity",
        TIMESTAMP="timestamp",
        DURATION="duration",
    )
    sensor.SensorStateClass = types.SimpleNamespace(
        MEASUREMENT="measurement",
        TOTAL_INCREASING="total_increasing",
    )

    binary_sensor = _module("homeassistant.components.binary_sensor")
    binary_sensor.BinarySensorEntity = type("BinarySensorEntity", (), {})
    binary_sensor.BinarySensorEntityDescription = BinarySensorEntityDescription

    number = _module("homeassistant.components.number")
    number.NumberEntity = type("NumberEntity", (), {})
    number.NumberEntityDescription = NumberEntityDescription

    select = _module("homeassistant.components.select")
    select.SelectEntity = type("SelectEntity", (), {})
    select.SelectEntityDescription = SelectEntityDescription

    lock = _module("homeassistant.components.lock")
    lock.LockEntity = type("LockEntity", (), {})

    button = _module("homeassistant.components.button")
    button.ButtonEntity = type("ButtonEntity", (), {})
    button.ButtonEntityDescription = ButtonEntityDescription

    util = _module("homeassistant.util", package=True)
    dt = _module("homeassistant.util.dt")
    dt.now = lambda: datetime.now(timezone.utc)
    dt.as_local = lambda value: value
    util.dt = dt


_install_dependency_stubs()

for loaded_name in tuple(sys.modules):
    if loaded_name.startswith("custom_components.zentraly"):
        del sys.modules[loaded_name]

custom_components = _module("custom_components", package=True)
custom_components.__path__ = [str(ROOT / "custom_components")]
zentraly_package = _module("custom_components.zentraly", package=True)
zentraly_package.__path__ = [str(PACKAGE_PATH)]

for dependency in ("const.py", "api.py", "schedule.py", "zttin01.py", "advanced.py"):
    _load_module(f"custom_components.zentraly.{dependency[:-3]}", dependency)

PLATFORM_NAMES = (
    "climate",
    "sensor",
    "binary_sensor",
    "number",
    "select",
    "lock",
    "button",
)
PLATFORMS = {
    name: _load_module(f"custom_components.zentraly.{name}", f"{name}.py")
    for name in PLATFORM_NAMES
}
integration = _load_module("custom_components.zentraly.integration", "__init__.py")
config_flow = _load_module("custom_components.zentraly.config_flow", "config_flow.py")
advanced = sys.modules["custom_components.zentraly.advanced"]
ConfigEntry = sys.modules["homeassistant.config_entries"].ConfigEntry


class FakeCoordinator:
    def __init__(self, data: list[dict]) -> None:
        self.data = data
        self.last_update_success = True

    async def async_request_refresh(self) -> None:
        return None

    def async_update_listeners(self) -> None:
        return None


class FakeServices:
    def __init__(self) -> None:
        self.registered: dict[str, tuple] = {}

    def has_service(self, domain: str, service: str) -> bool:
        return service in self.registered

    def async_register(self, domain: str, service: str, callback, *, schema) -> None:
        self.registered[service] = (domain, callback, schema)


class FakeHass:
    def __init__(self, entry_id: str, coordinator: FakeCoordinator) -> None:
        self.data = {
            "zentraly": {
                entry_id: {
                    "api": object(),
                    "coordinator": coordinator,
                    "drafts": advanced.AdvancedDraftStore(),
                }
            }
        }
        self.services = FakeServices()


DEVICES = [
    {"serial": "LEGACY", "name": "Legacy", "device_type": 2, "connected": True},
    {
        "serial": "THERMOSTAT",
        "parent_serial": "HUB",
        "name": "Thermostat",
        "device_type": 16,
        "connected": True,
        "mac": "AA:BB:CC:DD:EE:FF",
        "endpoint_id": 1,
    },
    {
        "serial": "BOILER",
        "parent_serial": "HUB",
        "name": "Boiler",
        "device_type": 17,
        "connected": True,
        "mac": "11:22:33:44:55:66",
        "endpoint_id": 1,
    },
]


class PlatformSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_reuses_saved_or_derived_identity_after_auth_rejection(self) -> None:
        api_class = integration.ZentralyApi
        identities = []

        async def reject_login(api):
            identities.append(api._device_guid)
            raise integration.ZentralyApiError("test rejection")

        entries = [ConfigEntry("entry-a"), ConfigEntry("entry-b"), ConfigEntry("entry-c")]
        for entry in entries:
            entry.data = {"email": "test@example.invalid", "password": "test-password"}
        entries[2].data["device_guid"] = "SAVED-INSTALLATION-GUID"

        with patch.object(api_class, "authenticate", reject_login):
            for entry in entries:
                for _ in range(2):
                    hass = FakeHass(entry.entry_id, FakeCoordinator([]))
                    with self.assertLogs(integration.__name__, level="ERROR"):
                        self.assertFalse(await integration.async_setup_entry(hass, entry))

        self.assertEqual(identities[0], identities[1])
        self.assertEqual(identities[2], identities[3])
        self.assertNotEqual(identities[0], identities[2])
        self.assertEqual(["SAVED-INSTALLATION-GUID"] * 2, identities[4:])
        self.assertNotIn("device_guid", entries[0].data)

    async def test_config_flow_saves_the_identity_used_for_login(self) -> None:
        identities = []

        async def accept_login(api):
            identities.append(api._device_guid)
            return {}

        credentials = {"email": "test@example.invalid", "password": "test-password"}
        with patch.object(config_flow.ZentralyApi, "authenticate", accept_login):
            for _ in range(2):
                flow = config_flow.ZentralyConfigFlow()
                flow.hass = FakeHass("new-entry", FakeCoordinator([]))
                result = await flow.async_step_user(credentials)
                self.assertEqual(identities[-1], result["data"]["device_guid"])
                self.assertEqual(credentials["email"], result["data"]["email"])
                self.assertEqual(credentials["password"], result["data"]["password"])
        self.assertNotEqual(identities[0], identities[1])

    async def test_all_platforms_import_and_setup_expected_entities(self) -> None:
        entry = ConfigEntry()
        coordinator = FakeCoordinator(DEVICES)
        hass = FakeHass(entry.entry_id, coordinator)
        expected = {
            "climate": 2,
            "sensor": 13,
            "binary_sensor": 7,
            "number": 7,
            "select": 7,
            "lock": 1,
            "button": 4,
        }

        for name, platform in PLATFORMS.items():
            entities: list[object] = []
            await platform.async_setup_entry(hass, entry, entities.extend)
            with self.subTest(platform=name):
                self.assertEqual(expected[name], len(entities))
                self.assertTrue(all(entity.coordinator is coordinator for entity in entities))

    async def test_poll_failure_marks_every_platform_entity_unavailable(self) -> None:
        entry = ConfigEntry()
        coordinator = FakeCoordinator(DEVICES)
        coordinator.last_update_success = False
        hass = FakeHass(entry.entry_id, coordinator)

        for name, platform in PLATFORMS.items():
            entities: list[object] = []
            await platform.async_setup_entry(hass, entry, entities.extend)
            with self.subTest(platform=name):
                self.assertTrue(entities)
                self.assertTrue(all(not entity.available for entity in entities))

    async def test_three_services_register_once(self) -> None:
        coordinator = FakeCoordinator(DEVICES)
        hass = FakeHass("entry-1", coordinator)

        integration._async_register_services(hass)
        integration._async_register_services(hass)

        self.assertEqual(
            {
                "refresh_device",
                "apply_thermostat_advanced_settings",
                "apply_boiler_settings",
            },
            set(hass.services.registered),
        )
        for domain, callback, schema in hass.services.registered.values():
            self.assertEqual("zentraly", domain)
            self.assertTrue(callable(callback))
            self.assertIsNotNone(schema)

    async def test_raw_read_failure_logs_only_a_short_device_suffix(self) -> None:
        api_module = sys.modules["custom_components.zentraly.api"]

        class FailingApi:
            async def read_zttin01_raw_attrs(self, *args):
                raise api_module.ZentralyApiError("offline")

        devices = [
            {
                "serial": "FULL-PRIVATE-SERIAL-1234",
                "parent_serial": "HUB",
                "device_type": 16,
                "mac": "AA:BB:CC:DD:EE:FF",
                "endpoint_id": 1,
            }
        ]

        with self.assertLogs("custom_components.zentraly.integration", level="WARNING") as logs:
            await integration._async_enrich_raw_attrs(FailingApi(), devices)

        output = "\n".join(logs.output)
        self.assertNotIn("FULL-PRIVATE-SERIAL-1234", output)
        self.assertIn("...1234", output)


if __name__ == "__main__":
    unittest.main()
