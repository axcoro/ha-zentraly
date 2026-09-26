"""Upstream setpoint restoration with real read confirmation and mixed families."""
from __future__ import annotations

import copy
import types
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

import test_platform_smoke as smoke

climate = smoke.PLATFORMS["climate"]
ApiError = smoke.integration.ZentralyApiError
AuthError = smoke.integration.ZentralyAuthError


class UpstreamRestoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.device = {
            "serial": "SYNTHETIC-TYPE2", "iot_hub_device_id": "SYNTHETIC-PARENT",
            "device_type": 2, "mode": 4, "is_on": True,
            "target_temperature": 23.0, "current_temperature": 23.1,
            "connected": True, "data_source": "cloud_get_config",
        }
        self.type16 = {
            "serial": "SYNTHETIC-TYPE16", "device_type": 16,
            "parent_serial": "SYNTHETIC-PARENT", "mac": "SYNTHETIC-MAC",
            "mode": 1, "heat_demand": True, "target_temperature": 21.0,
            "connected": True,
        }
        self.actual = dict(self.device)
        self.entities = []
        self.entry = smoke.ConfigEntry()
        self.coordinator = types.SimpleNamespace(
            data=[self.device, self.type16], last_update_success=True,
            config_entry=self.entry, hass=object(),
            async_request_refresh=AsyncMock(),
        )

        def publish(devices):
            self.coordinator.data = devices
            for entity in self.entities:
                entity._handle_coordinator_update()

        self.coordinator.async_set_updated_data = publish

        async def off(_):
            self.actual.update(mode=0, target_temperature=5.0, is_on=False)

        async def on(_):
            self.actual.update(mode=2)

        async def target(_, value):
            self.actual.update(target_temperature=value)

        async def read(_):
            return {"status": 200, "ids": [
                {"temperature": 2310},
                {"targetTemp": round(self.actual["target_temperature"] * 100)},
                {"thermostatMode": self.actual["mode"]},
                {"output": int(self.actual["is_on"])},
            ]}

        self.api = Mock()
        self.api.turn_off = AsyncMock(side_effect=off)
        self.api.turn_on = AsyncMock(side_effect=on)
        self.api.set_target_temperature = AsyncMock(side_effect=target)
        self.api.get_device_config = AsyncMock(side_effect=read)
        self.entity = self._new_entity()

    def _new_entity(self):
        entity = climate.ZentralyThermostat(self.coordinator, self.api, self.device)
        entity.async_write_ha_state = Mock()
        self.entities.append(entity)
        return entity

    async def test_off_on_restores_observed_target_in_upstream_order(self):
        await self.entity.async_turn_off()
        self.assertEqual(5.0, self.entity.target_temperature)
        self.assertEqual(23.0, self.entity.extra_state_attributes["last_heating_temperature"])
        self.assertEqual(climate.HVACAction.OFF, self.entity.hvac_action)
        await self.entity.async_turn_on()
        self.assertEqual(23.0, self.entity.target_temperature)
        self.assertEqual(climate.HVACMode.HEAT, self.entity.hvac_mode)
        self.assertEqual([
            call.turn_off("SYNTHETIC-PARENT"),
            call.get_device_config("SYNTHETIC-PARENT"),
            call.turn_on("SYNTHETIC-PARENT"),
            call.set_target_temperature("SYNTHETIC-PARENT", 23.0),
            call.get_device_config("SYNTHETIC-PARENT"),
        ], self.api.mock_calls)
        self.assertEqual(self.type16, self.coordinator.data[1])

    async def test_restart_while_off_restores_saved_target_without_writing(self):
        await self.entity.async_turn_off()
        saved = dict(self.entity.extra_state_attributes)
        restored = self._new_entity()
        restored.async_get_last_state = AsyncMock(
            return_value=types.SimpleNamespace(attributes=saved)
        )
        self.api.reset_mock()
        await restored.async_added_to_hass()
        self.assertEqual([], self.api.mock_calls)
        await restored.async_turn_on()
        self.api.set_target_temperature.assert_awaited_once_with("SYNTHETIC-PARENT", 23.0)
        self.assertEqual(23.0, restored.target_temperature)

    async def test_current_available_heat_target_wins_over_restore_state(self):
        self.entity.async_get_last_state = AsyncMock(
            return_value=types.SimpleNamespace(attributes={"last_heating_temperature": 19.0})
        )
        await self.entity.async_added_to_hass()
        self.assertEqual(23.0, self.entity.extra_state_attributes["last_heating_temperature"])

    async def test_unavailable_state_does_not_replace_saved_target(self):
        self.coordinator.last_update_success = False
        self.device["target_temperature"] = 5.0
        restored = self._new_entity()
        restored.async_get_last_state = AsyncMock(
            return_value=types.SimpleNamespace(attributes={"last_heating_temperature": 24.0})
        )
        await restored.async_added_to_hass()
        restored._handle_coordinator_update()
        self.assertFalse(restored.available)
        self.assertEqual(24.0, restored.extra_state_attributes["last_heating_temperature"])

    async def test_invalid_restore_targets_are_ignored(self):
        self.device.update(mode=0, target_temperature=5.0)
        for value in (True, "23", None, 4.9, 30.1, float("nan"), float("inf")):
            with self.subTest(value=value):
                restored = self._new_entity()
                restored.async_get_last_state = AsyncMock(
                    return_value=types.SimpleNamespace(attributes={"last_heating_temperature": value})
                )
                await restored.async_added_to_hass()
                self.assertIsNone(restored.extra_state_attributes["last_heating_temperature"])

    async def test_external_heat_target_is_remembered_but_off_target_is_not(self):
        self.device["target_temperature"] = 24.0
        self.entity._handle_coordinator_update()
        self.device.update(mode=0, target_temperature=5.0)
        self.entity._handle_coordinator_update()
        await self.entity.async_turn_on()
        self.api.set_target_temperature.assert_awaited_once_with("SYNTHETIC-PARENT", 24.0)

    async def test_failed_first_write_prevents_target_write_and_starts_reauth(self):
        await self.entity.async_turn_off()
        self.api.turn_on.side_effect = AuthError("synthetic-private-session")
        with self.assertRaises(climate.ConfigEntryAuthFailed) as caught:
            await self.entity.async_turn_on()
        self.assertNotIn("synthetic-private-session", str(caught.exception))
        self.assertEqual(1, self.entry.reauth_requests)
        self.api.turn_on.assert_awaited_once()
        self.api.set_target_temperature.assert_not_awaited()
        self.assertEqual(23.0, self.entity.extra_state_attributes["last_heating_temperature"])

    async def test_failed_confirmation_retains_saved_target_and_marks_unavailable(self):
        self.api.get_device_config.side_effect = ApiError("synthetic read failure")
        with self.assertRaises(ApiError):
            await self.entity.async_set_temperature(temperature=25.0)
        self.api.set_target_temperature.assert_awaited_once()
        self.api.get_device_config.assert_awaited_once()
        self.assertEqual(23.0, self.entity.extra_state_attributes["last_heating_temperature"])
        self.assertFalse(self.entity.available)

    async def test_confirmation_remembers_actual_temperature_instead_of_request(self):
        async def rounded_target(_, value):
            self.actual["target_temperature"] = value - 0.01

        self.api.set_target_temperature.side_effect = rounded_target
        await self.entity.async_set_temperature(temperature=24.0)
        self.assertEqual(23.99, self.entity.target_temperature)
        self.assertEqual(23.99, self.entity.extra_state_attributes["last_heating_temperature"])
        self.api.get_device_config.assert_awaited_once()

    async def test_confirmed_target_changed_while_off_is_used_on_next_turn_on(self):
        await self.entity.async_turn_off()
        await self.entity.async_set_temperature(temperature=24.0)
        self.assertEqual(climate.HVACMode.OFF, self.entity.hvac_mode)
        self.assertEqual(24.0, self.entity.extra_state_attributes["last_heating_temperature"])
        self.entity.async_write_ha_state.assert_called_once_with()
        self.api.set_target_temperature.reset_mock()
        await self.entity.async_turn_on()
        self.api.set_target_temperature.assert_awaited_once_with("SYNTHETIC-PARENT", 24.0)

    async def test_failed_restore_target_is_not_retried_or_published_as_success(self):
        await self.entity.async_turn_off()
        self.api.set_target_temperature.side_effect = ApiError("synthetic write failure")
        self.api.get_device_config.reset_mock()
        with self.assertRaises(ApiError):
            await self.entity.async_turn_on()
        self.api.turn_on.assert_awaited_once()
        self.api.set_target_temperature.assert_awaited_once()
        self.api.get_device_config.assert_not_awaited()
        self.assertEqual(climate.HVACMode.OFF, self.entity.hvac_mode)
        self.assertEqual(5.0, self.entity.target_temperature)
        self.assertEqual(23.0, self.entity.extra_state_attributes["last_heating_temperature"])

    async def test_type16_uses_its_existing_heat_contract_and_keeps_identity(self):
        before = copy.deepcopy(self.coordinator.data[0])
        entity = climate.ZentralyThermostat(self.coordinator, self.api, self.type16)
        entity.async_get_last_state = AsyncMock(
            return_value=types.SimpleNamespace(attributes={"last_heating_temperature": 25.0})
        )
        self.api.set_zttin01_target_temperature = AsyncMock()
        with patch.object(entity, "_refresh_zttin01_after_write", AsyncMock()) as confirm:
            await entity.async_added_to_hass()
            await entity.async_turn_on()
        self.api.set_zttin01_target_temperature.assert_awaited_once_with(
            "SYNTHETIC-PARENT", "SYNTHETIC-MAC", 1, 21.0,
        )
        confirm.assert_awaited_once_with({"target_temperature": 21.0, "mode": 1})
        self.api.turn_on.assert_not_awaited()
        self.api.set_target_temperature.assert_not_awaited()
        entity.async_get_last_state.assert_not_awaited()
        self.assertEqual("zentraly_SYNTHETIC-TYPE16", entity._attr_unique_id)
        self.assertEqual(before, self.coordinator.data[0])


if __name__ == "__main__":
    unittest.main()
