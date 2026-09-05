"""Binary sensor platform for Zentraly devices."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    BOILER_DEVICE_TYPES,
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
    DOMAIN,
    THERMOSTAT_DEVICE_TYPES,
    ZTTIN01_MODE_AUTO,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ZentralyBinarySensorDescription(BinarySensorEntityDescription):
    """Description for a Zentraly binary sensor."""

    device_types: set[int]
    value_key: str
    invert_value: bool = False


BINARY_SENSOR_DESCRIPTIONS = (
    ZentralyBinarySensorDescription(
        key="heat_demand",
        translation_key="heat_demand",
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT} | BOILER_DEVICE_TYPES,
        value_key="heat_demand",
    ),
    ZentralyBinarySensorDescription(
        key="is_on",
        translation_key="is_on",
        device_class="power",
        device_types=THERMOSTAT_DEVICE_TYPES - {DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key="is_on",
    ),
    ZentralyBinarySensorDescription(
        key="connected",
        translation_key="connected",
        device_class="connectivity",
        device_types=THERMOSTAT_DEVICE_TYPES,
        value_key="connected",
    ),
    ZentralyBinarySensorDescription(
        key="schedule_mode_active",
        translation_key="schedule_mode_active",
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key="mode",
    ),
    ZentralyBinarySensorDescription(
        key="is_opentherm_on",
        translation_key="is_opentherm_on",
        device_types=BOILER_DEVICE_TYPES,
        value_key="is_opentherm_on",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zentraly binary sensor entities."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    devices = coordinator.data or []

    entities = [
        ZentralyBinarySensor(coordinator, device, description)
        for device in devices
        for description in BINARY_SENSOR_DESCRIPTIONS
        if device.get("device_type") in description.device_types
    ]

    _LOGGER.info("Adding %s Zentraly binary sensor entities", len(entities))
    async_add_entities(entities)


class ZentralyBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Zentraly binary sensor entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device: dict[str, Any],
        description: ZentralyBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_serial = device["serial"]
        device_type = device.get("device_type")

        self._attr_unique_id = f"zentraly_{device['serial']}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device["serial"])},
            "name": device.get("name", "Zentraly Device"),
            "manufacturer": "Zentraly (FV Group)",
            "model": (
                "ZTTIN01 Thermostat"
                if device_type == DEVICE_TYPE_ZTTIN01_THERMOSTAT
                else "Boiler Sensor"
                if device_type in BOILER_DEVICE_TYPES
                else "WiFi Thermostat"
            ),
            "sw_version": device.get("firmware"),
        }

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
    def is_on(self) -> bool | None:
        """Return the current binary sensor state."""
        if data := self._device_data:
            value = data.get(self.entity_description.value_key)
            if value is None:
                return None
            if self.entity_description.key == "schedule_mode_active":
                try:
                    return int(value) == ZTTIN01_MODE_AUTO
                except (TypeError, ValueError):
                    return None
            state = bool(value)
            if self.entity_description.invert_value:
                return not state
            return state

        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._device_data
        return super().available and bool(data and data.get("connected", False))
