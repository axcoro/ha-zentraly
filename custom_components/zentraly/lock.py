"""Lock platform for Zentraly thermostats."""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
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
    ZTTIN01_DEFAULT_ENDPOINT,
)
from .zttin01 import get_device_data, refresh_zttin01_after_write


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zentraly lock entities."""
    api: ZentralyApi = hass.data[DOMAIN][entry.entry_id]["api"]
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    devices = coordinator.data or []

    entities = [
        ZentralyLock(coordinator, api, device)
        for device in devices
        if device.get("device_type") == DEVICE_TYPE_ZTTIN01_THERMOSTAT
    ]

    async_add_entities(entities)


class ZentralyLock(CoordinatorEntity, LockEntity):
    """Zentraly lock entity."""

    _attr_has_entity_name = True
    _attr_translation_key = "thermostat_lock"

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        api: ZentralyApi,
        device: dict[str, Any],
    ) -> None:
        """Initialize the lock."""
        super().__init__(coordinator)
        self._api = api
        self._device_serial = device["serial"]
        self._command_device_id = command_device_id(device)
        self._device_mac = device.get("mac")
        self._endpoint_id = device.get("endpoint_id") or ZTTIN01_DEFAULT_ENDPOINT
        self._attr_unique_id = f"zentraly_{device['serial']}_lock"
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
    def is_locked(self) -> bool | None:
        """Return true if the thermostat is locked."""
        if data := self._device_data:
            value = data.get("is_locked")
            if value is None:
                return None
            return bool(value)
        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._device_data
        return super().available and bool(data and data.get("connected", False))

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the thermostat."""
        await self._api.set_zttin01_lock(
            self._command_device_id,
            self._device_mac,
            self._endpoint_id,
            True,
        )
        await refresh_zttin01_after_write(
            self._api,
            self.coordinator,
            self._device_serial,
            self._device_mac,
            self._endpoint_id,
            {"is_locked": True},
            command_device_id=self._command_device_id,
        )

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the thermostat."""
        await self._api.set_zttin01_lock(
            self._command_device_id,
            self._device_mac,
            self._endpoint_id,
            False,
        )
        await refresh_zttin01_after_write(
            self._api,
            self.coordinator,
            self._device_serial,
            self._device_mac,
            self._endpoint_id,
            {"is_locked": False},
            command_device_id=self._command_device_id,
        )
