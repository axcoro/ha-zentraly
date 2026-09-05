"""Shared helpers for Zentraly ZTTIN01 entities."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import ZentralyApi, ZentralyApiError
from .const import ZTTIN01_DEFAULT_HEAT_TEMPERATURE, ZTTIN01_MODE_AWAY, ZTTIN01_OFF_TEMPERATURE

ZTTIN01_STATE_KEYS = (
    "current_temperature",
    "humidity",
    "target_temperature",
    "mode",
    "is_locked",
    "schedule",
    "temperature_offset",
    "away_temperature",
    "display_brightness",
    "display_always_on",
    "display_type",
)


def get_device_data(
    coordinator: DataUpdateCoordinator,
    device_serial: str,
) -> dict[str, Any] | None:
    """Get current device data from coordinator."""
    if not coordinator.data:
        return None

    for device in coordinator.data:
        if device.get("serial") == device_serial:
            return device

    return None


def apply_zttin01_state(
    coordinator: DataUpdateCoordinator,
    device_serial: str,
    state: dict[str, Any],
) -> bool:
    """Apply fresh ZTTIN01 values to coordinator data."""
    if not coordinator.data:
        return False

    coordinator_data = []
    updated = False

    for device in coordinator.data:
        if device.get("serial") != device_serial:
            coordinator_data.append(device)
            continue

        updated_device = dict(device)
        for key in ZTTIN01_STATE_KEYS:
            if key in state:
                updated_device[key] = state[key]
                updated = True

        coordinator_data.append(updated_device)

    if updated:
        coordinator.async_set_updated_data(coordinator_data)

    return updated


async def refresh_zttin01_after_write(
    api: ZentralyApi,
    coordinator: DataUpdateCoordinator,
    device_serial: str,
    device_mac: str | None,
    endpoint_id: int | None,
    expected_state: dict[str, Any] | None = None,
    logger: logging.Logger | None = None,
    *,
    command_device_id: str | None = None,
) -> dict[str, Any]:
    """Read effective ZTTIN01 state after a write and update the coordinator."""
    try:
        state = await api.read_zttin01_thermostat_state(
            command_device_id or device_serial,
            device_mac,
            endpoint_id,
        )
    except ZentralyApiError as err:
        if logger:
            logger.warning("Failed to read ZTTIN01 state after write: %s", err)
        state = {}
    except Exception as err:  # noqa: BLE001
        if logger:
            logger.warning("Unexpected error reading ZTTIN01 state after write: %s", err)
        state = {}

    def unconfirmed_keys(actual_state: dict[str, Any]) -> set[str]:
        return (
            {
                key
                for key, expected in expected_state.items()
                if key not in actual_state or actual_state[key] != expected
            }
            if expected_state
            else set()
        )

    unconfirmed = unconfirmed_keys(state)
    if unconfirmed:
        await asyncio.sleep(1)
        try:
            retry_state = await api.read_zttin01_thermostat_state(
                command_device_id or device_serial,
                device_mac,
                endpoint_id,
            )
        except ZentralyApiError as err:
            if logger:
                logger.warning("Failed to retry ZTTIN01 state after write: %s", err)
            retry_state = {}
        except Exception as err:  # noqa: BLE001
            if logger:
                logger.warning("Unexpected error retrying ZTTIN01 state after write: %s", err)
            retry_state = {}

        state.update(retry_state)
        unconfirmed = unconfirmed_keys(state)

    if logger and unconfirmed:
        logger.debug(
            "ZTTIN01 readback did not confirm: %s",
            ", ".join(sorted(unconfirmed)),
        )

    applied = bool(state and apply_zttin01_state(coordinator, device_serial, state))
    if applied and not unconfirmed:
        return state

    await coordinator.async_request_refresh()
    return state


def normal_heat_target_temperature(data: dict[str, Any] | None) -> float:
    """Return a target temperature that exits off/away safely."""
    data = data or {}
    mode = data.get("mode")
    target = data.get("target_temperature")

    if (
        isinstance(target, (int, float))
        and target > ZTTIN01_OFF_TEMPERATURE
        and mode != ZTTIN01_MODE_AWAY
    ):
        return target

    return ZTTIN01_DEFAULT_HEAT_TEMPERATURE
