"""Select platform for Zentraly thermostats."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .advanced import (
    AdvancedDraftStore,
    BOILER_COMFORT_MODE,
    BOILER_FORCED_ON,
    BOILER_H2O_ENABLED,
    BOILER_WEATHER_TYPE,
    BOOL_TO_YES_NO,
    DISPLAY_TYPE_OPTIONS,
    DISPLAY_TYPE_TO_OPTION,
    OPTION_TO_DISPLAY_TYPE,
    OPTION_TO_WEATHER_TYPE,
    THERMOSTAT_DISPLAY_ALWAYS_ON,
    THERMOSTAT_DISPLAY_TYPE,
    WEATHER_TYPE_OPTIONS,
    WEATHER_TYPE_TO_OPTION,
    YES_NO_OPTIONS,
    YES_NO_TO_BOOL,
)
from .api import ZentralyApi, command_device_id
from .const import (
    BOILER_DEVICE_TYPES,
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
    DOMAIN,
    ZTTIN01_AWAY_TEMPERATURE,
    ZTTIN01_DEFAULT_ENDPOINT,
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

MODE_TO_OPTION = {
    ZTTIN01_MODE_OFF: "off",
    ZTTIN01_MODE_MANUAL: "manual",
    ZTTIN01_MODE_AUTO: "auto",
    ZTTIN01_MODE_AWAY: "away",
}
OPTION_TO_MODE = {value: key for key, value in MODE_TO_OPTION.items()}
OPTIONS = ["off", "manual", "auto", "away"]


@dataclass(frozen=True, kw_only=True)
class ZentralyDraftSelectDescription(SelectEntityDescription):
    """Description for an advanced draft select."""

    device_types: set[int]
    value_key: str
    options: list[str]
    kind: str
    enabled_default: bool = True


DRAFT_SELECT_DESCRIPTIONS = (
    ZentralyDraftSelectDescription(
        key=THERMOSTAT_DISPLAY_ALWAYS_ON,
        translation_key=THERMOSTAT_DISPLAY_ALWAYS_ON,
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key=THERMOSTAT_DISPLAY_ALWAYS_ON,
        options=YES_NO_OPTIONS,
        kind="bool",
    ),
    ZentralyDraftSelectDescription(
        key=THERMOSTAT_DISPLAY_TYPE,
        translation_key=THERMOSTAT_DISPLAY_TYPE,
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key=THERMOSTAT_DISPLAY_TYPE,
        options=DISPLAY_TYPE_OPTIONS,
        kind="display_type",
    ),
    ZentralyDraftSelectDescription(
        key=BOILER_H2O_ENABLED,
        translation_key=BOILER_H2O_ENABLED,
        device_types=BOILER_DEVICE_TYPES,
        value_key=BOILER_H2O_ENABLED,
        options=YES_NO_OPTIONS,
        kind="bool",
    ),
    ZentralyDraftSelectDescription(
        key=BOILER_COMFORT_MODE,
        translation_key=BOILER_COMFORT_MODE,
        device_types=BOILER_DEVICE_TYPES,
        value_key=BOILER_COMFORT_MODE,
        options=YES_NO_OPTIONS,
        kind="bool",
    ),
    ZentralyDraftSelectDescription(
        key=BOILER_FORCED_ON,
        translation_key=BOILER_FORCED_ON,
        device_types=BOILER_DEVICE_TYPES,
        value_key=BOILER_FORCED_ON,
        options=YES_NO_OPTIONS,
        kind="bool",
    ),
    ZentralyDraftSelectDescription(
        key=BOILER_WEATHER_TYPE,
        translation_key=BOILER_WEATHER_TYPE,
        device_types=BOILER_DEVICE_TYPES,
        value_key=BOILER_WEATHER_TYPE,
        options=WEATHER_TYPE_OPTIONS,
        kind="weather_type",
        enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zentraly select entities."""
    api: ZentralyApi = hass.data[DOMAIN][entry.entry_id]["api"]
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    drafts: AdvancedDraftStore = hass.data[DOMAIN][entry.entry_id]["drafts"]
    devices = coordinator.data or []

    entities: list[SelectEntity] = [
        ZentralyModeSelect(coordinator, api, device)
        for device in devices
        if device.get("device_type") == DEVICE_TYPE_ZTTIN01_THERMOSTAT
    ]
    entities.extend(
        ZentralyDraftSelect(coordinator, drafts, device, description)
        for device in devices
        for description in DRAFT_SELECT_DESCRIPTIONS
        if device.get("device_type") in description.device_types
    )

    async_add_entities(entities)


