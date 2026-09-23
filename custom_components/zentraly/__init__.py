"""Zentraly Thermostat integration for Home Assistant."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import voluptuous as vol

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .advanced import (
    AdvancedDraftStore,
    BOILER_ADVANCED_KEYS,
    BOILER_COMFORT_MODE,
    BOILER_FORCED_ON,
    BOILER_H2O_ENABLED,
    BOILER_H2O_TEMPERATURE,
    BOILER_HEATING_TEMPERATURE,
    BOILER_ON_DELAY,
    BOILER_WEATHER_TYPE,
    THERMOSTAT_ADVANCED_KEYS,
    THERMOSTAT_AWAY_TEMPERATURE,
    THERMOSTAT_DISPLAY_ALWAYS_ON,
    THERMOSTAT_DISPLAY_BRIGHTNESS,
    THERMOSTAT_DISPLAY_TYPE,
    THERMOSTAT_TEMPERATURE_OFFSET,
    async_apply_boiler_advanced,
    async_apply_thermostat_advanced,
)
from .api import ZentralyApi, ZentralyApiError, ZentralyAuthError, command_device_id
from .const import (
    CONF_DEVICE_GUID,
    CONF_FIREBASE_TOKEN,
    CONF_TOKEN,
    CONF_USER_ID,
    BOILER_DEVICE_TYPES,
    DEVICE_TYPE_THERMOSTAT,
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
    DATA_REAUTH_DRAFTS,
    DOMAIN,
    PLATFORMS,
    SCAN_INTERVAL_SECONDS,
    SERVICE_APPLY_BOILER_SETTINGS,
    SERVICE_APPLY_THERMOSTAT_ADVANCED_SETTINGS,
    SERVICE_REFRESH_DEVICE,
    ZTTIN01_DEFAULT_ENDPOINT,
)

from .zttwf import read_zttwf_state

_LOGGER = logging.getLogger(__name__)

_REDUNDANT_ENTITY_SUFFIXES = frozenset({
    "is_locked",
    "display_always_on",
    "is_forced_on",
    "is_h2o_enabled",
    "is_comfort_mode",
    "target_temperature",
    "temperature_offset",
    "away_temperature",
    "display_brightness",
    "display_type",
    "boiler_heating_temperature",
    "boiler_h2o_temperature",
    "on_delay",
    "weather_type",
    "mode",
    "attr_65006_200",
    "attr_65000_1111",
    "is_on",
    "heating_on_time_today",
    "schedule",
})


def _safe_device_reference(serial: object) -> str:
    """Return a short non-identifying device reference for logs."""
    value = str(serial or "")
    return f"...{value[-4:]}" if value else "unknown"

REFRESH_DEVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_DEVICE_ID): str})
APPLY_THERMOSTAT_ADVANCED_SCHEMA = vol.Schema({
    vol.Required(ATTR_DEVICE_ID): str,
    vol.Optional(THERMOSTAT_TEMPERATURE_OFFSET): vol.All(vol.Coerce(float), vol.Range(min=-6, max=6)),
    vol.Optional(THERMOSTAT_AWAY_TEMPERATURE): vol.All(vol.Coerce(float), vol.Range(min=5, max=30)),
    vol.Optional(THERMOSTAT_DISPLAY_ALWAYS_ON): vol.Boolean(),
    vol.Optional(THERMOSTAT_DISPLAY_BRIGHTNESS): vol.All(vol.Coerce(int), vol.Range(min=5, max=100)),
    vol.Optional(THERMOSTAT_DISPLAY_TYPE): vol.In([0, 1, "horario", "temperatura"]),
})
APPLY_BOILER_SCHEMA = vol.Schema({
    vol.Required(ATTR_DEVICE_ID): str,
    vol.Optional(BOILER_H2O_TEMPERATURE): vol.All(vol.Coerce(float), vol.Range(min=30, max=60)),
    vol.Optional(BOILER_H2O_ENABLED): vol.Boolean(),
    vol.Optional(BOILER_HEATING_TEMPERATURE): vol.All(vol.Coerce(float), vol.Range(min=30, max=80)),
    vol.Optional(BOILER_COMFORT_MODE): vol.Boolean(),
    vol.Optional(BOILER_ON_DELAY): vol.All(vol.Coerce(float), vol.Range(min=0, max=10)),
    vol.Optional(BOILER_FORCED_ON): vol.Boolean(),
    vol.Optional(BOILER_WEATHER_TYPE): vol.In([2, 3, "losa_radiante", "radiadores"]),
})


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove obsolete read-only mirrors of advanced configuration controls."""
    if entry.version > 5:
        _LOGGER.error("Unsupported Zentraly config entry version: %s", entry.version)
        return False

    if entry.version < 3:
        entity_registry = er.async_get(hass)
        removed = 0
        for registry_entry in er.async_entries_for_config_entry(
            entity_registry,
            entry.entry_id,
        ):
            if _is_redundant_configuration_entity(registry_entry.unique_id):
                entity_registry.async_remove(registry_entry.entity_id)
                removed += 1

        hass.config_entries.async_update_entry(entry, version=3)
        _LOGGER.info("Removed %s redundant Zentraly configuration entities", removed)

    if entry.version < 4:
        entity_registry = er.async_get(hass)
        removed = 0
        for registry_entry in er.async_entries_for_config_entry(
            entity_registry,
            entry.entry_id,
        ):
            if registry_entry.unique_id.endswith(("_is_on", "_heating_on_time_today")):
                entity_registry.async_remove(registry_entry.entity_id)
                removed += 1

        hass.config_entries.async_update_entry(entry, version=4)
        _LOGGER.info("Removed %s obsolete demand entity entries", removed)

    if entry.version < 5:
        entity_registry = er.async_get(hass)
        removed = 0
        for registry_entry in er.async_entries_for_config_entry(
            entity_registry,
            entry.entry_id,
        ):
            if registry_entry.unique_id.endswith("_schedule"):
                entity_registry.async_remove(registry_entry.entity_id)
                removed += 1

        hass.config_entries.async_update_entry(entry, version=5)
        _LOGGER.info("Removed %s obsolete raw schedule entities", removed)

    return True


