"""Tests for the Zentraly API client."""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PATH = ROOT / "custom_components" / "zentraly"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(ROOT / "custom_components")]
sys.modules.setdefault("custom_components", custom_components)

zentraly_package = types.ModuleType("custom_components.zentraly")
zentraly_package.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.zentraly", zentraly_package)

aiohttp = types.ModuleType("aiohttp")
aiohttp.ClientSession = object
aiohttp.ClientWebSocketResponse = object
aiohttp.ClientError = type("ClientError", (Exception,), {})
aiohttp.WSMsgType = types.SimpleNamespace(
    TEXT="text",
    BINARY="binary",
    CLOSE="close",
    CLOSED="closed",
    ERROR="error",
)
sys.modules.setdefault("aiohttp", aiohttp)

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)

homeassistant_const = types.ModuleType("homeassistant.const")
homeassistant_const.Platform = types.SimpleNamespace(
    CLIMATE="climate", SENSOR="sensor", BINARY_SENSOR="binary_sensor",
    NUMBER="number", SELECT="select", LOCK="lock", BUTTON="button",
)
sys.modules.setdefault("homeassistant.const", homeassistant_const)

_load_module("custom_components.zentraly.const", PACKAGE_PATH / "const.py")
api_module = _load_module("custom_components.zentraly.api", PACKAGE_PATH / "api.py")
local_module = sys.modules["custom_components.zentraly.local"]

ZentralyApi = api_module.ZentralyApi
ZentralyApiError = api_module.ZentralyApiError
ZentralyLocalClient = local_module.ZentralyLocalClient
const_module = sys.modules["custom_components.zentraly.const"]


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(self, status: int, data: dict) -> None:
        self.status = status
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def json(self) -> dict:
        return self._data


class FakeSession:
    """Record requests and return queued responses."""

    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.requests.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)

    def get(self, url: str, *, headers: dict) -> FakeResponse:
        self.requests.append({"url": url, "headers": headers})
        return self.responses.pop(0)


class FakeWebSocket:
    """Minimal WebSocket context manager with queued messages."""

    def __init__(self, *messages: dict) -> None:
        self.messages = [
            types.SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(message))
            for message in messages
        ]
        self.sent: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def receive(self):
        return self.messages.pop(0)


class FakeWebSocketSession:
    """Record the local WebSocket URL."""

    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket
        self.url: str | None = None
        self.timeout: int | None = None

    def ws_connect(self, url: str, *, timeout: int) -> FakeWebSocket:
        self.url = url
        self.timeout = timeout
        return self.websocket