class ZentralyModeSelect(CoordinatorEntity, SelectEntity):
    """Zentraly mode select entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "zentraly_mode"
    _attr_options = OPTIONS

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        api: ZentralyApi,
        device: dict[str, Any],
    ) -> None:
        """Initialize the mode select."""
        super().__init__(coordinator)
        self._api = api
        self._device_serial = device["serial"]
        self._command_device_id = command_device_id(device)
        self._device_mac = device.get("mac")
        self._endpoint_id = device.get("endpoint_id") or ZTTIN01_DEFAULT_ENDPOINT
        self._attr_unique_id = f"zentraly_{device['serial']}_mode_select"
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
    def current_option(self) -> str | None:
        """Return current Zentraly mode option."""
        if data := self._device_data:
            return MODE_TO_OPTION.get(data.get("mode"))
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._device_data
        return super().available and bool(data and data.get("connected", False))

    async def async_select_option(self, option: str) -> None:
        """Set Zentraly mode."""
        if option not in OPTION_TO_MODE:
            return

        mode = OPTION_TO_MODE[option]
        expected_state: dict[str, Any] = {"mode": mode}

        if mode == ZTTIN01_MODE_MANUAL:
            target_temperature = normal_heat_target_temperature(self._device_data)
            await self._api.set_zttin01_target_temperature(
                self._command_device_id,
                self._device_mac,
                self._endpoint_id,
                target_temperature,
            )
            expected_state["target_temperature"] = target_temperature
        else:
            await self._api.set_zttin01_mode(
                self._command_device_id,
                self._device_mac,
                self._endpoint_id,
                mode,
            )
            if mode == ZTTIN01_MODE_OFF:
                expected_state["target_temperature"] = ZTTIN01_OFF_TEMPERATURE
            elif mode == ZTTIN01_MODE_AWAY:
                expected_state["target_temperature"] = ZTTIN01_AWAY_TEMPERATURE

        await refresh_zttin01_after_write(
            self._api,
            self.coordinator,
            self._device_serial,
            self._device_mac,
            self._endpoint_id,
            expected_state,
            command_device_id=self._command_device_id,
        )


class ZentralyDraftSelect(CoordinatorEntity, SelectEntity):
    """Advanced configuration draft select."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        drafts: AdvancedDraftStore,
        device: dict[str, Any],
        description: ZentralyDraftSelectDescription,
    ) -> None:
        """Initialize the draft select."""
        super().__init__(coordinator)
        self._drafts = drafts
        self.entity_description = description
        self._device_serial = device["serial"]
        device_type = device.get("device_type")
        self._attr_options = description.options
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
    def current_option(self) -> str | None:
        """Return current draft or device option."""
        if not (data := self._device_data):
            return None

        value = self._drafts.get(
            self._device_serial,
            self.entity_description.value_key,
            data.get(self.entity_description.value_key),
        )
        if value is None:
            return None

        if self.entity_description.kind == "bool":
            return BOOL_TO_YES_NO.get(bool(value))
        if self.entity_description.kind == "display_type":
            try:
                return DISPLAY_TYPE_TO_OPTION.get(int(value))
            except (TypeError, ValueError):
                return None
        if self.entity_description.kind == "weather_type":
            try:
                return WEATHER_TYPE_TO_OPTION.get(int(value))
            except (TypeError, ValueError):
                return None
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

    async def async_select_option(self, option: str) -> None:
        """Update the draft value without writing to the device."""
        if option not in self.entity_description.options:
            return

        if self.entity_description.kind == "bool":
            value = YES_NO_TO_BOOL[option]
        elif self.entity_description.kind == "display_type":
            value = OPTION_TO_DISPLAY_TYPE[option]
        elif self.entity_description.kind == "weather_type":
            value = OPTION_TO_WEATHER_TYPE[option]
        else:
            return

        self._drafts.set(
            self._device_serial,
            self.entity_description.value_key,
            value,
        )
        self.coordinator.async_update_listeners()
