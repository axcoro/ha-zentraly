"""Number platform for Zentraly thermostats."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .advanced import (
    AdvancedDraftStore,
    BOILER_H2O_TEMPERATURE,
    BOILER_HEATING_TEMPERATURE,
    BOILER_ON_DELAY,
    THERMOSTAT_AWAY_TEMPERATURE,
    THERMOSTAT_DISPLAY_BRIGHTNESS,
    THERMOSTAT_TEMPERATURE_OFFSET,
)
from .api import ZentralyApi, command_device_id
from .const import (
    BOILER_DEVICE_TYPES,
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
    DOMAIN,
    ZTTIN01_DEFAULT_ENDPOINT,
    ZTTIN01_MODE_MANUAL,
)
from .zttin01 import get_device_data, refresh_zttin01_after_write


@dataclass(frozen=True, kw_only=True)
class ZentralyDraftNumberDescription(NumberEntityDescription):
    """Description for an advanced draft number."""

    device_types: set[int]
    value_key: str
    enabled_default: bool = True


DRAFT_NUMBER_DESCRIPTIONS = (
    ZentralyDraftNumberDescription(
        key=THERMOSTAT_TEMPERATURE_OFFSET,
        translation_key=THERMOSTAT_TEMPERATURE_OFFSET,
        native_min_value=-6,
        native_max_value=6,
        native_step=0.1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key=THERMOSTAT_TEMPERATURE_OFFSET,
    ),
    ZentralyDraftNumberDescription(
        key=THERMOSTAT_AWAY_TEMPERATURE,
        translation_key=THERMOSTAT_AWAY_TEMPERATURE,
        native_min_value=5,
        native_max_value=30,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key=THERMOSTAT_AWAY_TEMPERATURE,
    ),
    ZentralyDraftNumberDescription(
        key=THERMOSTAT_DISPLAY_BRIGHTNESS,
        translation_key=THERMOSTAT_DISPLAY_BRIGHTNESS,
        native_min_value=5,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key=THERMOSTAT_DISPLAY_BRIGHTNESS,
    ),
    ZentralyDraftNumberDescription(
        key=BOILER_H2O_TEMPERATURE,
        translation_key=BOILER_H2O_TEMPERATURE,
        native_min_value=30,
        native_max_value=60,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_types=BOILER_DEVICE_TYPES,
        value_key=BOILER_H2O_TEMPERATURE,
    ),
    ZentralyDraftNumberDescription(
        key=BOILER_HEATING_TEMPERATURE,
        translation_key=BOILER_HEATING_TEMPERATURE,
        native_min_value=30,
        native_max_value=80,
        native_step=1,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_types=BOILER_DEVICE_TYPES,
        value_key=BOILER_HEATING_TEMPERATURE,
    ),
    ZentralyDraftNumberDescription(
        key=BOILER_ON_DELAY,
        translation_key=BOILER_ON_DELAY,
        native_min_value=0,
        native_max_value=10,
        native_step=1,
        native_unit_of_measurement="min",
        device_types=BOILER_DEVICE_TYPES,
        value_key=BOILER_ON_DELAY,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zentraly number entities."""
    api: ZentralyApi = hass.data[DOMAIN][entry.entry_id]["api"]
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    drafts: AdvancedDraftStore = hass.data[DOMAIN][entry.entry_id]["drafts"]
    devices = coordinator.data or []

    entities: list[NumberEntity] = [
        ZentralyTargetTemperatureNumber(coordinator, api, device)
        for device in devices
        if device.get("device_type") == DEVICE_TYPE_ZTTIN01_THERMOSTAT
    ]
    entities.extend(
        ZentralyDraftNumber(coordinator, drafts, device, description)
        for device in devices
        for description in DRAFT_NUMBER_DESCRIPTIONS
        if device.get("device_type") in description.device_types
    )

    async_add_entities(entities)


class ZentralyTargetTemperatureNumber(CoordinatorEntity, NumberEntity):
    """Zentraly target temperature number entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "target_temperature"
    _attr_native_min_value = 5
    _attr_native_max_value = 30
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        api: ZentralyApi,
        device: dict[str, Any],
    ) -> None:
        """Initialize the target temperature number."""
        super().__init__(coordinator)
        self._api = api
        self._device_serial = device["serial"]
        self._command_device_id = command_device_id(device)
        self._device_mac = device.get("mac")
        self._endpoint_id = device.get("endpoint_id") or ZTTIN01_DEFAULT_ENDPOINT
        self._attr_unique_id = f"zentraly_{device['serial']}_target_temperature_number"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device["serial"])},
            "name": device.get("name", "Zentraly Thermostat"),
            "manufacturer": "Zentraly (FV Group)",
            "model": "ZTTIN01 Thermostat",
            "sw_version": device.get("firmware"),
        }

    @property
    def _device_data(self) -> dict[str, Any] | None:
        """Get current device data from coordinator."""
        return get_device_data(self.coordinator, self._device_serial)

    @property
    def native_value(self) -> float | None:
        """Return current target temperature."""
        if data := self._device_data:
            return data.get("target_temperature")
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._device_data
        return super().available and bool(data and data.get("connected", False))

    async def async_set_native_value(self, value: float) -> None:
        """Set target temperature."""
        await self._api.set_zttin01_target_temperature(
            self._command_device_id,
            self._device_mac,
            self._endpoint_id,
            value,
        )
        await refresh_zttin01_after_write(
            self._api,
            self.coordinator,
            self._device_serial,
            self._device_mac,
            self._endpoint_id,
            {
                "target_temperature": value,
                "mode": ZTTIN01_MODE_MANUAL,
            },
            command_device_id=self._command_device_id,
        )


class ZentralyDraftNumber(CoordinatorEntity, NumberEntity):
    """Advanced configuration draft number."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        drafts: AdvancedDraftStore,
        device: dict[str, Any],
        description: ZentralyDraftNumberDescription,
    ) -> None:
        """Initialize the draft number."""
        super().__init__(coordinator)
        self._drafts = drafts
        self.entity_description = description
        self._device_serial = device["serial"]
        device_type = device.get("device_type")
        self._attr_unique_id = f"zentraly_{device['serial']}_{description.key}_draft"
        self._attr_entity_registry_enabled_default = description.enabled_default
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device["serial"])},
            "name": device.get("name", "Zentraly Device"),
            "manufacturer": "Zentraly (FV Group)",
            "model": (
                "ZTTIN01 Thermostat"
                if device_type == DEVICE_TYPE_ZTTIN01_THERMOSTAT
                else "Boiler Sensor"
            ),
            "sw_version": device.get("firmware"),
        }

    @property
    def _device_data(self) -> dict[str, Any] | None:
        """Get current device data from coordinator."""
        return get_device_data(self.coordinator, self._device_serial)

    @property
    def native_value(self) -> float | None:
        """Return current draft or device value."""
        if data := self._device_data:
            value = self._drafts.get(
                self._device_serial,
                self.entity_description.value_key,
                data.get(self.entity_description.value_key),
            )
            if value is None:
                return None
            return float(value)
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._device_data
        return super().available and bool(data and data.get("connected", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return draft metadata."""
        dirty_keys = self._drafts.dirty_keys(self._device_serial)
        key = self.entity_description.value_key
        return {
            "pending": key in dirty_keys,
            "writes_on_apply": True,
        }

    async def async_set_native_value(self, value: float) -> None:
        """Update the draft value without writing to the device."""
        self._drafts.set(
            self._device_serial,
            self.entity_description.value_key,
            value,
        )
        self.coordinator.async_update_listeners()
