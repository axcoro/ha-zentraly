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
    CMD_SET_CONFIG,
    CONFIG_IDS,
    DC_OPER_RUN_IOT,
    DEVICE_TYPE_THERMOSTAT,
    TEMP_SCALE,
    THERMOSTAT_MODE_MANUAL,
    THERMOSTAT_MODE_OFF,
    ZENTRALY_APP_VERSION,
)
from .local import ZentralyLocalClient, ZentralyLocalError

_LOGGER = logging.getLogger(__name__)


class ZentralyApiError(Exception):
    """Base exception for Zentraly API errors."""


class ZentralyAuthError(ZentralyApiError):
    """Authentication error."""


class ZentralyApi:
    """Zentraly API client."""

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        token: str | None = None,
        session: aiohttp.ClientSession | None = None,
        local_client: ZentralyLocalClient | None = None,
        user_id: int | None = None,
        firebase_token: str | None = None,
        device_guid: str | None = None,
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
        self._firebase_token = firebase_token or self._device_guid
        self._request_counter = 0
        self._command_rid = 0
        self._local_keys: dict[str, str] = {}

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
            "ivstrUserFBToken": self._firebase_token,
            "ivstrUserGuid": self._device_guid,
            "ivstrUserZtVersion": ZENTRALY_APP_VERSION,
            "ivnroUserMobileOS": 1,
            "ivstrUserMobileTrade": "HomeAssistant",
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

    def _get_next_command_rid(self) -> int:
        """Return the next device command request ID used by the official app."""
        rid = self._command_rid
        self._command_rid = (rid + 1) % 10000
        return rid

    async def authenticate(self) -> dict[str, Any]:
        """Authenticate and get token."""
        session = await self._get_session()

        async with session.get(
            f"{API_BASE_URL}{API_LOGIN_ENDPOINT}",
            headers=self._get_headers("login"),
        ) as response:
            if response.status != 200:
                raise ZentralyAuthError(f"Authentication failed: {response.status}")

            data = await response.json()

            if data.get("numStatus") != 0:
                raise ZentralyAuthError(f"Authentication failed: status {data.get('numStatus')}")

            io_data = data.get("ioData", {})
            self._token = io_data.get("ivstrToken")
            self._user_id = io_data.get("ioUser", {}).get("ioDCModel", {}).get("ivlngUser")

            if not self._token:
                raise ZentralyAuthError("No token received")

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
            if response.status in (401, 403):
                raise ZentralyAuthError("Zentraly session was rejected")
            if response.status != 200:
                raise ZentralyApiError(f"API error: {response.status}")

            data = await response.json()

            if data.get("numStatus") != 0:
                raise ZentralyApiError(f"API error: status {data.get('numStatus')}")

            return data

    async def get_devices(self) -> list[dict[str, Any]]:
        """Get list of thermostat devices."""
        data = await self.get_user_data()

        devices = []
        self._local_keys.clear()
        io_data = data.get("ioData", {})
        io_user = io_data.get("ioUser", {})
        ubications = io_user.get("coUbications", [])

        for ubication in ubications:
            ubication_name = ubication.get("ioDCModel", {}).get("ivstrUbicationName", "")
            zones = ubication.get("coZones", [])

            for zone in zones:
                zone_name = zone.get("ioDCModel", {}).get("ivstrZoneName", "")
                zone_devices = zone.get("coDevices", [])

                for device in zone_devices:
                    device_model = device.get("ioDCModel", {})
                    sub_type = device.get("ioSubTypeObj", {}).get("ioDCModel", {})
                    iot_hub_device_id = device_model.get("ivstrParentDeviceSerial")
                    local_key = device_model.get("ivstrParentDeviceBleKey")

                    if device_model.get("ivblnUseLocalConn") and iot_hub_device_id and local_key:
                        self._local_keys[iot_hub_device_id] = local_key

                    devices.append({
                        "serial": device_model.get("ivstrDeviceSerial"),
                        "iot_hub_device_id": iot_hub_device_id,
                        "name": device_model.get("ivstrDeviceName"),
                        "mac": device_model.get("ivstrDeviceMac"),
                        "connected": device_model.get("ivblnDeviceConnected", False),
                        "firmware": device_model.get("ivstrDeviceFWVersion"),
                        "device_type": device_model.get("ivnroDeviceType"),
                        "ubication": ubication_name,
                        "zone": zone_name,
                        "user_id": device_model.get("ivlngUser"),
                        "ubication_id": device_model.get("ivnumUbication"),
                        "zone_id": device_model.get("ivnumZone"),
                        "device_id": device_model.get("ivnumDevice"),
                        "endpoint_id": device_model.get("ivnumEndPoint"),
                        # Thermostat specific data
                        "current_temperature": sub_type.get("ivnumDeviceTemperature", 0) / TEMP_SCALE,
                        "target_temperature": sub_type.get("ivnumDeviceTargetTemperature", 0) / TEMP_SCALE,
                        "humidity": sub_type.get("ivnumDeviceHumedity", 0),
                        "mode": sub_type.get("ivnroDeviceMode", 1),
                        "is_on": sub_type.get("ivblnDeviceOn", False),
                        "is_locked": sub_type.get("ivblnDeviceLockMode", False),
                    })

        await asyncio.gather(
            *(
                self._refresh_device_config(
                    device,
                    self._local_keys.get(
                        device.get("iot_hub_device_id") or device.get("serial")
                    ),
                )
                for device in devices
                if device.get("device_type") == DEVICE_TYPE_THERMOSTAT
            )
        )

        return devices

    async def _refresh_device_config(
        self,
        device: dict[str, Any],
        local_key: str | None,
    ) -> None:
        """Replace stale app snapshot fields with live thermostat config."""
        device_serial = device.get("iot_hub_device_id") or device.get("serial")
        if not device_serial:
            return

        config: dict[str, Any] | None = None
        data_source = "cloud"
        if local_key:
            try:
                config = await self.get_local_device_config(device_serial, local_key)
                data_source = "local"
            except (ZentralyApiError, ZentralyLocalError, json.JSONDecodeError, TypeError) as err:
                _LOGGER.debug(
                    "Local config unavailable for %s; falling back to cloud: %s",
                    device.get("name") or device.get("serial"),
                    err,
                )

        try:
            if config is None:
                config = await self.get_device_config(device_serial)
        except ZentralyAuthError:
            raise
        except (ZentralyApiError, json.JSONDecodeError, TypeError) as err:
            device["connected"] = False
            _LOGGER.warning(
                "Could not refresh live config for %s: %s",
                device.get("name") or device.get("serial"),
                err,
            )
            return

        config_items = config.get("ids")
        if not isinstance(config_items, list):
            device["connected"] = False
            _LOGGER.warning(
                "Live config for %s did not include an ids list",
                device.get("name") or device.get("serial"),
            )
            return

        device["connected"] = True
        device["data_source"] = data_source
        for item in config_items:
            if not isinstance(item, dict):
                continue
            if "temperature" in item:
                device["current_temperature"] = item["temperature"] / TEMP_SCALE
            if "targetTemp" in item:
                device["target_temperature"] = item["targetTemp"] / TEMP_SCALE
            if "humidity" in item:
                device["humidity"] = item["humidity"]
            if "thermostatMode" in item:
                device["mode"] = item["thermostatMode"]
            if "output" in item:
                device["is_on"] = bool(item["output"])
            if "lock" in item:
                device["is_locked"] = bool(item["lock"])

    async def get_local_device_config(
        self,
        device_serial: str,
        key: str,
    ) -> dict[str, Any]:
        """Get the current configuration directly from the local thermostat hub."""
        if self._local_client is None:
            raise ZentralyLocalError("local transport is not configured")
        return await self._local_client.send_command(
            device_serial,
            key,
            CMD_GET_CONFIG,
            {"ids": CONFIG_IDS},
        )

    async def _send_command_prefer_local(
        self,
        device_serial: str,
        command: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        local_key = self._local_keys.get(device_serial)
        if local_key and self._local_client:
            try:
                return await self._local_client.send_command(
                    device_serial,
                    local_key,
                    command,
                    data,
                )
            except ZentralyLocalError as err:
                _LOGGER.debug(
                    "Local command unavailable for device; falling back to cloud: %s",
                    err,
                )
        return await self.send_iot_command(device_serial, command, data)

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
                "cmd": command,
                "rid": self._get_next_command_rid(),
                **data,
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
            if response.status in (401, 403):
                raise ZentralyAuthError("Zentraly session was rejected")
            if response.status != 200:
                raise ZentralyApiError(f"Command failed: {response.status}")

            result = await response.json()

            if result.get("numStatus") != 0:
                raise ZentralyApiError(f"Command failed: status {result.get('numStatus')}")

            # Parse the inner JSON response
            io_data = result.get("ioData", "{}")
            if isinstance(io_data, str):
                io_data = json.loads(io_data)

            device_status = io_data.get("status") if isinstance(io_data, dict) else None
            if device_status != 200:
                raise ZentralyApiError(
                    f"Command failed: device status {device_status}"
                )

            return io_data

    async def get_device_config(self, device_serial: str) -> dict[str, Any]:
        """Get device configuration through the cloud fallback."""
        return await self.send_iot_command(
            device_serial,
            CMD_GET_CONFIG,
            {"ids": CONFIG_IDS}
        )

    async def set_target_temperature(self, device_serial: str, temperature: float) -> dict[str, Any]:
        """Set target temperature."""
        temp_value = int(temperature * TEMP_SCALE)
        return await self._send_command_prefer_local(
            device_serial,
            CMD_SET_CONFIG,
            {"ids": [{"targetTemp": temp_value}]}
        )

    async def set_hvac_mode(self, device_serial: str, mode: int) -> dict[str, Any]:
        """Set HVAC mode."""
        return await self._send_command_prefer_local(
            device_serial,
            CMD_SET_CONFIG,
            {"ids": [{"thermostatMode": mode}]}
        )

    async def turn_on(self, device_serial: str) -> dict[str, Any]:
        """Turn thermostat on (heat mode)."""
        return await self.set_hvac_mode(device_serial, THERMOSTAT_MODE_MANUAL)

    async def turn_off(self, device_serial: str) -> dict[str, Any]:
        """Turn thermostat off."""
        return await self.set_hvac_mode(device_serial, THERMOSTAT_MODE_OFF)

    @property
    def token(self) -> str | None:
        """Get current token."""
        return self._token

    @property
    def user_id(self) -> int | None:
        """Get user ID."""
        return self._user_id