def _is_redundant_configuration_entity(unique_id: str) -> bool:
    """Return whether a legacy unique ID belongs to a removed configuration mirror."""
    return any(
        unique_id.endswith(f"_{suffix}")
        for suffix in _REDUNDANT_ENTITY_SUFFIXES
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zentraly from a config entry."""
    session = aiohttp_client.async_get_clientsession(hass)
    # Existing entries keep the same identity across reloads and HA restarts.
    device_guid = entry.data.get(CONF_DEVICE_GUID) or str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{DOMAIN}:{entry.entry_id}")
    ).upper()

    saved = dict(entry.data)
    has_token = CONF_TOKEN in saved
    # An existing token must never trigger a password-login fallback. Validate the
    # original saved fields before constructor defaults can mask an incomplete import.
    if has_token and (
        not all(isinstance(saved.get(key), str) and saved[key].strip()
                for key in (CONF_TOKEN, CONF_FIREBASE_TOKEN, CONF_DEVICE_GUID))
        or type(saved.get(CONF_USER_ID)) is not int
        or saved[CONF_USER_ID] <= 0
    ):
        raise ConfigEntryAuthFailed("Incomplete Zentraly session; reauthentication required")
    if not saved.get(CONF_DEVICE_GUID):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DEVICE_GUID: device_guid}
        )

    api = ZentralyApi(
        email=saved.get(CONF_EMAIL),
        password=saved.get(CONF_PASSWORD),
        token=saved.get(CONF_TOKEN),
        session=session,
        device_guid=device_guid,
        user_id=saved.get(CONF_USER_ID),
        firebase_token=saved.get(CONF_FIREBASE_TOKEN),
    )

    hass.data.setdefault(DOMAIN, {})
    _async_register_services(hass)

    try:
        if not has_token:
            if not saved.get(CONF_PASSWORD):
                raise ZentralyAuthError("No Zentraly session or password available")
            await api.authenticate()
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, **api.session_data()}
            )
    except ZentralyAuthError:
        raise ConfigEntryAuthFailed("Zentraly authentication required") from None
    except (ZentralyApiError, aiohttp.ClientError, TimeoutError, OSError):
        raise ConfigEntryNotReady("Unable to connect to Zentraly") from None

    async def async_update_data():
        """Fetch data from API."""
        try:
            devices = await api.get_devices()
            await _async_enrich_device_state(api, devices)
            return devices
        except ZentralyAuthError:
            raise ConfigEntryAuthFailed("Zentraly authentication required") from None
        except (ZentralyApiError, aiohttp.ClientError, TimeoutError, OSError):
            raise UpdateFailed("Unable to update Zentraly data") from None

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="Zentraly",
        config_entry=entry,
        update_method=async_update_data,
        update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
    )

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "drafts": hass.data.get(DATA_REAUTH_DRAFTS, {}).get(entry.entry_id) or AdvancedDraftStore(),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Consume only after the entire setup succeeds; failures retain the handoff.
    pending = hass.data.get(DATA_REAUTH_DRAFTS, {})
    pending.pop(entry.entry_id, None)
    if not pending:
        hass.data.pop(DATA_REAUTH_DRAFTS, None)
    return True


async def _async_enrich_device_state(
    api: ZentralyApi,
    devices: list[dict],
) -> None:
    """Read validated ZTTWF state and optional existing type-16/17 attributes."""
    for device in devices:
        device_type = device.get("device_type")
        command_target = command_device_id(device)
        try:
            if device_type == DEVICE_TYPE_THERMOSTAT:
                state = await read_zttwf_state(api, device)
                state.update(connected=True, data_source="cloud_get_config")
            elif device_type == DEVICE_TYPE_ZTTIN01_THERMOSTAT:
                state = await api.read_zttin01_raw_attrs(
                    command_target,
                    device.get("mac"),
                    device.get("endpoint_id") or ZTTIN01_DEFAULT_ENDPOINT,
                )
            elif device_type in BOILER_DEVICE_TYPES:
                state = await api.read_boiler_raw_attrs(
                    command_target,
                    device.get("mac"),
                    device.get("endpoint_id") or ZTTIN01_DEFAULT_ENDPOINT,
                )
            else:
                continue
        except ZentralyAuthError:
            raise
        except ZentralyApiError:
            if device_type == DEVICE_TYPE_THERMOSTAT:
                device.update(connected=False, data_source="unavailable")
            _LOGGER.warning(
                "Failed to read cloud state for Zentraly device %s",
                _safe_device_reference(device.get("serial")),
            )
            continue
        except Exception as err:  # noqa: BLE001
            if device_type == DEVICE_TYPE_THERMOSTAT:
                device.update(connected=False, data_source="unavailable")
            _LOGGER.warning(
                "Unexpected error reading cloud state for Zentraly device %s (%s)",
                _safe_device_reference(device.get("serial")),
                type(err).__name__,
            )
            continue

        device.update(state)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register Zentraly services once."""
    async def async_refresh_device(call: ServiceCall) -> None:
        """Refresh Zentraly data from the cloud snapshot."""
        device_id = call.data.get(ATTR_DEVICE_ID)

        if device_id:
            device_registry = dr.async_get(hass)
            device = device_registry.async_get(device_id)
            if device is None:
                raise HomeAssistantError(f"Unknown device_id: {device_id}")
            serials = {
                identifier
                for domain, identifier in device.identifiers
                if domain == DOMAIN
            }

            matching_entry_ids = [
                entry_id
                for entry_id in device.config_entries
                if entry_id in hass.data.get(DOMAIN, {})
            ]
            if not matching_entry_ids:
                raise HomeAssistantError("Device does not belong to Zentraly")

            for entry_id in matching_entry_ids:
                entry_data = hass.data[DOMAIN][entry_id]
                drafts: AdvancedDraftStore = entry_data["drafts"]
                for serial in serials:
                    drafts.clear(serial)
                coordinator = entry_data["coordinator"]
                await coordinator.async_request_refresh()
            return

        if not hass.data.get(DOMAIN):
            raise HomeAssistantError("No loaded Zentraly entries to refresh")

        for entry_data in hass.data.get(DOMAIN, {}).values():
            drafts: AdvancedDraftStore = entry_data["drafts"]
            drafts.clear_all()
            coordinator = entry_data["coordinator"]
            await coordinator.async_request_refresh()

    async def async_apply_thermostat_advanced_settings(call: ServiceCall) -> None:
        """Apply advanced ZTTIN01 settings from a service call."""
        try:
            entry_data, device = _device_from_service_call(
                hass,
                call,
                {DEVICE_TYPE_ZTTIN01_THERMOSTAT},
            )
            api: ZentralyApi = entry_data["api"]
            coordinator: DataUpdateCoordinator = entry_data["coordinator"]
            drafts: AdvancedDraftStore = entry_data["drafts"]

            dirty_keys = set(call.data) & THERMOSTAT_ADVANCED_KEYS
            values = {
                key: device.get(key)
                for key in THERMOSTAT_ADVANCED_KEYS
            }
            for key in dirty_keys:
                values[key] = call.data[key]
            if THERMOSTAT_DISPLAY_TYPE in dirty_keys:
                value = values[THERMOSTAT_DISPLAY_TYPE]
                if isinstance(value, str):
                    values[THERMOSTAT_DISPLAY_TYPE] = 0 if value == "horario" else 1

            result = await async_apply_thermostat_advanced(
                api,
                coordinator,
                drafts,
                device,
                values,
                dirty_keys,
            )
            if not result.wrote:
                return
        except ZentralyAuthError:
            coordinator.config_entry.async_start_reauth(coordinator.hass)
            raise ConfigEntryAuthFailed("Zentraly authentication required") from None

    async def async_apply_boiler_settings(call: ServiceCall) -> None:
        """Apply advanced boiler settings from a service call."""
        try:
            entry_data, device = _device_from_service_call(
                hass,
                call,
                BOILER_DEVICE_TYPES,
            )
            api: ZentralyApi = entry_data["api"]
            coordinator: DataUpdateCoordinator = entry_data["coordinator"]
            drafts: AdvancedDraftStore = entry_data["drafts"]

            dirty_keys = set(call.data) & BOILER_ADVANCED_KEYS
            values = {
                key: device.get(key)
                for key in BOILER_ADVANCED_KEYS
            }
            for key in dirty_keys:
                values[key] = call.data[key]
            if BOILER_WEATHER_TYPE in dirty_keys:
                value = values[BOILER_WEATHER_TYPE]
                if isinstance(value, str):
                    values[BOILER_WEATHER_TYPE] = 2 if value == "losa_radiante" else 3

            result = await async_apply_boiler_advanced(
                api,
                coordinator,
                drafts,
                device,
                values,
                dirty_keys,
            )
            if not result.wrote:
                return
        except ZentralyAuthError:
            coordinator.config_entry.async_start_reauth(coordinator.hass)
            raise ConfigEntryAuthFailed("Zentraly authentication required") from None

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_DEVICE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_DEVICE,
            async_refresh_device,
            schema=REFRESH_DEVICE_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_APPLY_THERMOSTAT_ADVANCED_SETTINGS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_APPLY_THERMOSTAT_ADVANCED_SETTINGS,
            async_apply_thermostat_advanced_settings,
            schema=APPLY_THERMOSTAT_ADVANCED_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_APPLY_BOILER_SETTINGS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_APPLY_BOILER_SETTINGS,
            async_apply_boiler_settings,
            schema=APPLY_BOILER_SCHEMA,
        )


