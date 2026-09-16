"""Zentraly Thermostat integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

from homeassistant.components import zeroconf
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo

from .api import ZentralyApi, ZentralyApiError, ZentralyAuthError
from .const import (
    CONF_DEVICE_GUID, CONF_FIREBASE_TOKEN, CONF_TOKEN, CONF_USER_ID,
    DOMAIN, PLATFORMS, SCAN_INTERVAL_SECONDS,
)
from .local import ZentralyLocalClient

_LOGGER = logging.getLogger(__name__)

ZEROCONF_SERVICE_TYPE = "_zentraly._tcp.local."
ZEROCONF_TIMEOUT_MS = 3000


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zentraly from a config entry."""
    if entry.data.get(CONF_TOKEN) and not all(entry.data.get(key) for key in (
        CONF_USER_ID, CONF_FIREBASE_TOKEN, CONF_DEVICE_GUID,
    )):
        raise ConfigEntryAuthFailed("Zentraly session is incomplete")
    session = aiohttp_client.async_get_clientsession(hass)
    discovered_service_names: set[str] = set()
    discovery_lock = asyncio.Lock()
    last_discovery_time = 0.0

    async def async_browse_local_devices() -> None:
        """Browse the service type before resolving a concrete device name."""
        nonlocal last_discovery_time
        async with discovery_lock:
            current_time = asyncio.get_running_loop().time()
            if discovered_service_names or current_time - last_discovery_time < 60:
                return
            last_discovery_time = current_time

            zc = await zeroconf.async_get_instance(hass)

            def on_service_state_change(
                zeroconf,
                service_type: str,
                name: str,
                state_change,
            ) -> None:
                del zeroconf, service_type, state_change
                discovered_service_names.add(name)

            browser = AsyncServiceBrowser(
                zc,
                ZEROCONF_SERVICE_TYPE,
                handlers=[on_service_state_change],
            )
            try:
                await asyncio.sleep(ZEROCONF_TIMEOUT_MS / 1000)
            finally:
                await browser.async_cancel()

            _LOGGER.debug(
                "Zentraly mDNS browse found %s service(s)",
                len(discovered_service_names),
            )

    async def async_resolve_local_device(
        device_serial: str,
    ) -> tuple[str, int] | None:
        """Resolve a Zentraly hub using the service advertised by the app."""
        zc = await zeroconf.async_get_instance(hass)
        expected_service_name = f"{device_serial}.{ZEROCONF_SERVICE_TYPE}"
        service_name = next(
            (
                name
                for name in discovered_service_names
                if name.casefold() == expected_service_name.casefold()
            ),
            expected_service_name,
        )
        service_info = AsyncServiceInfo(ZEROCONF_SERVICE_TYPE, service_name)
        if not await service_info.async_request(zc, ZEROCONF_TIMEOUT_MS):
            await async_browse_local_devices()
            service_name = next(
                (
                    name
                    for name in discovered_service_names
                    if name.casefold() == expected_service_name.casefold()
                ),
                expected_service_name,
            )
            service_info = AsyncServiceInfo(ZEROCONF_SERVICE_TYPE, service_name)
            if not await service_info.async_request(zc, ZEROCONF_TIMEOUT_MS):
                return None
        addresses = service_info.parsed_scoped_addresses()
        if not addresses:
            return None
        return addresses[0], service_info.port or 80

    local_client = ZentralyLocalClient(session, async_resolve_local_device)
    device_guid = entry.data.get(CONF_DEVICE_GUID) or str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{DOMAIN}:{entry.entry_id}")
    ).upper()

    api = ZentralyApi(
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        token=entry.data.get(CONF_TOKEN),
        user_id=entry.data.get(CONF_USER_ID),
        firebase_token=entry.data.get(CONF_FIREBASE_TOKEN),
        device_guid=device_guid,
        session=session,
        local_client=local_client,
    )

    # A stored session is validated by the first inventory refresh below.
    try:
        if not entry.data.get(CONF_TOKEN):
            await api.authenticate()
    except ZentralyAuthError as err:
        raise ConfigEntryAuthFailed("Zentraly authentication failed") from err
    except ZentralyApiError as err:
        _LOGGER.error("Failed to authenticate with Zentraly: %s", err)
        return False

    async def async_update_data():
        """Fetch data from API."""
        try:
            return await api.get_devices()
        except ZentralyAuthError as err:
            raise ConfigEntryAuthFailed("Zentraly session was rejected") from err
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
