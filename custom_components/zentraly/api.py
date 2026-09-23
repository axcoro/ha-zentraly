"""Zentraly API Client."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
import uuid
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import (
    API_BASE_URL,
    API_FIREBASE_IV,
    API_FIREBASE_KEY,
    API_LOGIN_ENDPOINT,
    API_APP_ENDPOINT,
    API_IOT_COMMAND_ENDPOINT,
    AUTH_PREFIX_LOGIN,
    AUTH_PREFIX_TOKEN,
    CMD_GET_CONFIG,
    CMD_READ_ATTR,
    CMD_SET_CONFIG,
    CMD_WRITE_ATTR,
    CONFIG_IDS,
    BOILER_RAW_ATTR_READS,
    DC_OPER_RUN_IOT,
    DEVICE_TYPE_BOILER,
    DEVICE_TYPE_THERMOSTAT,
    DEVICE_TYPE_ZTTIN01_THERMOSTAT,
    TEMP_SCALE,
    ZTTIN01_ATTR_CURRENT_TEMPERATURE,
    ZTTIN01_ATTR_HUMIDITY,
    ZTTIN01_ATTR_LOCK,
    ZTTIN01_ATTR_MODE,
    ZTTIN01_ATTR_SCHEDULE,
    ZTTIN01_ATTR_TARGET_TEMPERATURE,
    ZTTIN01_ATTR_TYPE_INT,
    ZTTIN01_CLUSTER_THERMOSTAT,
    ZTTIN01_COMMAND_TIMEOUT,
    ZTTIN01_DEFAULT_ENDPOINT,
    ZTTIN01_RAW_ATTR_READS,
    ZTTIN01_READ_ATTRS,
    ZENTRALY_APP_VERSION,
    ZTTWF_MODE_OFF,
    ZTTWF_MODE_MANUAL,
)
from .local import ZentralyLocalClient, ZentralyLocalDiscoveryError, ZentralyLocalError

_LOGGER = logging.getLogger(__name__)


class ZentralyApiError(Exception):
    """Base exception for Zentraly API errors."""


class ZentralyAuthError(ZentralyApiError):
    """Authentication error, optionally retaining fields already read in this operation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.confirmed_state: dict[str, Any] = {}


def command_device_id(device: dict[str, Any]) -> str:
    """Return the IoT Hub command target for a Zentraly device."""
    return (
        device.get("iot_hub_device_id")
        or device.get("parent_serial")
        or device["serial"]
    )


