"""Validated local or cloud state for Zentraly type-2 / ZTTWF thermostats."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from math import isfinite
from typing import TYPE_CHECKING, Any

import aiohttp

from .api import ZentralyApi, ZentralyApiError, ZentralyAuthError, command_device_id
from .const import TEMP_SCALE, ZTTWF_MODE_OFF

if TYPE_CHECKING:
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

ZTTWF_STATE_KEYS = (
    "current_temperature", "target_temperature", "mode", "is_on", "humidity", "is_locked",
)
_CONFIG_KEYS = {"temperature", "targetTemp", "thermostatMode", "output", "humidity", "lock"}
_CRITICAL_KEYS = {"temperature", "targetTemp", "thermostatMode", "output"}


def _finite_number(value: Any) -> bool:
    """Accept representable JSON numbers, never booleans or numeric strings."""
    if type(value) not in (int, float):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def parse_zttwf_config(config: dict[str, Any]) -> dict[str, Any]:
    """Parse the validated getConfig shape, rejecting ambiguous/incomplete state."""
    if (not isinstance(config, dict) or type(config.get("status")) not in (int, str)
            or config["status"] not in (200, "200")):
        raise ZentralyApiError("ZTTWF returned an invalid config envelope")
    ids = config.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ZentralyApiError("ZTTWF returned invalid config ids")
    values: dict[str, Any] = {}
    for item in ids:
        if not isinstance(item, dict) or len(item) != 1:
            raise ZentralyApiError("ZTTWF returned an invalid config item")
        key, value = next(iter(item.items()))
        if key not in _CONFIG_KEYS:
            continue
        if key in ("temperature", "targetTemp", "humidity"):
            if not _finite_number(value) or (key == "humidity" and not 0 <= value <= 100):
                raise ZentralyApiError("ZTTWF returned an invalid measurement")
        elif key == "thermostatMode":
            if type(value) is not int:
                raise ZentralyApiError("ZTTWF returned an invalid mode")
        elif type(value) not in (bool, int) or value not in (0, 1):
            raise ZentralyApiError("ZTTWF returned an invalid binary state")
        # Validate before comparing: True == 1 must not bypass numeric validation.
        if key in values and values[key] != value:
            raise ZentralyApiError("ZTTWF returned contradictory config items")
        values[key] = value
    if not _CRITICAL_KEYS <= values.keys():
        raise ZentralyApiError("ZTTWF config is missing required state")
    return {
        "current_temperature": values["temperature"] / TEMP_SCALE,
        "target_temperature": values["targetTemp"] / TEMP_SCALE,
        "mode": values["thermostatMode"],
        "is_on": bool(values["output"]),
        "humidity": values.get("humidity"),
        "is_locked": bool(values["lock"]) if "lock" in values else None,
    }


async def read_zttwf_state(api: ZentralyApi, device: dict[str, Any]) -> dict[str, Any]:
    """Read through the shared local/cloud path with parent/fallback routing."""
    config = await api.get_device_config(command_device_id(device))
    state = parse_zttwf_config(config)
    if "data_source" in config:
        state["data_source"] = config["data_source"]
    return state


def _matches_expected(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Confirm the requested target/mode, not a speculative literal mode echo."""
    if not expected or not expected.keys() <= {"target_temperature", "mode"}:
        return False
    if "target_temperature" in expected:
        target = expected["target_temperature"]
        if not _finite_number(target):
            return False
        # Decimal text preserves the inclusive one-cent boundary (23.01 - 23.0
        # is slightly greater than 0.01 in binary float). Never use relative tolerance.
        if abs(Decimal(str(actual["target_temperature"])) - Decimal(str(target))) > Decimal("0.01"):
            return False
    if "mode" in expected:
        mode = expected["mode"]
        if type(mode) is not int:
            return False
        if (actual["mode"] == ZTTWF_MODE_OFF) != (mode == ZTTWF_MODE_OFF):
            return False
    return True


def _publish_state(
    coordinator: DataUpdateCoordinator,
    serial: str,
    state: dict[str, Any] | None,
) -> None:
    """Publish only real state, preserving other children even with the same parent."""
    devices = []
    updated = False
    for device in coordinator.data or []:
        if device.get("serial") != serial:
            devices.append(device)
            continue
        device = dict(device)
        if state is None:
            device.update(connected=False, data_source="unavailable")
        else:
            device.update({key: state[key] for key in ZTTWF_STATE_KEYS})
            device.update(connected=True, data_source=state.get("data_source", "cloud"))
        devices.append(device)
        updated = True
    if updated:
        coordinator.async_set_updated_data(devices)


async def refresh_zttwf_after_write(
    api: ZentralyApi,
    coordinator: DataUpdateCoordinator,
    device: dict[str, Any],
    expected_state: dict[str, Any],
) -> dict[str, Any]:
    """Confirm one write with one read, or one additional read on mismatch only."""
    state = None
    for attempt in range(2):
        try:
            state = await read_zttwf_state(api, device)
        except ZentralyAuthError:
            _publish_state(coordinator, device["serial"], state)
            raise
        except (ZentralyApiError, aiohttp.ClientError, TimeoutError, OSError):
            break
        if _matches_expected(state, expected_state):
            _publish_state(coordinator, device["serial"], state)
            return state
        if attempt == 0:
            await asyncio.sleep(1)
    _publish_state(coordinator, device["serial"], state)
    # Do not refresh the whole inventory here: its snapshot could overwrite the
    # actual getConfig result. No expected value is ever published as state.
    if state is None:
        raise ZentralyApiError("ZTTWF write could not be confirmed by a valid read")
    raise ZentralyApiError("ZTTWF write was not confirmed; actual state retained")
