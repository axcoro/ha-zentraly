"""Advanced configuration helpers for Zentraly devices."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from math import isclose
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import ZentralyApi
from .api import ZentralyApiError
from .api import command_device_id
from .const import (
    DEVICE_TYPE_BOILER,
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
    TEMP_SCALE,
    ZTTIN01_ATTR_TYPE_INT,
    ZTTIN01_CLUSTER_THERMOSTAT,
    ZTTIN01_COMMAND_TIMEOUT,
    ZTTIN01_DEFAULT_ENDPOINT,
)
from .zttin01 import refresh_zttin01_after_write

_LOGGER = logging.getLogger(__name__)

THERMOSTAT_TEMPERATURE_OFFSET = "temperature_offset"
THERMOSTAT_AWAY_TEMPERATURE = "away_temperature"
THERMOSTAT_DISPLAY_ALWAYS_ON = "display_always_on"
THERMOSTAT_DISPLAY_BRIGHTNESS = "display_brightness"
THERMOSTAT_DISPLAY_TYPE = "display_type"

BOILER_H2O_TEMPERATURE = "boiler_h2o_temperature"
BOILER_H2O_ENABLED = "is_h2o_enabled"
BOILER_HEATING_TEMPERATURE = "boiler_heating_temperature"
BOILER_COMFORT_MODE = "is_comfort_mode"
BOILER_ON_DELAY = "on_delay"
BOILER_FORCED_ON = "is_forced_on"
BOILER_WEATHER_TYPE = "weather_type"

THERMOSTAT_DISPLAY_KEYS = {
    THERMOSTAT_DISPLAY_ALWAYS_ON,
    THERMOSTAT_DISPLAY_BRIGHTNESS,
    THERMOSTAT_DISPLAY_TYPE,
}

THERMOSTAT_BOOLEAN_KEYS = {
    THERMOSTAT_DISPLAY_ALWAYS_ON,
}
THERMOSTAT_SCALED_KEYS = {
    THERMOSTAT_TEMPERATURE_OFFSET,
    THERMOSTAT_AWAY_TEMPERATURE,
}

THERMOSTAT_ADVANCED_KEYS = {
    THERMOSTAT_TEMPERATURE_OFFSET,
    THERMOSTAT_AWAY_TEMPERATURE,
    *THERMOSTAT_DISPLAY_KEYS,
}

BOILER_ADVANCED_KEYS = {
    BOILER_H2O_TEMPERATURE,
    BOILER_H2O_ENABLED,
    BOILER_HEATING_TEMPERATURE,
    BOILER_COMFORT_MODE,
    BOILER_ON_DELAY,
    BOILER_FORCED_ON,
    BOILER_WEATHER_TYPE,
}

DISPLAY_TYPE_TO_OPTION = {
    0: "horario",
    1: "temperatura",
}
OPTION_TO_DISPLAY_TYPE = {value: key for key, value in DISPLAY_TYPE_TO_OPTION.items()}
DISPLAY_TYPE_OPTIONS = ["temperatura", "horario"]

WEATHER_TYPE_TO_OPTION = {
    2: "losa_radiante",
    3: "radiadores",
}
OPTION_TO_WEATHER_TYPE = {value: key for key, value in WEATHER_TYPE_TO_OPTION.items()}
WEATHER_TYPE_OPTIONS = ["losa_radiante", "radiadores"]

YES_NO_TO_BOOL = {
    "yes": True,
    "no": False,
}
BOOL_TO_YES_NO = {value: key for key, value in YES_NO_TO_BOOL.items()}
YES_NO_OPTIONS = ["yes", "no"]

BOILER_KEY_TO_ATTR = {
    BOILER_H2O_TEMPERATURE: (65535, 56),
    BOILER_H2O_ENABLED: (65535, 1056),
    BOILER_HEATING_TEMPERATURE: (65535, 1),
    BOILER_COMFORT_MODE: (65535, 10001),
    BOILER_ON_DELAY: (65006, 10),
    BOILER_FORCED_ON: (65006, 2),
    BOILER_WEATHER_TYPE: (65535, 1001),
}

BOILER_BOOLEAN_KEYS = {
    BOILER_H2O_ENABLED,
    BOILER_COMFORT_MODE,
    BOILER_FORCED_ON,
}
BOILER_SCALED_KEYS = {
    BOILER_H2O_TEMPERATURE,
    BOILER_HEATING_TEMPERATURE,
    BOILER_ON_DELAY,
}


class AdvancedDraftStore:
    """Keep in-memory advanced configuration drafts by device serial."""

    def __init__(self) -> None:
        """Initialize the draft store."""
        self._values: dict[str, dict[str, Any]] = {}
        self._dirty: dict[str, set[str]] = {}
        self._last_apply: dict[str, ApplyResult] = {}

    def get(self, device_serial: str, key: str, fallback: Any = None) -> Any:
        """Return a draft value or the provided fallback."""
        return self._values.get(device_serial, {}).get(key, fallback)

    def set(self, device_serial: str, key: str, value: Any) -> None:
        """Set a draft value and mark it dirty."""
        self._values.setdefault(device_serial, {})[key] = value
        self._dirty.setdefault(device_serial, set()).add(key)
        self._last_apply.pop(device_serial, None)

    def dirty_keys(self, device_serial: str) -> set[str]:
        """Return dirty keys for a device."""
        return set(self._dirty.get(device_serial, set()))

    def is_dirty(self, device_serial: str) -> bool:
        """Return if a device has pending changes."""
        return bool(self._dirty.get(device_serial))

    def values_for(
        self,
        device_serial: str,
        device_data: dict[str, Any],
        keys: set[str],
    ) -> dict[str, Any]:
        """Return current advanced values, overlaying drafts over device data."""
        return {
            key: self.get(device_serial, key, device_data.get(key))
            for key in keys
        }

    def clear(self, device_serial: str, keys: set[str] | None = None) -> None:
        """Clear all or selected draft values."""
        if keys is None:
            self._values.pop(device_serial, None)
            self._dirty.pop(device_serial, None)
            self._last_apply.pop(device_serial, None)
            return

        for key in keys:
            self._values.get(device_serial, {}).pop(key, None)
            self._dirty.get(device_serial, set()).discard(key)

        if not self._values.get(device_serial):
            self._values.pop(device_serial, None)
        if not self._dirty.get(device_serial):
            self._dirty.pop(device_serial, None)

    def clear_all(self) -> None:
        """Clear every draft value."""
        self._values.clear()
        self._dirty.clear()
        self._last_apply.clear()

    def set_apply_result(self, device_serial: str, result: ApplyResult) -> None:
        """Store the outcome of the most recent apply attempt."""
        self._last_apply[device_serial] = result

    def last_apply_result(self, device_serial: str) -> ApplyResult | None:
        """Return the most recent apply outcome for a device."""
        return self._last_apply.get(device_serial)


@dataclass(frozen=True, kw_only=True)
class ApplyResult:
    """Result of an advanced configuration apply."""

    wrote: bool
    applied_keys: set[str]
    confirmed_keys: set[str] | None = None
    unconfirmed_keys: set[str] | None = None


class ZentralyConfirmationError(ZentralyApiError):
    """Raised when a device write cannot be confirmed by readAttr."""


def centi(value: float | int | None) -> int | None:
    """Convert Celsius to Zentraly centidegrees."""
    if value is None:
        return None
    return round(float(value) * TEMP_SCALE)


def bool_int(value: Any) -> int | None:
    """Convert a bool-like value to Zentraly 1/0."""
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("Expected a boolean value")
    return 1 if value else 0


def int_value(value: Any) -> int | None:
    """Return an integer value when possible."""
    if value is None:
        return None
    return int(value)


def minutes_to_seconds(value: float | int | None) -> int | None:
    """Convert minutes to seconds."""
    if value is None:
        return None
    return round(float(value) * 60)


def _attr(attr_id: int, value: Any) -> dict[str, Any]:
    """Build a Zentraly integer writeAttr attribute."""
    return {"id": attr_id, "type": ZTTIN01_ATTR_TYPE_INT, "val": value}


def thermostat_attrs(values: dict[str, Any], dirty_keys: set[str]) -> tuple[list[dict[str, Any]], set[str]]:
    """Build thermostat advanced write attrs."""
    attrs: list[dict[str, Any]] = []
    applied: set[str] = set()

    if THERMOSTAT_TEMPERATURE_OFFSET in dirty_keys:
        if (value := centi(values.get(THERMOSTAT_TEMPERATURE_OFFSET))) is not None:
            attrs.append(_attr(16, value))
            applied.add(THERMOSTAT_TEMPERATURE_OFFSET)

    if THERMOSTAT_AWAY_TEMPERATURE in dirty_keys:
        if (value := centi(values.get(THERMOSTAT_AWAY_TEMPERATURE))) is not None:
            attrs.append(_attr(17, value))
            applied.add(THERMOSTAT_AWAY_TEMPERATURE)

    if dirty_keys & THERMOSTAT_DISPLAY_KEYS:
        display_values = (
            (101, bool_int(values.get(THERMOSTAT_DISPLAY_ALWAYS_ON)), THERMOSTAT_DISPLAY_ALWAYS_ON),
            (100, int_value(values.get(THERMOSTAT_DISPLAY_BRIGHTNESS)), THERMOSTAT_DISPLAY_BRIGHTNESS),
            (102, int_value(values.get(THERMOSTAT_DISPLAY_TYPE)), THERMOSTAT_DISPLAY_TYPE),
        )
        for attr_id, value, key in display_values:
            if value is not None:
                attrs.append(_attr(attr_id, value))
                applied.add(key)

    return attrs, applied


def boiler_attrs_by_cluster(
    values: dict[str, Any],
    dirty_keys: set[str],
) -> tuple[dict[int, list[dict[str, Any]]], set[str]]:
    """Build boiler advanced write attrs grouped by cluster."""
    attrs_by_cluster: dict[int, list[dict[str, Any]]] = {}
    applied: set[str] = set()

    def add(cluster: int, attr_id: int, value: Any, key: str) -> None:
        if value is None:
            return
        attrs_by_cluster.setdefault(cluster, []).append(_attr(attr_id, value))
        applied.add(key)

    if BOILER_H2O_TEMPERATURE in dirty_keys:
        add(65535, 56, centi(values.get(BOILER_H2O_TEMPERATURE)), BOILER_H2O_TEMPERATURE)
    if BOILER_H2O_ENABLED in dirty_keys:
        add(65535, 1056, bool_int(values.get(BOILER_H2O_ENABLED)), BOILER_H2O_ENABLED)
    if BOILER_HEATING_TEMPERATURE in dirty_keys:
        add(65535, 1, centi(values.get(BOILER_HEATING_TEMPERATURE)), BOILER_HEATING_TEMPERATURE)
    if BOILER_COMFORT_MODE in dirty_keys:
        add(65535, 10001, bool_int(values.get(BOILER_COMFORT_MODE)), BOILER_COMFORT_MODE)
    if BOILER_WEATHER_TYPE in dirty_keys:
        add(65535, 1001, int_value(values.get(BOILER_WEATHER_TYPE)), BOILER_WEATHER_TYPE)
    if BOILER_ON_DELAY in dirty_keys:
        add(65006, 10, minutes_to_seconds(values.get(BOILER_ON_DELAY)), BOILER_ON_DELAY)
    if BOILER_FORCED_ON in dirty_keys:
        add(65006, 2, bool_int(values.get(BOILER_FORCED_ON)), BOILER_FORCED_ON)

    return attrs_by_cluster, applied


async def async_apply_thermostat_advanced(
    api: ZentralyApi,
    coordinator: DataUpdateCoordinator,
    store: AdvancedDraftStore | None,
    device: dict[str, Any],
    values: dict[str, Any],
    dirty_keys: set[str],
) -> ApplyResult:
    """Apply advanced thermostat settings."""
    attrs, applied_keys = thermostat_attrs(values, dirty_keys)
    if not attrs:
        return ApplyResult(wrote=False, applied_keys=set())

    command_target = command_device_id(device)
    try:
        await api.send_write_attr_command(
            command_target,
            device.get("mac"),
            ZTTIN01_CLUSTER_THERMOSTAT,
            device.get("endpoint_id") or ZTTIN01_DEFAULT_ENDPOINT,
            attrs,
            timeout=ZTTIN01_COMMAND_TIMEOUT,
        )
    except (ZentralyApiError, ValueError):
        result = ApplyResult(
            wrote=False,
            applied_keys=applied_keys,
            confirmed_keys=set(),
            unconfirmed_keys=applied_keys,
        )
        _store_apply_result(store, coordinator, device["serial"], result)
        raise

    expected_state = {key: values.get(key) for key in applied_keys}
    confirmed_state = await refresh_zttin01_after_write(
        api,
        coordinator,
        device["serial"],
        device.get("mac"),
        device.get("endpoint_id") or ZTTIN01_DEFAULT_ENDPOINT,
        expected_state,
        command_device_id=command_target,
    )

    confirmed_keys = _confirmed_thermostat_keys(
        values,
        applied_keys,
        confirmed_state,
    )
    unconfirmed_keys = applied_keys - confirmed_keys
    result = ApplyResult(
        wrote=True,
        applied_keys=applied_keys,
        confirmed_keys=confirmed_keys,
        unconfirmed_keys=unconfirmed_keys,
    )

    if store:
        store.clear(device["serial"], confirmed_keys)
    _store_apply_result(store, coordinator, device["serial"], result)

    if unconfirmed_keys:
        keys = ", ".join(sorted(unconfirmed_keys))
        raise ZentralyConfirmationError(
            f"Thermostat settings were not confirmed: {keys}"
        )

    return result


async def async_apply_boiler_advanced(
    api: ZentralyApi,
    coordinator: DataUpdateCoordinator,
    store: AdvancedDraftStore | None,
    device: dict[str, Any],
    values: dict[str, Any],
    dirty_keys: set[str],
) -> ApplyResult:
    """Apply advanced boiler settings and confirm each field with readAttr."""
    attrs_by_cluster, applied_keys = boiler_attrs_by_cluster(values, dirty_keys)
    if not attrs_by_cluster:
        return ApplyResult(wrote=False, applied_keys=set())

    device_serial = command_device_id(device)
    endpoint_id = device.get("endpoint_id") or ZTTIN01_DEFAULT_ENDPOINT
    try:
        for cluster, attrs in attrs_by_cluster.items():
            await api.send_write_attr_command(
                device_serial,
                device.get("mac"),
                cluster,
                endpoint_id,
                attrs,
                timeout=ZTTIN01_COMMAND_TIMEOUT,
            )
    except (ZentralyApiError, ValueError):
        result = ApplyResult(
            wrote=False,
            applied_keys=applied_keys,
            confirmed_keys=set(),
            unconfirmed_keys=applied_keys,
        )
        _store_apply_result(store, coordinator, device["serial"], result)
        raise

    confirmation_reads = _boiler_confirmation_reads(attrs_by_cluster)
    confirmed_state, confirmation_error = await _read_boiler_confirmation(
        api,
        device_serial,
        device.get("mac"),
        endpoint_id,
        confirmation_reads,
    )
    confirmed_keys = _confirmed_boiler_keys(values, applied_keys, confirmed_state)

    if confirmed_keys != applied_keys and confirmation_error is None:
        await asyncio.sleep(1)
        retry_state, confirmation_error = await _read_boiler_confirmation(
            api,
            device_serial,
            device.get("mac"),
            endpoint_id,
            _boiler_confirmation_reads_for_keys(applied_keys - confirmed_keys),
        )
        confirmed_state.update(retry_state)
        confirmed_keys = _confirmed_boiler_keys(values, applied_keys, confirmed_state)

    unconfirmed_keys = applied_keys - confirmed_keys
    result = ApplyResult(
        wrote=True,
        applied_keys=applied_keys,
        confirmed_keys=confirmed_keys,
        unconfirmed_keys=unconfirmed_keys,
    )

    await coordinator.async_request_refresh()
    if confirmed_state:
        _apply_boiler_state(coordinator, device["serial"], confirmed_state)
    if store:
        store.clear(device["serial"], confirmed_keys)
    _store_apply_result(store, coordinator, device["serial"], result)

    if unconfirmed_keys:
        keys = ", ".join(sorted(unconfirmed_keys))
        detail = "could not be read" if confirmation_error else "did not match the requested value"
        raise ZentralyConfirmationError(
            f"Boiler settings were not confirmed ({detail}): {keys}"
        )

    return result


def _boiler_confirmation_reads(
    attrs_by_cluster: dict[int, list[dict[str, Any]]],
) -> dict[int, list[dict[str, Any]]]:
    """Build readAttr requests for the exact boiler attrs just written."""
    return {
        cluster: [{"id": attr["id"], "type": attr["type"]} for attr in attrs]
        for cluster, attrs in attrs_by_cluster.items()
    }


def _boiler_confirmation_reads_for_keys(keys: set[str]) -> dict[int, list[dict[str, Any]]]:
    """Build readAttr requests for selected advanced boiler fields."""
    reads: dict[int, list[dict[str, Any]]] = {}
    for key in keys:
        cluster, attr_id = BOILER_KEY_TO_ATTR[key]
        reads.setdefault(cluster, []).append(
            {"id": attr_id, "type": ZTTIN01_ATTR_TYPE_INT}
        )
    return reads


def _confirmed_thermostat_keys(
    values: dict[str, Any],
    applied_keys: set[str],
    actual_state: dict[str, Any],
) -> set[str]:
    """Return requested thermostat keys confirmed by readAttr."""
    return {
        key
        for key in applied_keys
        if key in actual_state
        and _thermostat_values_match(key, values[key], actual_state[key])
    }


def _thermostat_values_match(key: str, expected: Any, actual: Any) -> bool:
    """Compare thermostat values in their Home Assistant units."""
    if key in THERMOSTAT_BOOLEAN_KEYS:
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if key in THERMOSTAT_SCALED_KEYS:
        try:
            return isclose(float(expected), float(actual), abs_tol=0.01)
        except (TypeError, ValueError):
            return False
    return expected == actual


async def _read_boiler_confirmation(
    api: ZentralyApi,
    device_serial: str,
    device_mac: str | None,
    endpoint_id: int,
    reads: dict[int, list[dict[str, Any]]],
) -> tuple[dict[str, Any], Exception | None]:
    """Read boiler settings without turning a failed read into a write retry."""
    try:
        return (
            await api.read_boiler_advanced_state(
                device_serial,
                device_mac,
                endpoint_id,
                reads,
            ),
            None,
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Unable to confirm boiler apply with readAttr: %s", err)
        return {}, err


def _confirmed_boiler_keys(
    values: dict[str, Any],
    applied_keys: set[str],
    actual_state: dict[str, Any],
) -> set[str]:
    """Return the requested keys whose readAttr values match the draft."""
    return {
        key
        for key in applied_keys
        if key in actual_state and _boiler_values_match(key, values[key], actual_state[key])
    }


def _boiler_values_match(key: str, expected: Any, actual: Any) -> bool:
    """Compare boiler values in their Home Assistant units."""
    if key in BOILER_BOOLEAN_KEYS:
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if key in BOILER_SCALED_KEYS:
        try:
            return isclose(float(expected), float(actual), abs_tol=0.01)
        except (TypeError, ValueError):
            return False
    return expected == actual


def _apply_boiler_state(
    coordinator: DataUpdateCoordinator,
    device_serial: str,
    state: dict[str, Any],
) -> bool:
    """Apply directly confirmed boiler fields after a cloud refresh."""
    if not coordinator.data:
        return False

    coordinator_data = []
    updated = False
    for device in coordinator.data:
        if device.get("serial") != device_serial:
            coordinator_data.append(device)
            continue
        updated_device = dict(device)
        for key in BOILER_ADVANCED_KEYS:
            if key in state:
                updated_device[key] = state[key]
                updated = True
        coordinator_data.append(updated_device)

    if updated:
        coordinator.async_set_updated_data(coordinator_data)
    return updated


def _store_apply_result(
    store: AdvancedDraftStore | None,
    coordinator: DataUpdateCoordinator,
    device_serial: str,
    result: ApplyResult,
) -> None:
    """Publish the outcome of an advanced apply attempt to button attributes."""
    if store:
        store.set_apply_result(device_serial, result)
    coordinator.async_update_listeners()


def advanced_keys_for_device(device: dict[str, Any]) -> set[str]:
    """Return advanced config keys for a device."""
    if device.get("device_type") == DEVICE_TYPE_ZTTIN01_THERMOSTAT:
        return THERMOSTAT_ADVANCED_KEYS
    if device.get("device_type") == DEVICE_TYPE_BOILER:
        return BOILER_ADVANCED_KEYS
    return set()
