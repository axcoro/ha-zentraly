"""Zentraly Thermostat integration for Home Assistant."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZentralyApi, ZentralyApiError
from .const import CONF_DEVICE_GUID, DOMAIN, PLATFORMS, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zentraly from a config entry."""
    session = aiohttp_client.async_get_clientsession(hass)
    # Existing entries keep the same identity across reloads and HA restarts.
    device_guid = entry.data.get(CONF_DEVICE_GUID) or str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{DOMAIN}:{entry.entry_id}")
    ).upper()

    api = ZentralyApi(
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        session=session,
        device_guid=device_guid,
    )

    # Authenticate
    try:
        await api.authenticate()
    except ZentralyApiError as err:
        _LOGGER.error("Failed to authenticate with Zentraly: %s", err)
        return False

    async def async_update_data():
        """Fetch data from API."""
        try:
            return await api.get_devices()
        except ZentralyApiError as err:
            raise UpdateFailed(f"Error communicating with Zentraly API: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="Zentraly",
        update_method=async_update_data,
        update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
