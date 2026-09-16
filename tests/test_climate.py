"""Observable thermostat state, including relay hysteresis and stale updates."""
from enum import IntFlag, StrEnum
import sys
import types
import unittest
from unittest.mock import AsyncMock

from test_api import PACKAGE_PATH, _load_module
from test_setup import module


class HVACMode(StrEnum):
    HEAT = "heat"
    OFF = "off"


class HVACAction(StrEnum):
    HEATING = "heating"
    IDLE = "idle"
    OFF = "off"


class Features(IntFlag):
    TARGET_TEMPERATURE = 1
    TURN_ON = 2
    TURN_OFF = 4


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success

    async def async_added_to_hass(self):
        pass

    def _handle_coordinator_update(self):
        pass


const = sys.modules["homeassistant.const"]
const.ATTR_TEMPERATURE = "temperature"
const.UnitOfTemperature = types.SimpleNamespace(CELSIUS="°C")
module("homeassistant.components.climate", ClimateEntity=type("ClimateEntity", (), {}),
       ClimateEntityFeature=Features, HVACAction=HVACAction, HVACMode=HVACMode)
module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
module("homeassistant.helpers.restore_state", RestoreEntity=type("RestoreEntity", (), {}))
sys.modules['homeassistant.core'].callback = lambda method: method
sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = CoordinatorEntity
climate = _load_module("custom_components.zentraly.climate", PACKAGE_PATH / "climate.py")


class ClimateStateTests(unittest.TestCase):
    def setUp(self):
        self.device = {"serial": "synthetic-123", "mode": 4, "is_on": True,
                       "current_temperature": 23.1, "target_temperature": 23,
                       "connected": True}
        self.coordinator = types.SimpleNamespace(data=[self.device], last_update_success=True)
        self.entity = climate.ZentralyThermostat(self.coordinator, None, self.device)

    def test_raw_mode_four_is_heating_even_above_target(self):
        self.assertEqual(HVACMode.HEAT, self.entity.hvac_mode)
        self.assertEqual(HVACAction.HEATING, self.entity.hvac_action)

    def test_relay_off_in_heat_mode_is_idle(self):
        self.device.update(is_on=False, current_temperature=22.9)
        self.assertEqual(HVACMode.HEAT, self.entity.hvac_mode)
        self.assertEqual(HVACAction.IDLE, self.entity.hvac_action)

    def test_raw_mode_zero_is_off(self):
        self.device.update(mode=0, is_on=False)
        self.assertEqual(HVACMode.OFF, self.entity.hvac_mode)
        self.assertEqual(HVACAction.OFF, self.entity.hvac_action)

    def test_failed_refresh_makes_last_connected_device_unavailable(self):
        self.assertTrue(self.entity.available)
        self.coordinator.last_update_success = False
        self.assertFalse(self.entity.available)


class PowerTargetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.device = {'serial': 'synthetic-123', 'mode': 4, 'is_on': True,
                       'target_temperature': 23.0, 'connected': True}
        async def off(_):
            self.device.update(mode=0, target_temperature=5.0, is_on=False)
        async def on(_):
            self.device.update(mode=2)
        async def target(_, value):
            self.device.update(target_temperature=value)
        self.api = types.SimpleNamespace(turn_off=AsyncMock(side_effect=off),
            turn_on=AsyncMock(side_effect=on), set_target_temperature=AsyncMock(side_effect=target))
        self.coordinator = types.SimpleNamespace(data=[self.device], last_update_success=True,
                                                async_request_refresh=AsyncMock())
        self.entity = climate.ZentralyThermostat(self.coordinator, self.api, self.device)

    async def test_on_restores_target_after_device_off_resets_it_to_five(self):
        await self.entity.async_turn_off()
        self.assertEqual(5.0, self.entity.target_temperature)
        await self.entity.async_turn_on()
        self.assertEqual(HVACMode.HEAT, self.entity.hvac_mode)
        self.assertEqual(23.0, self.entity.target_temperature)

    async def test_reload_while_off_preserves_the_heating_target(self):
        await self.entity.async_turn_off()
        saved = dict(self.entity.extra_state_attributes)
        restored = climate.ZentralyThermostat(self.coordinator, self.api, self.device)
        restored.async_get_last_state = AsyncMock(return_value=types.SimpleNamespace(attributes=saved))
        await restored.async_added_to_hass()
        await restored.async_turn_on()
        self.assertEqual(23.0, restored.target_temperature)

    async def test_external_heat_target_update_is_remembered(self):
        self.device['target_temperature'] = 24.0
        self.entity._handle_coordinator_update()
        self.device.update(mode=0, target_temperature=5.0)
        self.entity._handle_coordinator_update()
        await self.entity.async_turn_on()
        self.assertEqual(24.0, self.entity.target_temperature)


if __name__ == "__main__":
    unittest.main()
