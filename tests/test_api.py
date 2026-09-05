"""Tests for the Zentraly API client."""
from __future__ import annotations

import importlib.util
import base64
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

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
sys.modules.setdefault("aiohttp", aiohttp)

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)

homeassistant_const = types.ModuleType("homeassistant.const")
homeassistant_const.Platform = types.SimpleNamespace(
    CLIMATE="climate",
    SENSOR="sensor",
    BINARY_SENSOR="binary_sensor",
    NUMBER="number",
    SELECT="select",
    LOCK="lock",
    BUTTON="button",
)
sys.modules.setdefault("homeassistant.const", homeassistant_const)

_load_module("custom_components.zentraly.const", PACKAGE_PATH / "const.py")
api_module = _load_module("custom_components.zentraly.api", PACKAGE_PATH / "api.py")

ZentralyApi = api_module.ZentralyApi
ZentralyApiError = api_module.ZentralyApiError
command_device_id = getattr(api_module, "command_device_id", None)
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


def decode_firebase_header(header: str) -> dict:
    decryptor = Cipher(
        algorithms.AES(bytes.fromhex(const_module.API_FIREBASE_KEY)),
        modes.CBC(bytes.fromhex(const_module.API_FIREBASE_IV)),
    ).decryptor()
    padded = decryptor.update(base64.b64decode(header)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    envelope = json.loads(unpadder.update(padded) + unpadder.finalize())
    return json.loads(base64.b64decode(envelope["data"]))


class ZentralyApiTests(unittest.IsolatedAsyncioTestCase):
    """Verify the reverse-engineered API contract."""

    def test_firebase_header_matches_current_encrypted_contract(self) -> None:
        self.assertTrue(hasattr(const_module, "API_FIREBASE_KEY"))
        self.assertTrue(hasattr(const_module, "API_FIREBASE_IV"))
        self.assertTrue(hasattr(api_module, "time"))
        self.assertTrue(hasattr(api_module, "secrets"))
        api = ZentralyApi()
        api._device_guid = "TEST-GUID"

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
        self.assertEqual("TEST-GUID", firebase_data["ivstrUserFBToken"])
        self.assertEqual("7.2.0", firebase_data["ivstrUserZtVersion"])
        self.assertEqual("AR", firebase_data["ivstrUserCountry"])

    async def test_login_reuses_installation_identity_after_client_recreation(self) -> None:
        device_guid = "B32032E5-03E4-42C8-9ED4-062D81357C53"
        for _ in range(2):
            session = FakeSession(FakeResponse(200, {
                "numStatus": 0,
                "ioData": {"ivstrToken": "test-session-token", "ioUser": {"ioDCModel": {"ivlngUser": 1}}},
            }))
            api = ZentralyApi(
                email="test@example.invalid", password="test-password",
                session=session, device_guid=device_guid,
            )
            await api.authenticate()
            request = session.requests[0]
            self.assertEqual("https://ztprdrestservicesv2.azurewebsites.net/Login", request["url"])
            fields = decode_firebase_header(request["headers"]["Firebase"])
            self.assertEqual(device_guid, fields["ivstrUserGuid"])
            self.assertEqual(device_guid, fields["ivstrUserFBToken"])
            self.assertEqual("HomeAssistant", fields["ivstrUserMobileTrade"])

    def test_firebase_counter_increments_per_request(self) -> None:
        api = ZentralyApi()
        self.assertTrue(hasattr(api, "_request_counter"))
        self.assertTrue(hasattr(api_module, "secrets"))

        with patch.object(api_module.secrets, "randbelow", return_value=0):
            first_header = api._generate_firebase_header()
            second_header = api._generate_firebase_header()

        self.assertNotEqual(first_header, second_header)
        self.assertEqual(2, api._request_counter)

    def test_command_device_id_prefers_iot_hub_parent(self) -> None:
        self.assertIsNotNone(command_device_id)
        for device_type in (2, 16, 17):
            with self.subTest(device_type=device_type):
                device = {
                    "serial": "CHILD",
                    "parent_serial": "PARENT",
                    "device_type": device_type,
                }
                self.assertEqual("PARENT", command_device_id(device))

    def test_command_device_id_falls_back_to_serial(self) -> None:
        self.assertIsNotNone(command_device_id)
        self.assertEqual("DEVICE", command_device_id({"serial": "DEVICE"}))

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

    async def test_send_iot_command_reports_device_errors(self) -> None:
        session = FakeSession(
            FakeResponse(200, {"numStatus": 0, "ioData": '{"status":404}'})
        )
        api = ZentralyApi(token="test-token", session=session)

        with self.assertRaisesRegex(ZentralyApiError, "device status 404"):
            await api.send_iot_command("serial-123", "getConfig", {"ids": []})

    async def test_send_iot_command_rejects_missing_device_status(self) -> None:
        session = FakeSession(FakeResponse(200, {"numStatus": 0, "ioData": "{}"}))
        api = ZentralyApi(token="test-token", session=session)

        with self.assertRaisesRegex(ZentralyApiError, "device status None"):
            await api.send_iot_command("serial-123", "getConfig", {"ids": []})

    async def test_send_iot_command_rejects_outer_errors(self) -> None:
        session = FakeSession(
            FakeResponse(
                200,
                {
                    "numStatus": 7,
                    "ioData": '{"status":200,"token":"DO-NOT-LOG"}',
                },
            )
        )
        api = ZentralyApi(token="test-token", session=session)

        with self.assertRaises(ZentralyApiError) as caught:
            await api.send_iot_command("serial-123", "getConfig", {"ids": []})

        self.assertEqual("Command failed: status 7", str(caught.exception))
        self.assertNotIn("DO-NOT-LOG", str(caught.exception))

    async def test_auth_and_app_outer_errors_do_not_include_remote_payloads(self) -> None:
        auth_session = FakeSession(
            FakeResponse(
                200,
                {"numStatus": 9, "ioData": {"ivstrToken": "DO-NOT-LOG"}},
            )
        )
        auth_api = ZentralyApi(email="test@example.invalid", password="test", session=auth_session)

        with self.assertRaises(api_module.ZentralyAuthError) as auth_error:
            await auth_api.authenticate()
        self.assertEqual("Authentication failed: status 9", str(auth_error.exception))
        self.assertNotIn("DO-NOT-LOG", str(auth_error.exception))

        app_session = FakeSession(
            FakeResponse(200, {"numStatus": 8, "ioData": {"secret": "DO-NOT-LOG"}})
        )
        app_api = ZentralyApi(token="test-token", session=app_session)
        app_api._user_id = 42

        with self.assertRaises(ZentralyApiError) as app_error:
            await app_api.get_user_data()
        self.assertEqual("API error: status 8", str(app_error.exception))
        self.assertNotIn("DO-NOT-LOG", str(app_error.exception))

    async def test_send_iot_command_rejects_invalid_io_data(self) -> None:
        session = FakeSession(
            FakeResponse(200, {"numStatus": 0, "ioData": "not-json"})
        )
        api = ZentralyApi(token="test-token", session=session)

        with self.assertRaisesRegex(ZentralyApiError, "invalid ioData"):
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

    async def test_send_iot_command_does_not_allow_data_to_override_request_id(self) -> None:
        session = FakeSession(
            FakeResponse(200, {"numStatus": 0, "ioData": '{"status":200}'})
        )
        api = ZentralyApi(token="test-token", session=session)

        await api.send_iot_command("serial-123", "getConfig", {"ids": [], "rid": 9999})

        self.assertEqual(0, session.requests[0]["json"]["vioBody"]["data"]["rid"])

    async def test_attr_commands_share_the_command_request_id(self) -> None:
        session = FakeSession(
            FakeResponse(200, {"numStatus": 0, "ioData": '{"status":200}'}),
            FakeResponse(200, {"numStatus": 0, "ioData": '{"status":200}'}),
        )
        api = ZentralyApi(token="test-token", session=session)

        await api.send_write_attr_command(
            "serial-123",
            "AA:BB:CC:DD:EE:FF",
            65513,
            1,
            [{"id": 18, "type": 41, "val": 2150}],
        )
        await api.send_read_attr_command(
            "serial-123",
            "AA:BB:CC:DD:EE:FF",
            65513,
            1,
            [{"id": 18, "type": 41}],
        )

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

    def test_zttin01_read_attr_parser_normalizes_all_known_fields(self) -> None:
        api = ZentralyApi()
        response = {
            "attrs": [
                {"id": 0, "type": 41, "val": 2150},
                {"id": 1, "type": 41, "val": 47},
                {"id": 18, "type": 41, "val": 2200},
                {"id": 28, "type": 41, "val": 2},
                {"id": 4, "type": 72, "val": "0101286001"},
                {"id": 17, "type": 41, "val": 1700},
                {"id": 90, "type": 41, "val": 1},
                {"id": 16, "type": 41, "val": -50},
                {"id": 100, "type": 41, "val": 80},
                {"id": 101, "type": 41, "val": 0},
                {"id": 102, "type": 41, "val": 1},
            ]
        }

        self.assertEqual(
            {
                "current_temperature": 21.5,
                "humidity": 47,
                "target_temperature": 22.0,
                "mode": 2,
                "is_locked": True,
                "schedule": "0101286001",
                "temperature_offset": -0.5,
                "away_temperature": 17.0,
                "display_brightness": 80,
                "display_always_on": False,
                "display_type": 1,
            },
            api._parse_zttin01_read_attr_response(response),
        )

    def test_boiler_raw_parser_adds_known_semantic_fields(self) -> None:
        api = ZentralyApi()

        cluster_65535 = api._parse_raw_read_attr_response(
            65535,
            {
                "attrs": [
                    {"id": 1, "val": 6500},
                    {"id": 56, "val": 5000},
                    {"id": 1056, "val": 1},
                    {"id": 1001, "val": 3},
                    {"id": 10001, "val": 0},
                ]
            },
        )
        cluster_65006 = api._parse_raw_read_attr_response(
            65006,
            {
                "attrs": [
                    {"id": 0, "val": 1},
                    {"id": 2, "val": 0},
                    {"id": 10, "val": 120},
                    {"id": 13000, "val": 7200},
                ]
            },
        )

        self.assertEqual(65.0, cluster_65535["boiler_heating_temperature"])
        self.assertEqual(50.0, cluster_65535["boiler_h2o_temperature"])
        self.assertIs(cluster_65535["is_h2o_enabled"], True)
        self.assertEqual(3, cluster_65535["weather_type"])
        self.assertIs(cluster_65535["is_comfort_mode"], False)
        self.assertIs(cluster_65006["heat_demand"], True)
        self.assertIs(cluster_65006["is_forced_on"], False)
        self.assertEqual(2.0, cluster_65006["on_delay"])
        self.assertEqual(2.0, cluster_65006["heating_demand_time_today"])


if __name__ == "__main__":
    unittest.main()
