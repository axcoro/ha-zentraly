"""Sensor platform for Zentraly thermostats."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
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
from .schedule import (
    compact_schedule_summary,
    current_scheduled_entry,
    decode_schedule,
    next_scheduled_entry,
    schedule_entry_summary,
    today_scheduled_entries,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ZentralySensorDescription(SensorEntityDescription):
    """Description for a Zentraly sensor."""

    device_types: set[int]
    value_key: str
    value_map: dict[int, str] | None = None
    enabled_default: bool = True
    cluster: int | None = None
    attr_id: int | None = None
    attr_type: int | None = None
    raw: bool = False


SENSOR_DESCRIPTIONS = (
    ZentralySensorDescription(
        key="temperature",
        translation_key="ambient_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        device_types=THERMOSTAT_DEVICE_TYPES,
        value_key="current_temperature",
    ),
    ZentralySensorDescription(
        key="humidity",
        translation_key="ambient_humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        device_types=THERMOSTAT_DEVICE_TYPES,
        value_key="humidity",
    ),
    ZentralySensorDescription(
        key="decoded_schedule",
        translation_key="decoded_schedule",
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key="schedule",
    ),
    ZentralySensorDescription(
        key="scheduled_temperature",
        translation_key="scheduled_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key="schedule",
    ),
    ZentralySensorDescription(
        key="next_scheduled_change",
        translation_key="next_scheduled_change",
        device_class=SensorDeviceClass.TIMESTAMP,
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key="schedule",
    ),
    ZentralySensorDescription(
        key="heating_demand_time_today",
        translation_key="heating_demand_time_today",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        device_types={DEVICE_TYPE_ZTTIN01_THERMOSTAT},
        value_key="heating_demand_time_today",
    ),
    ZentralySensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=THERMOSTAT_DEVICE_TYPES | BOILER_DEVICE_TYPES,
        value_key="firmware",
        enabled_default=False,
    ),
    ZentralySensorDescription(
        key="off_delay",
        translation_key="off_delay",
        native_unit_of_measurement="min",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=BOILER_DEVICE_TYPES,
        value_key="off_delay",
        enabled_default=False,
    ),
    ZentralySensorDescription(
        key="boiler_message",
        translation_key="boiler_message",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=BOILER_DEVICE_TYPES,
        value_key="boiler_message",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Zentraly sensor entities."""
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    devices = coordinator.data or []

    entities = [
        ZentralySensor(coordinator, device, description)
        for device in devices
        for description in SENSOR_DESCRIPTIONS
        if device.get("device_type") in description.device_types
    ]

    _LOGGER.info("Adding %s Zentraly sensor entities", len(entities))
    async_add_entities(entities)


class ZentralySensor(CoordinatorEntity, SensorEntity):
    """Zentraly thermostat sensor entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        device: dict[str, Any],
        description: ZentralySensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_serial = device["serial"]
        device_type = device.get("device_type")

        if description.raw:
            self._attr_unique_id = f"zentraly_{device['serial']}_{description.key}"
        else:
            self._attr_unique_id = f"zentraly_{device['serial']}_{description.key}"
        self._attr_entity_registry_enabled_default = description.enabled_default
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device["serial"])},
            "name": device.get("name", "Zentraly Thermostat"),
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
    def native_value(self) -> Any:
        """Return the current sensor value."""
        if data := self._device_data:
            value = data.get(self.entity_description.value_key)
            if value is None:
                return None

            if self.entity_description.key == "decoded_schedule":
                entries = today_scheduled_entries(value)
                if entries is None:
                    return None
                return compact_schedule_summary(entries) or "Sin programaciones hoy"

            if self.entity_description.key == "scheduled_temperature":
                entry = current_scheduled_entry(value)
                if entry is None:
                    return None
                return entry["temperature"]

            if self.entity_description.key == "next_scheduled_change":
                entry = next_scheduled_entry(value)
                if entry is None:
                    return None
                return entry["datetime"]

            if self.entity_description.value_map is not None:
                try:
                    mapped_value = int(value)
                except (TypeError, ValueError):
                    return None
                return self.entity_description.value_map.get(
                    mapped_value,
                    f"unknown_{mapped_value}",
                )

            return value

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes for raw readAttr diagnostic sensors."""
        if self.entity_description.key == "decoded_schedule":
            if data := self._device_data:
                decoded = decode_schedule(data.get("schedule"))
                today_entries = today_scheduled_entries(data.get("schedule"))
                return {
                    "count": decoded.get("count"),
                    "entries": decoded.get("entries"),
                    "summary": [
                        schedule_entry_summary(entry)
                        for entry in decoded.get("entries", [])
                    ],
                    "today_entries": today_entries,
                    "today_summary": [
                        schedule_entry_summary(entry)
                        for entry in today_entries or []
                    ],
                    "parse_error": decoded.get("parse_error"),
                }
            return None

        if self.entity_description.key == "scheduled_temperature":
            if data := self._device_data:
                entry = current_scheduled_entry(data.get("schedule"))
                return {
                    "active_entry": entry,
                    "schedule_mode_active": data.get("mode") == ZTTIN01_MODE_AUTO,
                    "raw": data.get("schedule"),
                }
            return None

        if self.entity_description.key == "next_scheduled_change":
            if data := self._device_data:
                entry = next_scheduled_entry(data.get("schedule"))
                serialized_entry = dict(entry) if entry else None
                if serialized_entry and serialized_entry.get("datetime"):
                    serialized_entry["datetime"] = serialized_entry["datetime"].isoformat()
                return {
                    "next_entry": serialized_entry,
                    "time": entry.get("time") if entry else None,
                    "days": entry.get("days") if entry else None,
                    "temperature": entry.get("temperature") if entry else None,
                    "in_minutes": entry.get("in_minutes") if entry else None,
                }
            return None

        if not self.entity_description.raw:
            return None

        return {
            "cluster": self.entity_description.cluster,
            "attr_id": self.entity_description.attr_id,
            "attr_type": self.entity_description.attr_type,
            "source": "readAttr",
            "meaning": "unknown",
            "raw": True,
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        data = self._device_data
        return super().available and bool(data and data.get("connected", False))