class ZentralyApi:
    """Zentraly API client."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        token: str | None = None,
        session: aiohttp.ClientSession | None = None,
        device_guid: str | None = None,
        user_id: int | None = None,
        firebase_token: str | None = None,
        local_client: ZentralyLocalClient | None = None,
    ) -> None:
        """Initialize the API client."""
        self._email = email
        self._password = password
        self._token = token
        self._session = session
        self._local_client = local_client
        self._user_id = user_id
        self._close_session = False
        self._device_guid = device_guid or str(uuid.uuid4()).upper()
        self._firebase_token = firebase_token
        self._request_counter = 0
        self._rid = 0
        self._local_keys: dict[str, str] = {}

    def _next_rid(self) -> int:
        """Return the next request id for IoT commands."""
        rid = self._rid
        self._rid = (rid + 1) % 10000
        return rid

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
            self._close_session = True
        return self._session

    async def close(self) -> None:
        """Close the session if we created it."""
        if self._close_session and self._session:
            await self._session.close()

    def _generate_firebase_header(self) -> str:
        """Generate Firebase header for API requests."""
        firebase_data = {
            # The app falls back to its device ID when Firebase is unavailable.
            "ivstrUserFBToken": self._device_guid if self._firebase_token is None else self._firebase_token,
            "ivstrUserGuid": self._device_guid,
            "ivstrUserZtVersion": ZENTRALY_APP_VERSION,
            "ivnroUserMobileOS": 1,
            "ivstrUserMobileTrade": "",
            "ivstrUserMobileModel": "Integration",
            "ivstrUserMobileOSVersion": "1.0",
            "ivstrUserLanguage": "es",
            "ivstrUserCountry": "AR",
        }
        encoded_data = base64.b64encode(
            json.dumps(firebase_data, separators=(",", ":")).encode()
        ).decode()

        self._request_counter += 1
        request_data = {
            "contador": self._request_counter,
            "random": secrets.randbelow(100000),
            "data": encoded_data,
            "timestamp": int(time.time() * 1000) + secrets.randbelow(20001) - 10000,
        }

        key = bytes.fromhex(API_FIREBASE_KEY)
        iv = bytes.fromhex(API_FIREBASE_IV)
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        plaintext = json.dumps(request_data, separators=(",", ":")).encode()
        padded_plaintext = padder.update(plaintext) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ciphertext = encryptor.update(padded_plaintext) + encryptor.finalize()
        return base64.b64encode(ciphertext).decode()

    def _get_headers(self, auth_type: str = "token") -> dict[str, str]:
        """Get request headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "zentralyRN/420",
            "Firebase": self._generate_firebase_header(),
        }

        if auth_type == "login":
            headers["Authorization"] = f"{AUTH_PREFIX_LOGIN}{self._email}:{self._password}"
        elif auth_type == "token" and self._token:
            headers["Authorization"] = f"{AUTH_PREFIX_TOKEN}{self._token}"

        return headers

    def session_data(self) -> dict[str, Any]:
        """Export a complete session, never credentials or a partial login."""
        firebase_token = self._device_guid if self._firebase_token is None else self._firebase_token
        if (
            not all(isinstance(value, str) and value.strip() for value in
                    (self._token, firebase_token, self._device_guid))
            or type(self._user_id) is not int
            or self._user_id <= 0
        ):
            raise ZentralyAuthError("Incomplete Zentraly session")
        return {
            "token": self._token,
            "user_id": self._user_id,
            "firebase_token": firebase_token,
            "device_guid": self._device_guid,
        }

    @staticmethod
    async def _response_data(response: Any, label: str) -> dict[str, Any]:
        """Classify HTTP errors and reject malformed envelopes without logging bodies."""
        if response.status in (401, 403):
            raise ZentralyAuthError(f"{label}: {response.status}")
        if response.status != 200:
            raise ZentralyApiError(f"{label}: {response.status}")
        try:
            data = await response.json()
        except (ValueError, aiohttp.ClientError):
            raise ZentralyApiError(f"{label}: invalid JSON") from None
        if not isinstance(data, dict) or type(data.get("numStatus")) is not int:
            raise ZentralyApiError(f"{label}: invalid response envelope")
        return data

    async def authenticate(self) -> dict[str, Any]:
        """Authenticate once; commit identity only after a complete valid login."""
        session = await self._get_session()
        async with session.get(
            f"{API_BASE_URL}{API_LOGIN_ENDPOINT}",
            headers=self._get_headers("login"),
        ) as response:
            data = await self._response_data(response, "Authentication failed")
            if data["numStatus"] != 0:
                raise ZentralyAuthError(
                    f"Authentication failed: status {data['numStatus']}"
                )
            io_data = data.get("ioData")
            if not isinstance(io_data, dict):
                raise ZentralyApiError("Authentication failed: invalid ioData")
            user = io_data.get("ioUser", {})
            model = user.get("ioDCModel", {}) if isinstance(user, dict) else None
            if not isinstance(model, dict):
                raise ZentralyApiError("Authentication failed: invalid user data")
            token = io_data.get("ivstrToken")
            user_id = model.get("ivlngUser")
            if (not isinstance(token, str) or not token.strip()
                    or type(user_id) is not int or user_id <= 0):
                raise ZentralyAuthError("Authentication returned an incomplete session")
            self._token, self._user_id = token, user_id
            return data

    async def get_user_data(self) -> dict[str, Any]:
        """Get all user data including devices."""
        if not self._token:
            await self.authenticate()

        session = await self._get_session()

        payload = {
            "coUbications": [],
            "eDcOper": 1,
            "ioDCModel": {
                "ivlngUser": self._user_id
            }
        }

        async with session.post(
            f"{API_BASE_URL}{API_APP_ENDPOINT}",
            headers=self._get_headers("token"),
            json=payload,
        ) as response:
            data = await self._response_data(response, "API error")
            if data["numStatus"] != 0:
                raise ZentralyApiError(f"API error: status {data['numStatus']}")
            if not isinstance(data.get("ioData"), dict):
                raise ZentralyApiError("API error: invalid ioData")
            return data

    async def get_devices(self) -> list[dict[str, Any]]:
        """Get list of Zentraly devices."""
        data = await self.get_user_data()

        devices = []
        local_keys: dict[str, str] = {}
        io_data = data.get("ioData", {})
        io_user = io_data.get("ioUser")
        if not isinstance(io_user, dict):
            raise ZentralyApiError("API error: malformed device inventory")
        ubications = io_user.get("coUbications")
        if not isinstance(ubications, list):
            raise ZentralyApiError("API error: malformed device inventory")

        for ubication in ubications:
            if not isinstance(ubication, dict) or not isinstance(ubication.get("ioDCModel", {}), dict):
                raise ZentralyApiError("API error: malformed device inventory")
            ubication_name = ubication.get("ioDCModel", {}).get("ivstrUbicationName", "")
            zones = ubication.get("coZones", [])
            if not isinstance(zones, list):
                raise ZentralyApiError("API error: malformed device inventory")

            for zone in zones:
                if not isinstance(zone, dict) or not isinstance(zone.get("ioDCModel", {}), dict):
                    raise ZentralyApiError("API error: malformed device inventory")
                zone_name = zone.get("ioDCModel", {}).get("ivstrZoneName", "")
                zone_devices = zone.get("coDevices", [])
                if not isinstance(zone_devices, list):
                    raise ZentralyApiError("API error: malformed device inventory")

                for device in zone_devices:
                    if not isinstance(device, dict):
                        raise ZentralyApiError("API error: malformed device inventory")
                    device_model = device.get("ioDCModel", {})
                    sub_object = device.get("ioSubTypeObj", {})
                    if not isinstance(device_model, dict) or not isinstance(sub_object, dict):
                        raise ZentralyApiError("API error: malformed device inventory")
                    sub_type = sub_object.get("ioDCModel", {})
                    if not isinstance(sub_type, dict):
                        raise ZentralyApiError("API error: malformed device inventory")
                    device_type = device_model.get("ivnroDeviceType")
                    local_target = device_model.get("ivstrParentDeviceSerial")
                    local_key = device_model.get("ivstrParentDeviceBleKey")
                    if (
                        device_model.get("ivblnUseLocalConn") is True
                        and isinstance(local_target, str) and local_target.strip()
                        and isinstance(local_key, str) and local_key.strip()
                    ):
                        local_keys[local_target] = local_key

                    device_data = {
                        "serial": device_model.get("ivstrDeviceSerial"),
                        "name": device_model.get("ivstrDeviceName"),
                        "mac": device_model.get("ivstrDeviceMac"),
                        "connected": device_model.get("ivblnDeviceConnected", False),
                        "firmware": device_model.get("ivstrDeviceFWVersion"),
                        "device_type": device_type,
                        "ubication": ubication_name,
                        "zone": zone_name,
                        "user_id": device_model.get("ivlngUser"),
                        "ubication_id": device_model.get("ivnumUbication"),
                        "zone_id": device_model.get("ivnumZone"),
                        "device_id": device_model.get("ivnumDevice"),
                        "endpoint_id": device_model.get("ivnumEndPoint"),
                        "parent_serial": device_model.get("ivstrParentDeviceSerial"),
                        "iot_hub_device_id": device_model.get(
                            "ivstrParentDeviceSerial"
                        ),
                    }

                    if device_type == DEVICE_TYPE_BOILER:
                        device_data.update({
                            "boiler_heating_temperature": self._scaled_value(
                                sub_type,
                                "ivnumDeviceTemperatureHeating",
                            ),
                            "boiler_h2o_temperature": self._scaled_value(
                                sub_type,
                                "ivnumDeviceTemperatureH2O",
                            ),
                            "heat_demand": sub_type.get("ivblnDeviceOn"),
                            "is_forced_on": sub_type.get("ivblnDeviceForcedOn"),
                            "is_h2o_enabled": sub_type.get("ivblnDeviceTemperatureH2O"),
                            "is_comfort_mode": sub_type.get("ivblnDeviceConfortMode"),
                            "is_opentherm_on": sub_type.get("ivblnDeviceOtOn"),
                            "weather_type": sub_type.get("ivnroDeviceWeatherType"),
                            "boiler_model": sub_type.get("ivnroBoilerModel"),
                            "boiler_trade": sub_type.get("ivnroBoilerTrade"),
                            "boiler_message": sub_type.get("ivnroDeviceMessage"),
                            "on_delay": self._seconds_to_minutes(
                                sub_type,
                                "ivnumDeviceOnDelay",
                            ),
                            "off_delay": self._seconds_to_minutes(
                                sub_type,
                                "ivnumDeviceOffDelay",
                            ),
                        })
                    else:
                        device_data.update({
                            "current_temperature": self._scaled_value(
                                sub_type,
                                "ivnumDeviceTemperature",
                            ),
                            "target_temperature": self._scaled_value(
                                sub_type,
                                "ivnumDeviceTargetTemperature",
                            ),
                            "humidity": sub_type.get("ivnumDeviceHumedity"),
                            "mode": sub_type.get("ivnroDeviceMode"),
                            "schedule": sub_type.get("ivstrDeviceCronoHexa"),
                            **(
                                {"heat_demand": sub_type.get("ivblnDeviceOn")}
                                if device_type == DEVICE_TYPE_ZTTIN01_THERMOSTAT
                                else {"is_on": sub_type.get("ivblnDeviceOn")}
                            ),
                            "is_locked": sub_type.get("ivblnDeviceLockMode"),
                            "temperature_offset": self._scaled_value(
                                sub_type,
                                "ivnumDeviceTemperatureOffset",
                            ),
                            "away_temperature": self._scaled_value(
                                sub_type,
                                "ivnumDeviceTemperatureAway",
                            ),
                            "display_brightness": sub_type.get("ivnroDeviceBrightness"),
                            "display_always_on": sub_type.get("ivblnDeviceAlwaysOn"),
                            "display_type": sub_type.get("ivnroDeviceTypeScreen"),
                        })

                    devices.append(device_data)

        # Commit the account's key map only after validating the entire inventory.
        self._local_keys = local_keys
        await asyncio.gather(*(
            self._refresh_device_config(device)
            for device in devices
            if device.get("device_type") == DEVICE_TYPE_THERMOSTAT
        ))
        return devices

    async def _refresh_device_config(self, device: dict[str, Any]) -> None:
        """Replace the app snapshot with validated live type-2 thermostat state."""
        from .zttwf import read_zttwf_state

        try:
            state = await read_zttwf_state(self, device)
        except ZentralyAuthError:
            raise
        except (ZentralyApiError, aiohttp.ClientError, TimeoutError, OSError):
            device.update(connected=False, data_source="unavailable")
            _LOGGER.debug("Live thermostat config unavailable")
            return
        device.update(state, connected=True, data_source=state.get("data_source", "cloud"))

    async def get_local_device_config(
        self, device_serial: str, key: str,
    ) -> dict[str, Any]:
        """Read configuration directly from the local thermostat hub."""
        if self._local_client is None:
            raise ZentralyApiError("Local transport is not configured")
        try:
            return await self._local_client.send_command(
                device_serial, key, CMD_GET_CONFIG, {"ids": CONFIG_IDS},
            )
        except (ZentralyLocalError, aiohttp.ClientError, TimeoutError, OSError,
                ValueError, TypeError):
            raise ZentralyApiError("Local configuration read failed") from None

    async def _send_command_prefer_local(
        self, device_serial: str, command: str, data: dict[str, Any],
    ) -> dict[str, Any]:
        """Choose one write transport before sending; never replay a failed write."""
        local_key = self._local_keys.get(device_serial)
        if local_key and self._local_client is not None:
            try:
                return await self._local_client.send_command(
                    device_serial, local_key, command, data,
                )
            except ZentralyLocalDiscoveryError:
                # Discovery finished before any connection or device write.
                # A connection/send failure below must never select cloud.
                pass
            except (ZentralyLocalError, aiohttp.ClientError, TimeoutError, OSError,
                    ValueError, TypeError):
                raise ZentralyApiError("Local device command failed") from None
        return await self.send_iot_command(device_serial, command, data)

    def _scaled_value(self, source: dict[str, Any], key: str) -> float | None:
        """Return a centesimal API value scaled to a regular number."""
        value = source.get(key)
        if value is None:
            return None

        try:
            return float(value) / TEMP_SCALE
        except (TypeError, ValueError):
            return None

    def _seconds_to_minutes(self, source: dict[str, Any], key: str) -> float | None:
        """Return an API seconds value converted to minutes."""
        value = source.get(key)
        if value is None:
            return None

        try:
            return float(value) / 60
        except (TypeError, ValueError):
            return None

    async def send_iot_command(
        self,
        device_serial: str,
        command: str,
        data: dict[str, Any],
        timeout: int = 15000,
    ) -> dict[str, Any]:
        """Send IoT command to device."""
        if not self._token:
            await self.authenticate()

        session = await self._get_session()

        command_payload = {
            "deviceId": device_serial,
            "timeOut": timeout,
            "data": {
                **data,
                "cmd": command,
                "rid": self._next_rid(),
            }
        }
        payload = {
            "eDcOper": DC_OPER_RUN_IOT,
            "vioBody": command_payload,
        }

        async with session.post(
            f"{API_BASE_URL}{API_IOT_COMMAND_ENDPOINT}",
            headers=self._get_headers("token"),
            json=payload,
        ) as response:
            result = await self._response_data(response, "Command failed")

            if result.get("numStatus") != 0:
                raise ZentralyApiError(
                    f"Command failed: status {result.get('numStatus')}"
                )

            # Parse the inner JSON response
            io_data = result.get("ioData", "{}")
            if isinstance(io_data, str):
                try:
                    io_data = json.loads(io_data)
                except json.JSONDecodeError:
                    raise ZentralyApiError("Command returned invalid ioData") from None

            if not isinstance(io_data, dict):
                raise ZentralyApiError("Command returned an invalid ioData response")

            status = io_data.get("status")
            # Numeric strings are accepted by the existing Action contract; do not
            # coerce booleans, fractional values or arbitrary server text.
            numeric_status = status
            if isinstance(status, str) and status.isascii() and status.isdigit():
                numeric_status = int(status) if len(status) <= 6 else None
            if type(numeric_status) is not int or numeric_status != 200:
                safe_status = status if type(status) is int or status is None else "invalid"
                raise ZentralyApiError(f"Command failed: device status {safe_status}")

            return io_data

    async def send_write_attr_command(
        self,
        device_serial: str,
        device_mac: str | None,
        cluster: int,
        endpoint_id: int | None,
        attrs: list[dict[str, Any]],
        timeout: int = ZTTIN01_COMMAND_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a writeAttr command to a device."""
        if not device_mac:
            raise ZentralyApiError("Device MAC is required for writeAttr commands")

        return await self.send_iot_command(
            device_serial,
            CMD_WRITE_ATTR,
            {
                "mac": device_mac,
                "cluster": cluster,
                "ep": endpoint_id or ZTTIN01_DEFAULT_ENDPOINT,
                "attrs": attrs,
            },
            timeout=timeout,
        )

    async def send_read_attr_command(
        self,
        device_serial: str,
        device_mac: str | None,
        cluster: int,
        endpoint_id: int | None,
        attrs: list[dict[str, Any]],
        timeout: int = ZTTIN01_COMMAND_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a readAttr command to a device."""
        if not device_mac:
            raise ZentralyApiError("Device MAC is required for readAttr commands")

        return await self.send_iot_command(
            device_serial,
            CMD_READ_ATTR,
            {
                "mac": device_mac,
                "cluster": cluster,
                "ep": endpoint_id or ZTTIN01_DEFAULT_ENDPOINT,
                "attrs": attrs,
            },
            timeout=timeout,
        )

    async def get_device_config(self, device_serial: str) -> dict[str, Any]:
        """Read validated config locally, with a cloud fallback for reads only."""
        from .zttwf import parse_zttwf_config

        local_key = self._local_keys.get(device_serial)
        if local_key and self._local_client is not None:
            try:
                config = await self.get_local_device_config(device_serial, local_key)
                parse_zttwf_config(config)
                return {**config, "data_source": "local"}
            except ZentralyApiError:
                _LOGGER.debug("Local config unavailable; falling back to cloud read")
        config = await self.send_iot_command(
            device_serial, CMD_GET_CONFIG, {"ids": CONFIG_IDS},
        )
        parse_zttwf_config(config)
        return {**config, "data_source": "cloud"}

    async def set_target_temperature(self, device_serial: str, temperature: float) -> dict[str, Any]:
        """Set target temperature."""
        temp_value = round(temperature * TEMP_SCALE)
        return await self._send_command_prefer_local(
            device_serial,
            CMD_SET_CONFIG,
            {"ids": [{"targetTemp": temp_value}]}
        )

    async def set_zttin01_target_temperature(
        self,
        device_serial: str,
        device_mac: str | None,
        endpoint_id: int | None,
        temperature: float,
    ) -> dict[str, Any]:
        """Set target temperature for a ZTTIN01 thermostat."""
        temp_value = round(temperature * TEMP_SCALE)
        return await self.send_write_attr_command(
            device_serial,
            device_mac,
            ZTTIN01_CLUSTER_THERMOSTAT,
            endpoint_id,
            [
                {
                    "id": ZTTIN01_ATTR_TARGET_TEMPERATURE,
                    "type": ZTTIN01_ATTR_TYPE_INT,
                    "val": temp_value,
                }
            ],
        )

    async def set_hvac_mode(self, device_serial: str, mode: int) -> dict[str, Any]:
        """Set HVAC mode."""
        return await self._send_command_prefer_local(
            device_serial,
            CMD_SET_CONFIG,
            {"ids": [{"thermostatMode": mode}]}
        )

    async def set_zttin01_mode(
        self,
        device_serial: str,
        device_mac: str | None,
        endpoint_id: int | None,
        mode: int,
    ) -> dict[str, Any]:
        """Set mode for a ZTTIN01 thermostat."""
        return await self.send_write_attr_command(
            device_serial,
            device_mac,
            ZTTIN01_CLUSTER_THERMOSTAT,
            endpoint_id,
            [
                {
                    "id": ZTTIN01_ATTR_MODE,
                    "type": ZTTIN01_ATTR_TYPE_INT,
                    "val": mode,
                }
            ],
        )

    async def set_zttin01_lock(
        self,
        device_serial: str,
        device_mac: str | None,
        endpoint_id: int | None,
        locked: bool,
    ) -> dict[str, Any]:
        """Set lock mode for a ZTTIN01 thermostat."""
        return await self.send_write_attr_command(
            device_serial,
            device_mac,
            ZTTIN01_CLUSTER_THERMOSTAT,
            endpoint_id,
            [
                {
                    "id": ZTTIN01_ATTR_LOCK,
                    "type": ZTTIN01_ATTR_TYPE_INT,
                    "val": 1 if locked else 0,
                }
            ],
        )

    async def read_zttin01_thermostat_state(
        self,
        device_serial: str,
        device_mac: str | None,
        endpoint_id: int | None,
    ) -> dict[str, Any]:
        """Read effective state from a ZTTIN01 thermostat."""
        response = await self.send_read_attr_command(
            device_serial,
            device_mac,
            ZTTIN01_CLUSTER_THERMOSTAT,
            endpoint_id,
            ZTTIN01_READ_ATTRS,
        )
        return self._parse_zttin01_read_attr_response(response)

    async def read_zttin01_raw_attrs(
        self,
        device_serial: str,
        device_mac: str | None,
        endpoint_id: int | None,
    ) -> dict[str, Any]:
        """Read raw diagnostic attributes from a ZTTIN01 thermostat."""
        return await self.read_raw_attrs(
            device_serial,
            device_mac,
            endpoint_id,
            ZTTIN01_RAW_ATTR_READS,
        )

    async def read_boiler_raw_attrs(
        self,
        parent_device_serial: str,
        boiler_mac: str | None,
        endpoint_id: int | None,
    ) -> dict[str, Any]:
        """Read raw diagnostic attributes from a boiler extension."""
        return await self.read_raw_attrs(
            parent_device_serial,
            boiler_mac,
            endpoint_id,
            BOILER_RAW_ATTR_READS,
        )

    async def read_boiler_advanced_state(
        self,
        parent_device_serial: str,
        boiler_mac: str | None,
        endpoint_id: int | None,
        cluster_reads: dict[int, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Read selected boiler attributes as normalized configuration state."""
        return await self.read_raw_attrs(
            parent_device_serial,
            boiler_mac,
            endpoint_id,
            cluster_reads,
        )

    async def read_raw_attrs(
        self,
        device_serial: str,
        device_mac: str | None,
        endpoint_id: int | None,
        cluster_reads: dict[int, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Read raw attributes grouped by cluster and return flat state keys."""
        state: dict[str, Any] = {}

        for cluster, attrs in cluster_reads.items():
            try:
                response = await self.send_read_attr_command(
                    device_serial,
                    device_mac,
                    cluster,
                    endpoint_id,
                    attrs,
                )
            except ZentralyAuthError as err:
                err.confirmed_state = {**state, **err.confirmed_state}
                raise
            state.update(self._parse_raw_read_attr_response(cluster, response))

        return state

    def _parse_raw_read_attr_response(
        self,
        cluster: int,
        response: Any,
    ) -> dict[str, Any]:
        """Parse readAttr output into raw diagnostic state keys."""
        state: dict[str, Any] = {}

        for attr in self._extract_attrs(response):
            if not isinstance(attr, dict) or "id" not in attr:
                continue
            try:
                attr_id = int(attr.get("id"))
            except (TypeError, ValueError):
                continue

            value = self._attr_value(attr)
            if value is None:
                continue

            if cluster == 65006 and attr_id == 0:
                if (numeric_value := self._numeric_attr_value(attr)) is not None:
                    state["heat_demand"] = bool(numeric_value)
                continue

            if cluster == 65006 and attr_id == 13000:
                if (numeric_value := self._numeric_attr_value(attr)) is not None:
                    state["heating_demand_time_today"] = numeric_value / 3600
                continue

            state[f"raw_attr_{cluster}_{attr_id}"] = value
            self._add_boiler_semantic_attr(state, cluster, attr_id, attr)

        return state

    def _add_boiler_semantic_attr(
        self,
        state: dict[str, Any],
        cluster: int,
        attr_id: int,
        attr: dict[str, Any],
    ) -> None:
        """Add normalized boiler configuration fields for known raw attributes."""
        numeric_value = self._numeric_attr_value(attr)
        if numeric_value is None:
            return

        if cluster == 65535:
            if attr_id == 1:
                state["boiler_heating_temperature"] = numeric_value / TEMP_SCALE
            elif attr_id == 56:
                state["boiler_h2o_temperature"] = numeric_value / TEMP_SCALE
            elif attr_id == 1056:
                state["is_h2o_enabled"] = bool(numeric_value)
            elif attr_id == 1001:
                state["weather_type"] = int(numeric_value)
            elif attr_id == 10001:
                state["is_comfort_mode"] = bool(numeric_value)
        elif cluster == 65006:
            if attr_id == 2:
                state["is_forced_on"] = bool(numeric_value)
            elif attr_id == 10:
                state["on_delay"] = numeric_value / 60

    def _parse_zttin01_read_attr_response(self, response: Any) -> dict[str, Any]:
        """Parse readAttr output into Home Assistant device fields."""
        attrs = self._extract_attrs(response)
        if not attrs:
            return {}

        by_id = {}
        for attr in attrs:
            if isinstance(attr, dict) and "id" in attr:
                try:
                    attr_id = int(attr.get("id"))
                except (TypeError, ValueError):
                    continue
                by_id[attr_id] = attr

        state: dict[str, Any] = {}

        if (attr := by_id.get(ZTTIN01_ATTR_CURRENT_TEMPERATURE)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["current_temperature"] = value / TEMP_SCALE

        if (attr := by_id.get(ZTTIN01_ATTR_HUMIDITY)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["humidity"] = int(value)

        if (attr := by_id.get(ZTTIN01_ATTR_TARGET_TEMPERATURE)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["target_temperature"] = value / TEMP_SCALE

        if (attr := by_id.get(ZTTIN01_ATTR_MODE)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["mode"] = int(value)

        if (attr := by_id.get(ZTTIN01_ATTR_LOCK)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["is_locked"] = bool(value)

        if (attr := by_id.get(ZTTIN01_ATTR_SCHEDULE)) is not None:
            if (value := self._attr_value(attr)) is not None:
                state["schedule"] = value

        if (attr := by_id.get(16)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["temperature_offset"] = value / TEMP_SCALE

        if (attr := by_id.get(17)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["away_temperature"] = value / TEMP_SCALE

        if (attr := by_id.get(100)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["display_brightness"] = int(value)

        if (attr := by_id.get(101)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["display_always_on"] = bool(value)

        if (attr := by_id.get(102)) is not None:
            if (value := self._numeric_attr_value(attr)) is not None:
                state["display_type"] = int(value)

        return state

    def _extract_attrs(self, value: Any) -> list[dict[str, Any]]:
        """Extract attrs from common readAttr response shapes."""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []

        if isinstance(value, list):
            if all(isinstance(item, dict) and "id" in item for item in value):
                return value

            attrs = []
            for item in value:
                attrs.extend(self._extract_attrs(item))
            return attrs

        if isinstance(value, dict):
            if isinstance(value.get("attrs"), list):
                return value["attrs"]

            attrs = []
            for nested_key in ("data", "ioData", "response", "result"):
                if nested_key in value:
                    attrs.extend(self._extract_attrs(value[nested_key]))
            return attrs

        return []

    def _attr_value(self, attr: dict[str, Any]) -> Any:
        """Return the value field from a readAttr attr."""
        for key in ("val", "value", "v", "data"):
            if key in attr:
                return attr[key]
        return None

    def _numeric_attr_value(self, attr: dict[str, Any]) -> float | None:
        """Return an attr value as a number when possible."""
        value = self._attr_value(attr)
        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def turn_on(self, device_serial: str) -> dict[str, Any]:
        """Turn thermostat on (heat mode)."""
        return await self.set_hvac_mode(device_serial, ZTTWF_MODE_MANUAL)

    async def turn_off(self, device_serial: str) -> dict[str, Any]:
        """Turn thermostat off."""
        return await self.set_hvac_mode(device_serial, ZTTWF_MODE_OFF)

    @property
    def token(self) -> str | None:
        """Get current token."""
        return self._token

    @property
    def user_id(self) -> int | None:
        """Get user ID."""
        return self._user_id
