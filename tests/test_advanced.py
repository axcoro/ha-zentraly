"""Focused tests for advanced Zentraly write confirmation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "custom_components" / "zentraly"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components)

zentraly_package = types.ModuleType("custom_components.zentraly")
zentraly_package.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.zentraly", zentraly_package)

aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientSession = object
sys.modules.setdefault("aiohttp", aiohttp)

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)

homeassistant_const = types.ModuleType("homeassistant.const")
homeassistant_const.Platform = types.SimpleNamespace(
    CLIMATE="climate",
    SENSOR="sensor",
    BINARY_SENSOR="binary_sensor",
    NUMBER="number",
    SELECT="select",
    LOCK="lock",
    BUTTON="button",
)
sys.modules.setdefault("homeassistant.const", homeassistant_const)

homeassistant_helpers = types.ModuleType("homeassistant.helpers")
homeassistant_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", homeassistant_helpers)

update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
update_coordinator.DataUpdateCoordinator = object
sys.modules.setdefault("homeassistant.helpers.update_coordinator", update_coordinator)

_load_module("custom_components.zentraly.const", PACKAGE_PATH / "const.py")
api_module = _load_module("custom_components.zentraly.api", PACKAGE_PATH / "api.py")
zttin01_module = _load_module("custom_components.zentraly.zttin01", PACKAGE_PATH / "zttin01.py")
advanced = _load_module("custom_components.zentraly.advanced", PACKAGE_PATH / "advanced.py")


class FakeCoordinator:
    """Minimal coordinator that records updates and refreshes."""

    def __init__(self, device: dict) -> None:
        self.data = [device]
        self.refresh_count = 0
        self.listener_count = 0

    def async_set_updated_data(self, data: list[dict]) -> None:
        self.data = data

    async def async_request_refresh(self) -> None:
        self.refresh_count += 1

    def async_update_listeners(self) -> None:
        self.listener_count += 1


class FakeApi:
    """Record writes and return a configured thermostat read."""

    def __init__(
        self,
        state: dict | None = None,
        error: Exception | None = None,
        write_error: Exception | None = None,
        states: list[dict] | None = None,
    ) -> None:
        self.state = state or {}
        self.error = error
        self.write_error = write_error
        self.states = list(states or [])
        self.writes: list[tuple] = []
        self.reads: list[tuple] = []

    async def send_write_attr_command(self, *args, **kwargs) -> dict:
        self.writes.append((args, kwargs))
        if self.write_error:
            raise self.write_error
        return {"status": 200}

    async def read_zttin01_thermostat_state(self, *args) -> dict:
        self.reads.append(args)
        if self.error:
            raise self.error
        if self.states:
            return dict(self.states.pop(0))
        return dict(self.state)


class Zttin01RefreshTests(unittest.IsolatedAsyncioTestCase):
    """A read failure or mismatch must never become invented state."""

    def setUp(self) -> None:
        self.sleep = AsyncMock()
        self.sleep_patcher = patch("asyncio.sleep", new=self.sleep)
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)

    async def test_failed_read_does_not_apply_expected_state(self) -> None:
        coordinator = FakeCoordinator({"serial": "CHILD", "target_temperature": 20.0})
        api = FakeApi(error=api_module.ZentralyApiError("offline"))

        state = await zttin01_module.refresh_zttin01_after_write(
            api,
            coordinator,
            "CHILD",
            "AA:BB:CC:DD:EE:FF",
            1,
            {"target_temperature": 22.0},
        )

        self.assertEqual({}, state)
        self.assertEqual(20.0, coordinator.data[0]["target_temperature"])
        self.assertEqual(1, coordinator.refresh_count)

    async def test_actual_read_wins_over_mismatched_expected_state(self) -> None:
        coordinator = FakeCoordinator({"serial": "CHILD", "target_temperature": 19.0})
        api = FakeApi(state={"target_temperature": 20.0})

        state = await zttin01_module.refresh_zttin01_after_write(
            api,
            coordinator,
            "CHILD",
            "AA:BB:CC:DD:EE:FF",
            1,
            {"target_temperature": 22.0},
        )

        self.assertEqual({"target_temperature": 20.0}, state)
        self.assertEqual(20.0, coordinator.data[0]["target_temperature"])
        self.assertEqual(1, coordinator.refresh_count)

    async def test_matching_read_does_not_request_redundant_full_refresh(self) -> None:
        coordinator = FakeCoordinator({"serial": "CHILD", "target_temperature": 19.0})
        api = FakeApi(state={"target_temperature": 22.0})

        await zttin01_module.refresh_zttin01_after_write(
            api,
            coordinator,
            "CHILD",
            "AA:BB:CC:DD:EE:FF",
            1,
            {"target_temperature": 22.0},
        )

        self.assertEqual(22.0, coordinator.data[0]["target_temperature"])
        self.assertEqual(0, coordinator.refresh_count)

    async def test_command_parent_is_separate_from_coordinator_child_identity(self) -> None:
        coordinator = FakeCoordinator({"serial": "CHILD", "target_temperature": 19.0})
        api = FakeApi(state={"target_temperature": 20.0})

        await zttin01_module.refresh_zttin01_after_write(
            api,
            coordinator,
            "CHILD",
            "AA:BB:CC:DD:EE:FF",
            1,
            command_device_id="PARENT",
        )

        self.assertEqual("PARENT", api.reads[0][0])
        self.assertEqual("CHILD", coordinator.data[0]["serial"])
        self.assertEqual(20.0, coordinator.data[0]["target_temperature"])


class ThermostatAdvancedConfirmationTests(unittest.IsolatedAsyncioTestCase):
    """Advanced drafts clear only after exact readAttr confirmation."""

    def setUp(self) -> None:
        self.sleep = AsyncMock()
        self.sleep_patcher = patch("asyncio.sleep", new=self.sleep)
        self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)

    def _store_with_drafts(self) -> object:
        store = advanced.AdvancedDraftStore()
        store.set("CHILD", advanced.THERMOSTAT_TEMPERATURE_OFFSET, 1.5)
        store.set("CHILD", advanced.THERMOSTAT_AWAY_TEMPERATURE, 18.0)
        return store

    def _device(self) -> dict:
        return {
            "serial": "CHILD",
            "parent_serial": "PARENT",
            "iot_hub_device_id": "IOT-HUB",
            "mac": "AA:BB:CC:DD:EE:FF",
            "endpoint_id": 1,
            "temperature_offset": 0.0,
            "away_temperature": 17.0,
        }

    async def test_partial_confirmation_clears_only_matching_draft(self) -> None:
        store = self._store_with_drafts()
        coordinator = FakeCoordinator(self._device())
        api = FakeApi(state={"temperature_offset": 1.5, "away_temperature": 17.0})
        values = {"temperature_offset": 1.5, "away_temperature": 18.0}
        dirty = set(values)

        with self.assertRaisesRegex(advanced.ZentralyConfirmationError, "away_temperature"):
            await advanced.async_apply_thermostat_advanced(
                api, coordinator, store, self._device(), values, dirty
            )

        self.assertEqual({"away_temperature"}, store.dirty_keys("CHILD"))
        result = store.last_apply_result("CHILD")
        self.assertEqual({"temperature_offset"}, result.confirmed_keys)
        self.assertEqual({"away_temperature"}, result.unconfirmed_keys)

    async def test_failed_confirmation_preserves_all_drafts(self) -> None:
        store = self._store_with_drafts()
        coordinator = FakeCoordinator(self._device())
        api = FakeApi(error=api_module.ZentralyApiError("offline"))
        values = {"temperature_offset": 1.5, "away_temperature": 18.0}

        with self.assertRaises(advanced.ZentralyConfirmationError):
            await advanced.async_apply_thermostat_advanced(
                api, coordinator, store, self._device(), values, set(values)
            )

        self.assertEqual(set(values), store.dirty_keys("CHILD"))

    async def test_failed_write_preserves_drafts_and_records_failure(self) -> None:
        store = self._store_with_drafts()
        coordinator = FakeCoordinator(self._device())
        api = FakeApi(write_error=api_module.ZentralyApiError("rejected"))
        values = {"temperature_offset": 1.5, "away_temperature": 18.0}

        with self.assertRaisesRegex(api_module.ZentralyApiError, "rejected"):
            await advanced.async_apply_thermostat_advanced(
                api, coordinator, store, self._device(), values, set(values)
            )

        self.assertEqual(set(values), store.dirty_keys("CHILD"))
        result = store.last_apply_result("CHILD")
        self.assertFalse(result.wrote)
        self.assertEqual(set(), result.confirmed_keys)
        self.assertEqual(set(values), result.unconfirmed_keys)

    async def test_full_confirmation_clears_all_drafts(self) -> None:
        store = self._store_with_drafts()
        coordinator = FakeCoordinator(self._device())
        state = {"temperature_offset": 1.5, "away_temperature": 18.0}
        api = FakeApi(state=state)

        result = await advanced.async_apply_thermostat_advanced(
            api, coordinator, store, self._device(), state, set(state)
        )

        self.assertEqual(set(), store.dirty_keys("CHILD"))
        self.assertEqual(set(state), result.confirmed_keys)
        self.assertEqual(set(), result.unconfirmed_keys)
        self.assertEqual("IOT-HUB", api.writes[0][0][0])
        self.assertEqual("IOT-HUB", api.reads[0][0])

    async def test_delayed_readback_is_confirmed_by_one_read_only_retry(self) -> None:
        store = self._store_with_drafts()
        coordinator = FakeCoordinator(self._device())
        requested = {"temperature_offset": 1.5, "away_temperature": 18.0}
        api = FakeApi(
            states=[
                {"temperature_offset": 0.0, "away_temperature": 17.0},
                requested,
            ]
        )

        result = await advanced.async_apply_thermostat_advanced(
            api, coordinator, store, self._device(), requested, set(requested)
        )

        self.sleep.assert_awaited_once_with(1)
        self.assertEqual(2, len(api.reads))
        self.assertEqual(set(requested), result.confirmed_keys)
        self.assertEqual(set(), store.dirty_keys("CHILD"))
        self.assertEqual(0, coordinator.refresh_count)


class AdvancedAttributeBuilderTests(unittest.TestCase):
    """Keep the already captured advanced attribute mappings stable."""

    def test_thermostat_attributes_use_captured_ids_and_units(self) -> None:
        values = {
            advanced.THERMOSTAT_TEMPERATURE_OFFSET: -1.5,
            advanced.THERMOSTAT_AWAY_TEMPERATURE: 17.0,
            advanced.THERMOSTAT_DISPLAY_ALWAYS_ON: True,
            advanced.THERMOSTAT_DISPLAY_BRIGHTNESS: 80,
            advanced.THERMOSTAT_DISPLAY_TYPE: 1,
        }

        attrs, applied = advanced.thermostat_attrs(values, set(values))

        self.assertEqual(
            [
                {"id": 16, "type": 41, "val": -150},
                {"id": 17, "type": 41, "val": 1700},
                {"id": 101, "type": 41, "val": 1},
                {"id": 100, "type": 41, "val": 80},
                {"id": 102, "type": 41, "val": 1},
            ],
            attrs,
        )
        self.assertEqual(set(values), applied)

    def test_boiler_attributes_keep_captured_clusters_and_units(self) -> None:
        values = {
            advanced.BOILER_H2O_TEMPERATURE: 50.0,
            advanced.BOILER_H2O_ENABLED: True,
            advanced.BOILER_HEATING_TEMPERATURE: 65.0,
            advanced.BOILER_COMFORT_MODE: False,
            advanced.BOILER_WEATHER_TYPE: 3,
            advanced.BOILER_ON_DELAY: 2.0,
            advanced.BOILER_FORCED_ON: True,
        }

        attrs_by_cluster, applied = advanced.boiler_attrs_by_cluster(values, set(values))

        self.assertEqual(
            {
                65535: [
                    {"id": 56, "type": 41, "val": 5000},
                    {"id": 1056, "type": 41, "val": 1},
                    {"id": 1, "type": 41, "val": 6500},
                    {"id": 10001, "type": 41, "val": 0},
                    {"id": 1001, "type": 41, "val": 3},
                ],
                65006: [
                    {"id": 10, "type": 41, "val": 120},
                    {"id": 2, "type": 41, "val": 1},
                ],
            },
            attrs_by_cluster,
        )
        self.assertEqual(set(values), applied)

    def test_boiler_confirmation_returns_only_matching_fields(self) -> None:
        values = {
            advanced.BOILER_H2O_TEMPERATURE: 50.0,
            advanced.BOILER_H2O_ENABLED: True,
        }
        actual = {
            advanced.BOILER_H2O_TEMPERATURE: 50.0,
            advanced.BOILER_H2O_ENABLED: False,
        }

        confirmed = advanced._confirmed_boiler_keys(values, set(values), actual)

        self.assertEqual({advanced.BOILER_H2O_TEMPERATURE}, confirmed)


if __name__ == "__main__":
    unittest.main()