class ZentralyApiTests(unittest.IsolatedAsyncioTestCase):
    """Verify the reverse-engineered API contract."""

    async def test_login_reuses_installation_identity_after_client_recreation(self) -> None:
        for _ in range(2):
            session = FakeSession(FakeResponse(200, {
                "numStatus": 0,
                "ioData": {"ivstrToken": "synthetic-token", "ioUser": {"ioDCModel": {"ivlngUser": 42}}},
            }))
            api = ZentralyApi(email="test@example.invalid", password="synthetic-password",
                              device_guid="TEST-GUID", session=session)
            await api.authenticate()
            decryptor = Cipher(algorithms.AES(bytes.fromhex(const_module.API_FIREBASE_KEY)),
                               modes.CBC(bytes.fromhex(const_module.API_FIREBASE_IV))).decryptor()
            padded = decryptor.update(base64.b64decode(session.requests[0]["headers"]["Firebase"])) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            envelope = json.loads(unpadder.update(padded) + unpadder.finalize())
            metadata = json.loads(base64.b64decode(envelope["data"]))
            self.assertEqual("TEST-GUID", metadata["ivstrUserGuid"])
            self.assertEqual("TEST-GUID", metadata["ivstrUserFBToken"])

    def test_firebase_header_matches_current_encrypted_contract(self) -> None:
        api = ZentralyApi(firebase_token="synthetic-firebase-token", device_guid="TEST-GUID")

        with (
            patch.object(api_module.time, "time", return_value=1_750_000_000.0),
            patch.object(api_module.secrets, "randbelow", side_effect=[12345, 6790]),
        ):
            header = api._generate_firebase_header()

        decryptor = Cipher(
            algorithms.AES(bytes.fromhex(const_module.API_FIREBASE_KEY)),
            modes.CBC(bytes.fromhex(const_module.API_FIREBASE_IV)),
        ).decryptor()
        padded_plaintext = decryptor.update(base64.b64decode(header)) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()
        encrypted_envelope = json.loads(plaintext)
        firebase_data = json.loads(base64.b64decode(encrypted_envelope["data"]))

        self.assertEqual(1, encrypted_envelope["contador"])
        self.assertEqual(12345, encrypted_envelope["random"])
        self.assertEqual(1_749_999_996_790, encrypted_envelope["timestamp"])
        self.assertEqual("TEST-GUID", firebase_data["ivstrUserGuid"])
        self.assertEqual("synthetic-firebase-token", firebase_data["ivstrUserFBToken"])
        self.assertEqual("", firebase_data["ivstrUserMobileTrade"])
        self.assertEqual("Integration", firebase_data["ivstrUserMobileModel"])
        self.assertEqual("7.2.0", firebase_data["ivstrUserZtVersion"])
        self.assertEqual("AR", firebase_data["ivstrUserCountry"])

    async def test_local_client_logs_in_and_reads_wrapped_config(self) -> None:
        websocket = FakeWebSocket(
            {"status": 200, "rid": 0},
            {
                "status": 200,
                "rid": 1,
                "data": json.dumps({"ids": [{"targetTemp": 2300}]}),
            },
        )
        session = FakeWebSocketSession(websocket)
        resolver = AsyncMock(return_value=("192.168.40.25", 80))
        client = ZentralyLocalClient(session, resolver)

        result = await client.send_command(
            "iot-hub-456",
            "local-key",
            "getConfig",
            {"ids": ["targetTemp"]},
        )

        self.assertEqual([{"targetTemp": 2300}], result["ids"])
        self.assertEqual("ws://192.168.40.25:80/ws", session.url)
        self.assertEqual(
            [
                {"cmd": "login", "rid": 0, "key": "local-key"},
                {"cmd": "getConfig", "rid": 1, "ids": ["targetTemp"]},
            ],
            websocket.sent,
        )
        resolver.assert_awaited_once_with("iot-hub-456")

    async def test_write_prefers_local_transport_after_inventory_refresh(self) -> None:
        local_client = types.SimpleNamespace(
            send_command=AsyncMock(return_value={"status": 200})
        )
        api = ZentralyApi(token="test-token", session=FakeSession(), local_client=local_client)
        api._local_keys["iot-hub-456"] = "local-key"

        result = await api.set_target_temperature("iot-hub-456", 21.5)

        self.assertEqual({"status": 200}, result)
        local_client.send_command.assert_awaited_once_with(
            "iot-hub-456",
            "local-key",
            "setConfig",
            {"ids": [{"targetTemp": 2150}]},
        )

    def test_firebase_counter_increments_per_request(self) -> None:
        api = ZentralyApi()

        with patch.object(api_module.secrets, "randbelow", return_value=0):
            first_header = api._generate_firebase_header()
            second_header = api._generate_firebase_header()

        self.assertNotEqual(first_header, second_header)
        self.assertEqual(2, api._request_counter)

    async def test_send_iot_command_uses_app_action_envelope(self) -> None:
        session = FakeSession(
            FakeResponse(200, {"numStatus": 0, "ioData": '{"status":200}'})
        )
        api = ZentralyApi(token="test-token", session=session)

        result = await api.send_iot_command(
            "serial-123",
            "setConfig",
            {"ids": [{"targetTemp": 2150}]},
        )

        self.assertEqual({"status": 200}, result)
        self.assertEqual(1, len(session.requests))
        request = session.requests[0]
        self.assertEqual(
            "https://ztprdrestservicesv2.azurewebsites.net/app/Action",
            request["url"],
        )
        self.assertEqual(
            {
                "eDcOper": 28,
                "vioBody": {
                    "deviceId": "serial-123",
                    "timeOut": 15000,
                    "data": {
                        "cmd": "setConfig",
                        "rid": 0,
                        "ids": [{"targetTemp": 2150}],
                    },
                },
            },
            request["json"],
        )

    async def test_send_iot_command_reports_http_errors(self) -> None:
        session = FakeSession(FakeResponse(405, {}))
        api = ZentralyApi(token="test-token", session=session)

        with self.assertRaisesRegex(ZentralyApiError, "Command failed: 405"):
            await api.send_iot_command("serial-123", "getConfig", {"ids": []})

    async def test_power_commands_use_wifi_manual_and_off_modes(self) -> None:
        session = FakeSession(*[FakeResponse(200, {"numStatus": 0, "ioData": {"status": 200}}) for _ in range(2)])
        api = ZentralyApi(token="synthetic-token", session=session)
        await api.turn_on("thermostat-123")
        await api.turn_off("thermostat-123")
        self.assertEqual([2, 0], [r["json"]["vioBody"]["data"]["ids"][0]["thermostatMode"] for r in session.requests])

    async def test_local_access_disabled_skips_lan_and_clears_cached_key(self) -> None:
        api = ZentralyApi(token="synthetic-token")
        api._local_keys["thermostat-123"] = "old-local-key"
        api.get_user_data = AsyncMock(return_value={"ioData": {"ioUser": {"coUbications": [{"coZones": [{"coDevices": [{
            "ioDCModel": {"ivstrDeviceSerial": "thermostat-123", "ivstrParentDeviceSerial": "thermostat-123", "ivnroDeviceType": 2,
                          "ivstrParentDeviceBleKey": "local-key", "ivblnUseLocalConn": False}
        }]}]}]}}})
        api.get_local_device_config = AsyncMock(return_value={"ids": []})
        api.get_device_config = AsyncMock(return_value={"status": 200, "ids": [
            {"targetTemp": 2300}, {"temperature": 2200}, {"thermostatMode": 4}, {"output": 1}]})
        devices = await api.get_devices()
        api.get_local_device_config.assert_not_awaited()
        api.get_device_config.assert_awaited_once_with("thermostat-123")
        self.assertNotIn("thermostat-123", api._local_keys)
        self.assertEqual(4, devices[0]["mode"])
        self.assertTrue(devices[0]["is_on"])

    async def test_send_iot_command_reports_device_errors(self) -> None:
        session = FakeSession(
            FakeResponse(200, {"numStatus": 0, "ioData": '{"status":404}'})
        )
        api = ZentralyApi(token="test-token", session=session)

        with self.assertRaisesRegex(ZentralyApiError, "device status 404"):
            await api.send_iot_command("serial-123", "getConfig", {"ids": []})

    async def test_send_iot_command_increments_device_request_id(self) -> None:
        session = FakeSession(
            FakeResponse(200, {"numStatus": 0, "ioData": '{"status":200}'}),
            FakeResponse(200, {"numStatus": 0, "ioData": '{"status":200}'}),
        )
        api = ZentralyApi(token="test-token", session=session)

        await api.send_iot_command("serial-123", "getConfig", {"ids": []})
        await api.send_iot_command("serial-123", "getConfig", {"ids": []})

        self.assertEqual(
            [0, 1],
            [request["json"]["vioBody"]["data"]["rid"] for request in session.requests],
        )

    async def test_get_devices_exposes_parent_iot_hub_device_id(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "numStatus": 0,
                    "ioData": {
                        "ioUser": {
                            "coUbications": [
                                {
                                    "ioDCModel": {"ivstrUbicationName": "Casa"},
                                    "coZones": [
                                        {
                                            "ioDCModel": {"ivstrZoneName": "PA"},
                                            "coDevices": [
                                                {
                                                    "ioDCModel": {
                                                        "ivstrDeviceSerial": "thermostat-123",
                                                        "ivstrParentDeviceSerial": "iot-hub-456",
                                                    },
                                                    "ioSubTypeObj": {"ioDCModel": {}},
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                },
            )
        )
        api = ZentralyApi(token="test-token", session=session)
        api._user_id = 42

        devices = await api.get_devices()

        self.assertEqual("thermostat-123", devices[0]["serial"])
        self.assertEqual("iot-hub-456", devices[0]["iot_hub_device_id"])

    async def test_get_devices_prefers_local_config_over_stale_cloud_snapshot(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "numStatus": 0,
                    "ioData": {
                        "ioUser": {
                            "coUbications": [
                                {
                                    "ioDCModel": {"ivstrUbicationName": "Casa"},
                                    "coZones": [
                                        {
                                            "ioDCModel": {"ivstrZoneName": "PB"},
                                            "coDevices": [
                                                {
                                                    "ioDCModel": {
                                                        "ivstrDeviceSerial": "thermostat-123",
                                                        "ivstrParentDeviceSerial": "iot-hub-456",
                                                        "ivstrParentDeviceBleKey": "local-key",
                                                        "ivblnUseLocalConn": True,
                                                        "ivnroDeviceType": 2,
                                                        "ivblnDeviceConnected": True,
                                                    },
                                                    "ioSubTypeObj": {
                                                        "ioDCModel": {
                                                            "ivnumDeviceTemperature": 2200,
                                                            "ivnumDeviceTargetTemperature": 2100,
                                                            "ivnumDeviceHumedity": 45,
                                                            "ivnroDeviceMode": 1,
                                                            "ivblnDeviceOn": False,
                                                        }
                                                    },
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                },
            ),
        )
        api = ZentralyApi(token="test-token", session=session, local_client=object())
        api._user_id = 42
        api.get_local_device_config = AsyncMock(
            return_value={
                "status": 200,
                "ids": [
                    {"targetTemp": 2300},
                    {"temperature": 2290},
                    {"humidity": 50},
                    {"thermostatMode": 1},
                    {"output": 1},
                ],
            }
        )

        devices = await api.get_devices()

        self.assertEqual(23.0, devices[0]["target_temperature"])
        self.assertEqual(22.9, devices[0]["current_temperature"])
        self.assertEqual(50, devices[0]["humidity"])
        self.assertTrue(devices[0]["is_on"])
        self.assertEqual("local", devices[0]["data_source"])
        self.assertEqual(1, len(session.requests))
        api.get_local_device_config.assert_awaited_once_with(
            "iot-hub-456", "local-key"
        )

    async def test_get_devices_falls_back_to_cloud_when_local_is_unavailable(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "numStatus": 0,
                    "ioData": {
                        "ioUser": {
                            "coUbications": [
                                {
                                    "ioDCModel": {"ivstrUbicationName": "Casa"},
                                    "coZones": [
                                        {
                                            "ioDCModel": {"ivstrZoneName": "PB"},
                                            "coDevices": [
                                                {
                                                    "ioDCModel": {
                                                        "ivstrDeviceSerial": "thermostat-123",
                                                        "ivstrParentDeviceSerial": "iot-hub-456",
                                                        "ivstrParentDeviceBleKey": "local-key",
                                                        "ivblnUseLocalConn": True,
                                                        "ivnroDeviceType": 2,
                                                        "ivblnDeviceConnected": True,
                                                    },
                                                    "ioSubTypeObj": {
                                                        "ioDCModel": {
                                                            "ivnumDeviceTargetTemperature": 2100,
                                                        }
                                                    },
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                },
            ),
            FakeResponse(
                200,
                {
                    "numStatus": 0,
                    "ioData": json.dumps(
                        {
                            "status": 200,
                            "ids": [{"targetTemp": 2200}, {"temperature": 2100},
                                    {"thermostatMode": 2}, {"output": 0}],
                        }
                    ),
                },
            ),
        )
        api = ZentralyApi(token="test-token", session=session, local_client=object())
        api._user_id = 42
        api.get_local_device_config = AsyncMock(
            side_effect=ZentralyApiError("local unavailable")
        )

        devices = await api.get_devices()

        self.assertEqual(22.0, devices[0]["target_temperature"])
        self.assertTrue(devices[0]["connected"])
        self.assertEqual("cloud", devices[0]["data_source"])
        api.get_local_device_config.assert_awaited_once_with(
            "iot-hub-456", "local-key"
        )
        self.assertEqual(2, len(session.requests))

    async def test_get_devices_marks_stale_thermostat_unavailable(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "numStatus": 0,
                    "ioData": {
                        "ioUser": {
                            "coUbications": [
                                {
                                    "ioDCModel": {"ivstrUbicationName": "Casa"},
                                    "coZones": [
                                        {
                                            "ioDCModel": {"ivstrZoneName": "PB"},
                                            "coDevices": [
                                                {
                                                    "ioDCModel": {
                                                        "ivstrDeviceSerial": "thermostat-123",
                                                        "ivstrParentDeviceSerial": "iot-hub-456",
                                                        "ivnroDeviceType": 2,
                                                        "ivblnDeviceConnected": True,
                                                    },
                                                    "ioSubTypeObj": {
                                                        "ioDCModel": {
                                                            "ivnumDeviceTargetTemperature": 2100,
                                                        }
                                                    },
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                },
            ),
            FakeResponse(500, {}),
        )
        api = ZentralyApi(token="test-token", session=session)
        api._user_id = 42

        devices = await api.get_devices()

        self.assertFalse(devices[0]["connected"])
        self.assertEqual(21.0, devices[0]["target_temperature"])

    async def test_user_data_keeps_existing_app_contract(self) -> None:
        session = FakeSession(FakeResponse(200, {"numStatus": 0, "ioData": {}}))
        api = ZentralyApi(token="test-token", session=session)
        api._user_id = 42

        await api.get_user_data()

        request = session.requests[0]
        self.assertEqual(
            "https://ztprdrestservicesv2.azurewebsites.net/App",
            request["url"],
        )
        self.assertEqual(
            {
                "coUbications": [],
                "eDcOper": 1,
                "ioDCModel": {"ivlngUser": 42},
            },
            request["json"],
        )


if __name__ == "__main__":
    unittest.main()
