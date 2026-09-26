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
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .api import ZentralyAuthError, ZentralyApi, command_device_id
from .const import (
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
    DOMAIN,
    THERMOSTAT_DEVICE_TYPES,
    ZTTWF_MODE_OFF,
    ZTTWF_MODE_MANUAL,
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

from .zttwf import refresh_zttwf_after_write

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


class ZentralyThermostat(CoordinatorEntity, ClimateEntity, RestoreEntity):
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
        self._last_heating_temperature: float | None = None
        self._changing_mode = False
        self._remember_heating_temperature()

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

    async def async_added_to_hass(self) -> None:
        """Restore the type-2 heating target when its live OFF target is 5 C."""
        await super().async_added_to_hass()
        if (not self._is_zttin01 and self._last_heating_temperature is None
                and (state := await self.async_get_last_state())):
            value = state.attributes.get("last_heating_temperature")
            if type(value) in (int, float) and self._attr_min_temp <= value <= self._attr_max_temp:
                self._last_heating_temperature = value

    @callback
    def _remember_heating_temperature(self) -> None:
        """Remember observed type-2 heating targets, never a requested value."""
        value = self.target_temperature
        if (not self._is_zttin01 and not self._changing_mode and self.available
                and self.hvac_mode == HVACMode.HEAT and type(value) in (int, float)
                and self._attr_min_temp <= value <= self._attr_max_temp):
            self._last_heating_temperature = value

    @callback
    def _handle_coordinator_update(self) -> None:
        self._remember_heating_temperature()
        super()._handle_coordinator_update()

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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose transport and retain the type-2 heating target across restarts."""
        data = self._device_data
        attributes = {
            "data_source": data.get("data_source", "cloud_snapshot") if data else "unavailable",
        }
        if not self._is_zttin01:
            attributes["last_heating_temperature"] = self._last_heating_temperature
        return attributes

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return modes using each family's validated contract."""
        data = self._device_data
        if self._is_zttin01:
            mode = data.get("mode", 1) if data else 1
            if mode == ZTTIN01_MODE_OFF:
                return HVACMode.OFF
            if mode == ZTTIN01_MODE_AUTO:
                return HVACMode.AUTO
            return HVACMode.HEAT
        mode = data.get("mode") if data else None
        if type(mode) is not int:
            return None
        return HVACMode.OFF if mode == ZTTWF_MODE_OFF else HVACMode.HEAT

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
        """Use ZTTWF output; preserve ZTTIN01 demand and temperature fallback."""
        if data := self._device_data:
            if not self._is_zttin01:
                mode = data.get("mode")
                if type(mode) is not int:
                    return None
                if mode == ZTTWF_MODE_OFF:
                    return HVACAction.OFF
                output = data.get("is_on")
                if not isinstance(output, bool):
                    return None
                return HVACAction.HEATING if output else HVACAction.IDLE

            if data.get("mode") == ZTTIN01_MODE_OFF:
                return HVACAction.OFF
            if (heat_demand := data.get("heat_demand")) is not None:
                return HVACAction.HEATING if heat_demand else HVACAction.IDLE
            current = data.get("current_temperature", 0)
            target = data.get("target_temperature", 0)
            if not isinstance(current, (int, float)) or not isinstance(target, (int, float)):
                return None
            return HVACAction.HEATING if current < target else HVACAction.IDLE
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._device_data
        return super().available and bool(data and data.get("connected", False))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        try:
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
            await self._api.set_target_temperature(self._command_device_id, temperature)
            state = await self._refresh_zttwf_after_write({"target_temperature": temperature})
            # A confirmed target selected while OFF becomes the next heating target.
            value = state["target_temperature"]
            if self._attr_min_temp <= value <= self._attr_max_temp:
                self._last_heating_temperature = value
                self.async_write_ha_state()
        except ZentralyAuthError:
            self.coordinator.config_entry.async_start_reauth(self.coordinator.hass)
            raise ConfigEntryAuthFailed("Zentraly authentication required") from None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        try:
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

            if hvac_mode not in (HVACMode.HEAT, HVACMode.OFF):
                return
            self._remember_heating_temperature()
            restore_target = (
                self._last_heating_temperature if self.hvac_mode == HVACMode.OFF else None
            )
            self._changing_mode = True
            try:
                if hvac_mode == HVACMode.HEAT:
                    await self._api.turn_on(self._command_device_id)
                    expected_state = {"mode": ZTTWF_MODE_MANUAL}
                    if restore_target is not None:
                        await self._api.set_target_temperature(self._command_device_id, restore_target)
                        expected_state["target_temperature"] = restore_target
                else:
                    await self._api.turn_off(self._command_device_id)
                    expected_state = {"mode": ZTTWF_MODE_OFF}
            finally:
                self._changing_mode = False
            await self._refresh_zttwf_after_write(expected_state)
        except ZentralyAuthError:
            self.coordinator.config_entry.async_start_reauth(self.coordinator.hass)
            raise ConfigEntryAuthFailed("Zentraly authentication required") from None

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set preset mode."""
        try:
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
        except ZentralyAuthError:
            self.coordinator.config_entry.async_start_reauth(self.coordinator.hass)
            raise ConfigEntryAuthFailed("Zentraly authentication required") from None

    async def _refresh_zttwf_after_write(self, expected_state: dict[str, Any]) -> dict[str, Any]:
        """Read the same command target as the write; publish by child identity."""
        return await refresh_zttwf_after_write(
            self._api,
            self.coordinator,
            {"serial": self._device_serial, "iot_hub_device_id": self._command_device_id},
            expected_state,
        )

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
