"""Climate platform for Zentraly thermostats."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_TEMPERATURE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .api import ZentralyApi, command_device_id
from .const import (
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
    DOMAIN,
    HVAC_MODE_MAP,
    THERMOSTAT_DEVICE_TYPES,
    ZENTRALY_PRESET_AWAY,
    ZENTRALY_PRESET_NONE,
    ZTTIN01_AWAY_TEMPERATURE,
    ZTTIN01_DEFAULT_ENDPOINT,
    ZTTIN01_DEFAULT_HEAT_TEMPERATURE,
    ZTTIN01_MODE_AUTO,
    ZTTIN01_MODE_AWAY,
    ZTTIN01_MODE_MANUAL,
    ZTTIN01_MODE_OFF,
    ZTTIN01_OFF_TEMPERATURE,
)
from .zttin01 import (
    get_device_data,
    normal_heat_target_temperature,
    refresh_zttin01_after_write,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zentraly climate entities."""
    api: ZentralyApi = hass.data[DOMAIN][entry.entry_id]["api"]
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    devices = coordinator.data or []

    entities = [
        ZentralyThermostat(coordinator, api, device)
        for device in devices
        if device.get("device_type") in THERMOSTAT_DEVICE_TYPES
    ]

    async_add_entities(entities)


class ZentralyThermostat(CoordinatorEntity, ClimateEntity):
    """Zentraly thermostat entity."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_min_temp = 5
    _attr_max_temp = 30
    _attr_target_temperature_step = 0.5

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        api: ZentralyApi,
        device: dict[str, Any],
    ) -> None:
        """Initialize the thermostat."""
        super().__init__(coordinator)
        self._api = api
        self._device_serial = device["serial"]
        self._command_device_id = command_device_id(device)
        device_type = device.get("device_type")
        self._device_type = device_type
        self._device_mac = device.get("mac")
        self._endpoint_id = device.get("endpoint_id") or ZTTIN01_DEFAULT_ENDPOINT
        self._attr_unique_id = f"zentraly_{device['serial']}"
        self._attr_name = device.get("name", "Thermostat")

        if self._is_zttin01:
            self._attr_supported_features = (
                ClimateEntityFeature.TARGET_TEMPERATURE
                | ClimateEntityFeature.TURN_ON
                | ClimateEntityFeature.TURN_OFF
                | ClimateEntityFeature.PRESET_MODE
            )
            self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]
            self._attr_preset_modes = [ZENTRALY_PRESET_NONE, ZENTRALY_PRESET_AWAY]

        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device["serial"])},
            "name": device.get("name", "Zentraly Thermostat"),
            "manufacturer": "Zentraly (FV Group)",
            "model": (
                "ZTTIN01 Thermostat"
                if device_type == DEVICE_TYPE_ZTTIN01_THERMOSTAT
                else "WiFi Thermostat"
            ),
            "sw_version": device.get("firmware"),
        }

    @property
    def _is_zttin01(self) -> bool:
        """Return true for ZTTIN01 thermostats."""
        return self._device_type == DEVICE_TYPE_ZTTIN01_THERMOSTAT

    @property
    def _device_data(self) -> dict[str, Any] | None:
        """Get current device data from coordinator."""
        return get_device_data(self.coordinator, self._device_serial)

    @property
    def current_temperature(self) -> float | None:
        """Return current temperature."""
        if data := self._device_data:
            return data.get("current_temperature")
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return target temperature."""
        if data := self._device_data:
            return data.get("target_temperature")
        return None

    @property
    def current_humidity(self) -> int | None:
        """Return current humidity."""
        if data := self._device_data:
            return data.get("humidity")
        return None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        if data := self._device_data:
            mode = data.get("mode", 1)
            if self._is_zttin01:
                if mode == ZTTIN01_MODE_OFF:
                    return HVACMode.OFF
                if mode == ZTTIN01_MODE_AUTO:
                    return HVACMode.AUTO
                return HVACMode.HEAT

            mode_str = HVAC_MODE_MAP.get(mode, "heat")
            if mode_str == "heat":
                return HVACMode.HEAT
            elif mode_str == "off":
                return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def preset_mode(self) -> str | None:
        """Return current preset mode."""
        if not self._is_zttin01:
            return None

        if data := self._device_data:
            if data.get("mode") == ZTTIN01_MODE_AWAY:
                return ZENTRALY_PRESET_AWAY

        return ZENTRALY_PRESET_NONE

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return current HVAC action."""
        if data := self._device_data:
            if data.get("mode") == ZTTIN01_MODE_OFF:
                return HVACAction.OFF

            if self._is_zttin01 and (heat_demand := data.get("heat_demand")) is not None:
                return HVACAction.HEATING if heat_demand else HVACAction.IDLE

            if not self._is_zttin01 and not data.get("is_on", False):
                return HVACAction.OFF

            current = data.get("current_temperature", 0)
            target = data.get("target_temperature", 0)

            if not isinstance(current, (int, float)) or not isinstance(target, (int, float)):
                return None

            if current < target:
                return HVACAction.HEATING
            else:
                return HVACAction.IDLE
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._device_data
        return super().available and bool(data and data.get("connected", False))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        if self._is_zttin01:
            await self._api.set_zttin01_target_temperature(
                self._command_device_id,
                self._device_mac,
                self._endpoint_id,
                temperature,
            )
            await self._refresh_zttin01_after_write(
                {
                    "target_temperature": temperature,
                    "mode": ZTTIN01_MODE_MANUAL,
                }
            )
            return
        else:
            await self._api.set_target_temperature(self._command_device_id, temperature)

        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        if self._is_zttin01:
            if hvac_mode == HVACMode.HEAT:
                target_temperature = self._normal_heat_target_temperature()
                await self._api.set_zttin01_target_temperature(
                    self._command_device_id,
                    self._device_mac,
                    self._endpoint_id,
                    target_temperature,
                )
                expected_state = {
                    "target_temperature": target_temperature,
                    "mode": ZTTIN01_MODE_MANUAL,
                }
            elif hvac_mode == HVACMode.AUTO:
                await self._api.set_zttin01_mode(
                    self._command_device_id,
                    self._device_mac,
                    self._endpoint_id,
                    ZTTIN01_MODE_AUTO,
                )
                expected_state = {"mode": ZTTIN01_MODE_AUTO}
            elif hvac_mode == HVACMode.OFF:
                await self._api.set_zttin01_mode(
                    self._command_device_id,
                    self._device_mac,
                    self._endpoint_id,
                    ZTTIN01_MODE_OFF,
                )
                expected_state = {
                    "target_temperature": ZTTIN01_OFF_TEMPERATURE,
                    "mode": ZTTIN01_MODE_OFF,
                }
            else:
                return

            await self._refresh_zttin01_after_write(expected_state)
            return

        if hvac_mode == HVACMode.HEAT:
            await self._api.turn_on(self._command_device_id)
        elif hvac_mode == HVACMode.OFF:
            await self._api.turn_off(self._command_device_id)

        await self.coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        if not self._is_zttin01:
            return

        if preset_mode == ZENTRALY_PRESET_AWAY:
            await self._api.set_zttin01_mode(
                self._command_device_id,
                self._device_mac,
                self._endpoint_id,
                ZTTIN01_MODE_AWAY,
            )
            expected_state = {
                "target_temperature": ZTTIN01_AWAY_TEMPERATURE,
                "mode": ZTTIN01_MODE_AWAY,
            }
        elif preset_mode == ZENTRALY_PRESET_NONE:
            await self._api.set_zttin01_target_temperature(
                self._command_device_id,
                self._device_mac,
                self._endpoint_id,
                ZTTIN01_DEFAULT_HEAT_TEMPERATURE,
            )
            expected_state = {
                "target_temperature": ZTTIN01_DEFAULT_HEAT_TEMPERATURE,
                "mode": ZTTIN01_MODE_MANUAL,
            }
        else:
            return

        await self._refresh_zttin01_after_write(expected_state)

    def _normal_heat_target_temperature(self) -> float:
        """Return a target temperature that exits off/away safely."""
        return normal_heat_target_temperature(self._device_data)

    async def _refresh_zttin01_after_write(
        self,
        expected_state: dict[str, Any] | None = None,
    ) -> None:
        """Read effective ZTTIN01 state after a write, then refresh /App."""
        await refresh_zttin01_after_write(
            self._api,
            self.coordinator,
            self._device_serial,
            self._device_mac,
            self._endpoint_id,
            expected_state,
            _LOGGER,
            command_device_id=self._command_device_id,
        )

    async def async_turn_on(self) -> None:
        """Turn the thermostat on."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the thermostat off."""
        await self.async_set_hvac_mode(HVACMode.OFF)