def _device_from_service_call(
    hass: HomeAssistant,
    call: ServiceCall,
    allowed_device_types: set[int],
) -> tuple[dict[str, object], dict[str, object]]:
    """Return integration entry data and device data for a service call."""
    device_id = call.data.get(ATTR_DEVICE_ID)
    if not device_id:
        raise HomeAssistantError("device_id is required")

    device_registry = dr.async_get(hass)
    registry_device = device_registry.async_get(device_id)
    if registry_device is None:
        raise HomeAssistantError(f"Unknown device_id: {device_id}")

    serials = {
        identifier
        for domain, identifier in registry_device.identifiers
        if domain == DOMAIN
    }
    if not serials:
        raise HomeAssistantError("Device does not belong to Zentraly")

    for entry_id in registry_device.config_entries:
        entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
        if not entry_data:
            continue
        for device in entry_data["coordinator"].data or []:
            if device.get("serial") in serials:
                if device.get("device_type") not in allowed_device_types:
                    raise HomeAssistantError("Zentraly device type is not supported by this service")
                return entry_data, device

    raise HomeAssistantError("Zentraly device is not loaded")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_REFRESH_DEVICE)
            hass.services.async_remove(DOMAIN, SERVICE_APPLY_THERMOSTAT_ADVANCED_SETTINGS)
            hass.services.async_remove(DOMAIN, SERVICE_APPLY_BOILER_SETTINGS)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Discard a pending reauth handoff when its config entry is removed."""
    pending = hass.data.get(DATA_REAUTH_DRAFTS, {})
    pending.pop(entry.entry_id, None)
    if not pending:
        hass.data.pop(DATA_REAUTH_DRAFTS, None)
