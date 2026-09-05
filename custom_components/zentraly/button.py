"""Button platform for Zentraly devices."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .advanced import (
    AdvancedDraftStore,
    advanced_keys_for_device,
    async_apply_boiler_advanced,
    async_apply_thermostat_advanced,
)
from .api import ZentralyApi
from .const import (
    BOILER_DEVICE_TYPES,
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
    DOMAIN,
)
from .zttin01 import get_device_data


@dataclass(frozen=True, kw_only=True)
class ZentralyButtonDescription(ButtonEntityDescription):
    """Description for a Zentraly button."""

    kind: str
    device_types: set[int]


BUTTON_DESCRIPTIONS = (
    ZentralyButtonDescription(
        key="refresh",
        translation_key="refresh",
        kind="refresh",
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT} | BOILER_DEVICE_TYPES,
    ),
    ZentralyButtonDescription(
        key="apply_advanced_settings",
        translation_key="apply_advanced_settings",
        kind="apply_advanced_settings",
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT} | BOILER_DEVICE_TYPES,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zentraly button entities."""
    api: ZentralyApi = hass.data[DOMAIN][entry.entry_id]["api"]
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    drafts: AdvancedDraftStore = hass.data[DOMAIN][entry.entry_id]["drafts"]
    devices = coordinator.data or []

    entities = [
        ZentralyAdvancedButton(coordinator, api, drafts, device, description)
        for device in devices
        for description in BUTTON_DESCRIPTIONS
        if device.get("device_type") in description.device_types
    ]

    async_add_entities(entities)


class ZentralyAdvancedButton(CoordinatorEntity, ButtonEntity):
    """Zentraly advanced configuration button."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        api: ZentralyApi,
        drafts: AdvancedDraftStore,
        device: dict[str, Any],
        description: ZentralyButtonDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._api = api
        self._drafts = drafts
        self._device_serial = device["serial"]
        self._device_type = device.get("device_type")
        self._attr_unique_id = f"zentraly_{device['serial']}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device["serial"])},
            "name": device.get("name", "Zentraly Device"),
            "manufacturer": "Zentraly (FV Group)",
            "model": (
                "ZTTIN01 Thermostat"
                if self._device_type == DEVICE_TYPE_ZTTIN01_THERMOSTAT
                else "Boiler Sensor"
            ),
            "sw_version": device.get("firmware"),
        }

    @property
    def _device_data(self) -> dict[str, Any] | None:
        """Get current device data from coordinator."""
        return get_device_data(self.coordinator, self._device_serial)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._device_data
        return super().available and bool(data and data.get("connected", False))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return button state attributes."""
        if self.entity_description.kind != "apply_advanced_settings":
            return None

        dirty = sorted(self._drafts.dirty_keys(self._device_serial))
        attributes = {
            "pending_changes": dirty,
            "has_pending_changes": bool(dirty),
        }
        if result := self._drafts.last_apply_result(self._device_serial):
            attributes.update({
                "last_apply_confirmed_changes": sorted(result.confirmed_keys or set()),
                "last_apply_unconfirmed_changes": sorted(result.unconfirmed_keys or set()),
            })
        return attributes

    async def async_press(self) -> None:
        """Handle the button press."""
        data = self._device_data
        if data is None:
            return

        if self.entity_description.kind == "refresh":
            self._drafts.clear(self._device_serial)
            await self.coordinator.async_request_refresh()
            self.coordinator.async_update_listeners()
            return

        dirty_keys = self._drafts.dirty_keys(self._device_serial)
        if not dirty_keys:
            return

        values = self._drafts.values_for(
            self._device_serial,
            data,
            advanced_keys_for_device(data),
        )

        if self._device_type == DEVICE_TYPE_ZTTIN01_THERMOSTAT:
            await async_apply_thermostat_advanced(
                self._api,
                self.coordinator,
                self._drafts,
                data,
                values,
                dirty_keys,
            )
            return

        if self._device_type in BOILER_DEVICE_TYPES:
            await async_apply_boiler_advanced(
                self._api,
                self.coordinator,
                self._drafts,
                data,
                values,
                dirty_keys,
            )
