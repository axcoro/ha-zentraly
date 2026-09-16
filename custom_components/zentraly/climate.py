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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .api import ZentralyApi
from .const import (
    DEVICE_TYPE_THERMOSTAT,
    DOMAIN,
    THERMOSTAT_MODE_OFF,
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
        if device.get("device_type") == DEVICE_TYPE_THERMOSTAT
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
        self._iot_hub_device_id = (
            device.get("iot_hub_device_id") or self._device_serial
        )
        self._attr_unique_id = f"zentraly_{device['serial']}"
        self._attr_name = device.get("name", "Thermostat")
        self._last_heating_temperature: float | None = None
        self._changing_mode = False
        self._remember_heating_temperature()

        # Device info
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device["serial"])},
            "name": device.get("name", "Zentraly Thermostat"),
            "manufacturer": "Zentraly (FV Group)",
            "model": "WiFi Thermostat",
            "sw_version": device.get("firmware"),
        }

    async def async_added_to_hass(self) -> None:
        """Recover the heating setpoint when the device starts in OFF mode."""
        await super().async_added_to_hass()
        if self._last_heating_temperature is None and (state := await self.async_get_last_state()):
            value = state.attributes.get("last_heating_temperature")
            if type(value) in (int, float) and self._attr_min_temp <= value <= self._attr_max_temp:
                self._last_heating_temperature = value

    @callback
    def _remember_heating_temperature(self) -> None:
        """OFF reports 5 C; keep the last target observed during heating mode."""
        value = self.target_temperature
        if (not self._changing_mode and self.available and self.hvac_mode == HVACMode.HEAT
            and type(value) in (int, float) and self._attr_min_temp <= value <= self._attr_max_temp):
            self._last_heating_temperature = value

    @callback
    def _handle_coordinator_update(self) -> None:
        self._remember_heating_temperature()
        super()._handle_coordinator_update()

    @property
    def _device_data(self) -> dict[str, Any] | None:
        """Get current device data from coordinator."""
        if not self.coordinator.data:
            return None
        for device in self.coordinator.data:
            if device.get("serial") == self._device_serial:
                return device
        return None

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
        """Expose the transport and retain the setpoint across power cycles."""
        data = self._device_data
        return {
            "data_source": data.get("data_source", "cloud_snapshot") if data else "unavailable",
            "last_heating_temperature": self._last_heating_temperature,
        }

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        if data := self._device_data:
            if data.get("mode") == THERMOSTAT_MODE_OFF:
                return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return current HVAC action."""
        if data := self._device_data:
            if self.hvac_mode == HVACMode.OFF:
                return HVACAction.OFF
            return HVACAction.HEATING if data.get("is_on") else HVACAction.IDLE
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        if data := self._device_data:
            return super().available and data.get("connected", False)
        return False

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return

        await self._api.set_target_temperature(self._iot_hub_device_id, temperature)
        self._last_heating_temperature = temperature
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        self._remember_heating_temperature()
        restore_target = self._last_heating_temperature if self.hvac_mode == HVACMode.OFF else None
        self._changing_mode = True
        try:
            if hvac_mode == HVACMode.HEAT:
                await self._api.turn_on(self._iot_hub_device_id)
                if restore_target is not None:
                    await self._api.set_target_temperature(self._iot_hub_device_id, restore_target)
            elif hvac_mode == HVACMode.OFF:
                await self._api.turn_off(self._iot_hub_device_id)
        finally:
            self._changing_mode = False

        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Turn the thermostat on."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the thermostat off."""
        await self.async_set_hvac_mode(HVACMode.OFF)
