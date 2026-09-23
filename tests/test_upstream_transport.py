"""Upstream local transport with isolated synthetic account/device fixtures."""
from __future__ import annotations

import copy
import importlib
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_api import FakeResponse, FakeSession
import test_platform_smoke as smoke

api_module = importlib.import_module("custom_components.zentraly.api")
local_module = importlib.import_module("custom_components.zentraly.local")
zttwf = importlib.import_module("custom_components.zentraly.zttwf")

CONFIG = {"status": 200, "ids": [
    {"temperature": 2010}, {"targetTemp": 1606}, {"thermostatMode": 2}, {"output": 1},
]}


def inventory(*devices):
    return {"numStatus": 0, "ioData": {"ioUser": {"coUbications": [{
        "coZones": [{"coDevices": list(devices)}],
    }]}}}


def device(kind=2, serial="synthetic-child", parent="synthetic-parent", key="synthetic-key"):
    return {"ioDCModel": {
        "ivnroDeviceType": kind, "ivstrDeviceSerial": serial,
        "ivstrParentDeviceSerial": parent, "ivstrParentDeviceBleKey": key,
        "ivblnUseLocalConn": True, "ivblnDeviceConnected": False,
    }, "ioSubTypeObj": {"ioDCModel": {}}}


class UpstreamTransportTests(unittest.IsolatedAsyncioTestCase):
    def api(self, *, local_result=None, local_error=None):
        client = unittest.mock.Mock()
        client.send_command = AsyncMock(
            return_value=copy.deepcopy(local_result or CONFIG), side_effect=local_error)
        api = api_module.ZentralyApi(token="synthetic-token", local_client=client)
        api._local_keys["synthetic-parent"] = "synthetic-key"
        return api, client

    async def test_local_read_and_write_keep_upstream_contract_without_cloud(self):
        api, client = self.api()
        with patch.object(api, "send_iot_command", new_callable=AsyncMock) as cloud:
            config = await api.get_device_config("synthetic-parent")
            await api.set_target_temperature("synthetic-parent", 16.06)
            await api.turn_on("synthetic-parent")
            await api.turn_off("synthetic-parent")
        self.assertEqual("local", config["data_source"])
        self.assertEqual(CONFIG["ids"], config["ids"])
        calls = client.send_command.await_args_list
        self.assertEqual(["getConfig", "setConfig", "setConfig", "setConfig"],
                         [call.args[2] for call in calls])
        self.assertEqual([{"targetTemp": 1606}], calls[1].args[3]["ids"])
        self.assertEqual([{"thermostatMode": 2}], calls[2].args[3]["ids"])
        self.assertEqual([{"thermostatMode": 0}], calls[3].args[3]["ids"])
        cloud.assert_not_awaited()

    async def test_read_falls_back_once_after_local_error_or_invalid_config(self):
        for error, result in ((local_module.ZentralyLocalError("synthetic-secret"), None),
                              (TimeoutError("synthetic-secret"), None),
                              (None, {"status": 200, "ids": []})):
            with self.subTest(error=type(error).__name__):
                api, client = self.api(local_error=error, local_result=result)
                with patch.object(api, "send_iot_command", new_callable=AsyncMock,
                                  return_value=CONFIG) as cloud:
                    config = await api.get_device_config("synthetic-parent")
                self.assertEqual("cloud", config["data_source"])
                self.assertEqual(1, client.send_command.await_count)
                cloud.assert_awaited_once_with("synthetic-parent", "getConfig",
                                              {"ids": api_module.CONFIG_IDS})

    async def test_failed_local_write_is_sanitized_and_never_replayed(self):
        for error in (local_module.ZentralyLocalError("synthetic-secret"),
                      TimeoutError("synthetic-secret"), OSError("synthetic-secret")):
            for method, argument in (("set_target_temperature", 16.06), ("set_hvac_mode", 2)):
                with self.subTest(error=type(error).__name__, method=method):
                    api, client = self.api(local_error=error)
                    with patch.object(api, "send_iot_command", new_callable=AsyncMock) as cloud:
                        with self.assertRaises(api_module.ZentralyApiError) as caught:
                            await getattr(api, method)("synthetic-parent", argument)
                    self.assertNotIn("synthetic-secret", str(caught.exception))
                    self.assertEqual(1, client.send_command.await_count)
                    cloud.assert_not_awaited()

    async def test_missing_key_or_client_selects_cloud_before_write(self):
        for has_client in (True, False):
            api, client = self.api()
            if has_client:
                api._local_keys.clear()
            else:
                api._local_client = None
            with patch.object(api, "send_iot_command", new_callable=AsyncMock,
                              return_value={"status": 200}) as cloud:
                await api.set_target_temperature("synthetic-parent", 16.06)
            cloud.assert_awaited_once_with("synthetic-parent", "setConfig",
                                          {"ids": [{"targetTemp": 1606}]})
            client.send_command.assert_not_awaited()

    async def test_undiscovered_hub_selects_cloud_before_any_local_connection(self):
        for discovery_error in (None, TimeoutError("synthetic-secret"),
                                OSError("synthetic-secret")):
            for method, argument in (("set_target_temperature", 16.06),
                                     ("set_hvac_mode", 2)):
                with self.subTest(error=type(discovery_error).__name__, method=method):
                    session = Mock()
                    resolver = AsyncMock(return_value=None, side_effect=discovery_error)
                    client = local_module.ZentralyLocalClient(session, resolver)
                    api = api_module.ZentralyApi(token="synthetic-token", local_client=client)
                    api._local_keys["synthetic-parent"] = "synthetic-key"
                    with patch.object(api, "send_iot_command", new_callable=AsyncMock,
                                      return_value=CONFIG) as cloud:
                        config = await api.get_device_config("synthetic-parent")
                        self.assertEqual("cloud", config["data_source"])
                        cloud.reset_mock()
                        await getattr(api, method)("synthetic-parent", argument)
                    expected = {"targetTemp": 1606} if method == "set_target_temperature" else {"thermostatMode": 2}
                    cloud.assert_awaited_once_with("synthetic-parent", "setConfig", {"ids": [expected]})
                    session.ws_connect.assert_not_called()

    async def test_real_local_write_failure_never_replays_through_cloud(self):
        for stage in ("connect", "confirmation"):
            with self.subTest(stage=stage):
                websocket = AsyncMock()
                websocket.__aenter__.return_value = websocket
                if stage == "connect":
                    websocket.__aenter__.side_effect = OSError("synthetic-secret")
                session = Mock()
                session.ws_connect.return_value = websocket
                resolver = AsyncMock(return_value=("192.0.2.1", 80))
                client = local_module.ZentralyLocalClient(session, resolver)
                # A cached address may become unreachable; attempting it still
                # commits this operation to the local transport.
                client._address_cache["synthetic-parent"] = ("192.0.2.1", 80)
                api = api_module.ZentralyApi(token="synthetic-token", local_client=client)
                api._local_keys["synthetic-parent"] = "synthetic-key"
                with patch.object(client, "_receive_response", new_callable=AsyncMock,
                                  side_effect=[{"status": 200}, TimeoutError("synthetic-secret")]), \
                        patch.object(api, "send_iot_command", new_callable=AsyncMock) as cloud, \
                        self.assertRaises(api_module.ZentralyApiError) as caught:
                    await api.set_target_temperature("synthetic-parent", 16.06)
                self.assertNotIn("synthetic-secret", str(caught.exception))
                session.ws_connect.assert_called_once()
                commands = [call.args[0]["cmd"] for call in websocket.send_json.await_args_list]
                self.assertEqual([] if stage == "connect" else ["login", "setConfig"], commands)
                cloud.assert_not_awaited()
                self.assertNotIn("synthetic-parent", client._address_cache)

    async def test_mixed_inventory_refreshes_only_type2_and_keeps_keys_private(self):
        api, client = self.api()
        fixtures = inventory(device(), device(16, "synthetic-zttin"), device(17, "synthetic-boiler"))
        with patch.object(api, "get_user_data", new_callable=AsyncMock, return_value=fixtures), \
                patch.object(api, "send_iot_command", new_callable=AsyncMock) as cloud:
            devices = await api.get_devices()
        self.assertEqual([2, 16, 17], [item["device_type"] for item in devices])
        self.assertEqual(20.1, devices[0]["current_temperature"])
        self.assertEqual(16.06, devices[0]["target_temperature"])
        self.assertIsNone(devices[0]["humidity"])
        self.assertIsNone(devices[0]["is_locked"])
        self.assertEqual("local", devices[0]["data_source"])
        self.assertTrue(devices[0]["connected"])
        self.assertNotIn("data_source", devices[1])
        self.assertNotIn("data_source", devices[2])
        self.assertNotIn("synthetic-key", repr(devices))
        self.assertEqual(1, client.send_command.await_count)
        cloud.assert_not_awaited()

    async def test_valid_inventory_replaces_keys_without_cross_account_leak(self):
        api, client = self.api()
        other, _ = self.api()
        with patch.object(api, "get_user_data", new_callable=AsyncMock,
                          return_value=inventory()):
            self.assertEqual([], await api.get_devices())
        self.assertEqual({}, api._local_keys)
        self.assertEqual({"synthetic-parent": "synthetic-key"}, other._local_keys)
        client.send_command.assert_not_awaited()

    async def test_inventory_failure_retains_last_key_map_and_live_read_auth_propagates(self):
        api, _ = self.api()
        with patch.object(api, "get_user_data", new_callable=AsyncMock,
                          return_value={"ioData": {"ioUser": {}}}):
            with self.assertRaises(api_module.ZentralyApiError):
                await api.get_devices()
        self.assertEqual({"synthetic-parent": "synthetic-key"}, api._local_keys)
        api._local_client = None
        with patch.object(api, "get_user_data", new_callable=AsyncMock,
                          return_value=inventory(device())), \
                patch.object(api, "send_iot_command", new_callable=AsyncMock,
                             side_effect=api_module.ZentralyAuthError("expired")):
            with self.assertRaises(api_module.ZentralyAuthError):
                await api.get_devices()

    async def test_failed_live_read_does_not_publish_stale_snapshot_as_connected(self):
        api, _ = self.api(local_error=local_module.ZentralyLocalError("synthetic-secret"))
        with patch.object(api, "get_user_data", new_callable=AsyncMock,
                          return_value=inventory(device())), \
                patch.object(api, "send_iot_command", new_callable=AsyncMock,
                             side_effect=api_module.ZentralyApiError("unavailable")):
            devices = await api.get_devices()
        self.assertFalse(devices[0]["connected"])
        self.assertEqual("unavailable", devices[0]["data_source"])

    async def test_readback_publishes_effective_local_or_cloud_source(self):
        for local_available in (True, False):
            api, client = self.api(local_error=None if local_available else
                                   local_module.ZentralyLocalError("unavailable"))
            coordinator = smoke.FakeCoordinator([{
                "serial": "synthetic-child", "parent_serial": "synthetic-parent", "device_type": 2}])
            coordinator.async_set_updated_data = lambda data: setattr(coordinator, "data", data)
            with patch.object(api, "send_iot_command", new_callable=AsyncMock,
                              return_value=CONFIG):
                await zttwf.refresh_zttwf_after_write(
                    api, coordinator, coordinator.data[0], {"target_temperature": 16.05})
            self.assertEqual("local" if local_available else "cloud", coordinator.data[0]["data_source"])
            self.assertEqual(16.06, coordinator.data[0]["target_temperature"])
            self.assertEqual(1, client.send_command.await_count)

    async def test_type16_write_ignores_local_key_and_uses_existing_action_contract(self):
        api, client = self.api()
        session = FakeSession(FakeResponse(200, {"numStatus": 0, "ioData": {"status": 200}}))
        api._session = session
        await api.set_zttin01_target_temperature("synthetic-parent", "synthetic-mac", 1, 21.5)
        self.assertEqual("writeAttr", session.requests[0]["json"]["vioBody"]["data"]["cmd"])
        client.send_command.assert_not_awaited()
