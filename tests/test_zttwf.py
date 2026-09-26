"""Validated type-2 cloud state with synthetic fixtures only."""
from __future__ import annotations

import copy
import importlib
import importlib.util
import unittest
from unittest.mock import AsyncMock, patch

import test_platform_smoke as smoke

MODULE_NAME = "custom_components.zentraly.zttwf"
zttwf = importlib.import_module(MODULE_NAME) if importlib.util.find_spec(MODULE_NAME) else None
ApiError = smoke.integration.ZentralyApiError
AuthError = smoke.integration.ZentralyAuthError

CONFIG = {"status": 200, "ids": [
    {"temperature": 2310}, {"targetTemp": 2300}, {"thermostatMode": 4},
    {"output": 1}, {"humidity": 50}, {"lock": 0},
]}
STATE = {"current_temperature": 23.1, "target_temperature": 23.0,
         "mode": 4, "is_on": True, "humidity": 50, "is_locked": False}
DEVICE = {"serial": "SYNTHETIC-CHILD", "parent_serial": "SYNTHETIC-PARENT",
          "device_type": 2, "connected": False, "current_temperature": 1.0,
          "target_temperature": 2.0, "mode": 0}


def config_with(**fields):
    config = copy.deepcopy(CONFIG)
    for item in config["ids"]:
        key = next(iter(item))
        if key in fields:
            item[key] = fields[key]
    return config


class ZttwfParserTests(unittest.TestCase):
    def parse(self, config):
        self.assertIsNotNone(zttwf, "Type-2 cloud reader has not been implemented")
        return zttwf.parse_zttwf_config(config)

    def test_literal_cloud_fixture_matches_independent_semantic_state(self):
        self.assertEqual(STATE, self.parse(CONFIG))

    def test_optional_fields_missing_are_none_and_unknown_keys_ignored(self):
        config = copy.deepcopy(CONFIG)
        config["ids"] = config["ids"][:4] + [{"ssid": "synthetic-network"}]
        self.assertEqual(STATE | {"humidity": None, "is_locked": None}, self.parse(config))

    def test_invalid_envelopes_items_and_missing_critical_fields_rejected(self):
        invalid = [None, [], {}, {"status": 404, "ids": CONFIG["ids"]},
                   {"status": 200, "ids": []}, {"status": 200, "ids": {}},
                   {"status": 200, "ids": "synthetic-secret"}]
        for item in (None, [], {}, 1, "synthetic-secret", {"temperature": 2300, "output": 1}):
            invalid.append({"status": 200, "ids": CONFIG["ids"] + [item]})
        for key in ("temperature", "targetTemp", "thermostatMode", "output"):
            invalid.append({"status": 200, "ids": [item for item in CONFIG["ids"] if key not in item]})
        for config in invalid:
            with self.subTest(shape=type(config).__name__):
                with self.assertRaises(ApiError) as caught:
                    self.parse(config)
                self.assertNotIn("synthetic-secret", str(caught.exception))

    def test_types_finite_numbers_ranges_and_strict_binary_fields(self):
        invalid_fields = {
            "temperature": (True, None, "2310", float("nan"), float("inf"), -float("inf"), 10**400),
            "targetTemp": (False, None, "2300", float("nan"), float("inf")),
            "thermostatMode": (True, None, "4", 4.0),
            "output": ("0", "1", 2, -1, 0.0, None),
            "lock": ("0", 2, 1.0, None),
            "humidity": (True, None, "50", -1, 101, float("nan"), float("inf")),
        }
        for field, values in invalid_fields.items():
            for value in values:
                with self.subTest(field=field, value_type=type(value).__name__):
                    with self.assertRaises(ApiError):
                        self.parse(config_with(**{field: value}))

    def test_equal_duplicates_are_allowed_and_conflicts_rejected_after_validation(self):
        for key, value in (("temperature", 2310), ("output", True), ("thermostatMode", 4)):
            config = copy.deepcopy(CONFIG)
            config["ids"].append({key: value})
            self.assertEqual(STATE, self.parse(config))
        for key, value in (("temperature", 2400), ("output", 0), ("thermostatMode", 0), ("temperature", True)):
            with self.subTest(field=key, value_type=type(value).__name__):
                config = copy.deepcopy(CONFIG)
                config["ids"].append({key: value})
                with self.assertRaises(ApiError):
                    self.parse(config)

    def test_measurements_have_no_speculative_temperature_or_read_mode_limits(self):
        state = self.parse(config_with(temperature=-4500, targetTemp=12000, thermostatMode=99,
                                       humidity=0, output=False, lock=True))
        self.assertEqual(-45.0, state["current_temperature"])
        self.assertEqual(120.0, state["target_temperature"])
        self.assertEqual(99, state["mode"])
        self.assertIs(state["is_on"], False)
        self.assertIs(state["is_locked"], True)


class ZttwfEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def enrich(self, api, fixtures):
        """Exercise inventory/live type-2 reads before optional type-16/17 reads."""
        self.assertTrue(hasattr(smoke.integration, "_async_enrich_device_state"), "Family enrichment not implemented")
        inventory = {"ioData": {"ioUser": {"coUbications": [{"coZones": [{
            "coDevices": [{
                "ioDCModel": {
                    "ivstrDeviceSerial": device["serial"],
                    "ivstrParentDeviceSerial": device.get("parent_serial"),
                    "ivnroDeviceType": device["device_type"],
                    "ivblnDeviceConnected": device.get("connected", False),
                    "ivstrDeviceMac": device.get("mac"),
                    "ivnumEndPoint": device.get("endpoint_id"),
                },
                "ioSubTypeObj": {"ioDCModel": {}},
            } for device in fixtures],
        }]}]}}}
        with patch.object(api, "get_user_data", return_value=inventory):
            devices = await api.get_devices()
        await smoke.integration._async_enrich_device_state(api, devices)
        return devices

    async def test_cloud_reader_routes_parent_and_falls_back_to_child(self):
        self.assertIsNotNone(zttwf, "Type-2 reader not implemented")
        api = smoke.integration.ZentralyApi(token="synthetic-token")
        with patch.object(api, "get_device_config", return_value=CONFIG) as get:
            devices = await self.enrich(api, [DEVICE, DEVICE | {"parent_serial": None}])
        self.assertEqual([STATE, STATE], [{key: device[key] for key in STATE} for device in devices])
        self.assertEqual(["SYNTHETIC-PARENT", "SYNTHETIC-CHILD"], [call.args[0] for call in get.await_args_list])

    async def test_mixed_account_reads_each_family_once_and_keeps_other_semantics(self):
        api = smoke.integration.ZentralyApi(token="synthetic-token")
        fixtures = [DEVICE, smoke.DEVICES[1], smoke.DEVICES[2]]
        with patch.object(api, "get_device_config", return_value=CONFIG) as get, \
                patch.object(api, "read_zttin01_raw_attrs", return_value={"heat_demand": True}) as read16, \
                patch.object(api, "read_boiler_raw_attrs", return_value={"on_delay": 2.0}) as read17:
            devices = await self.enrich(api, fixtures)
        get.assert_awaited_once_with("SYNTHETIC-PARENT")
        read16.assert_awaited_once()
        read17.assert_awaited_once()
        self.assertEqual(STATE, {key: devices[0][key] for key in STATE})
        self.assertTrue(devices[0]["connected"])
        self.assertEqual("cloud", devices[0]["data_source"])
        self.assertTrue(devices[1]["heat_demand"])
        self.assertEqual(2.0, devices[2]["on_delay"])
        self.assertNotIn("data_source", devices[1])
        self.assertNotIn("data_source", devices[2])

    async def test_failed_type2_is_local_and_does_not_contaminate_same_parent_sibling(self):
        for error in (ApiError("synthetic-secret"), TimeoutError("synthetic-secret"),
                      OSError("synthetic-secret"), {"status": 200, "ids": []}):
            with self.subTest(error_type=type(error).__name__):
                api = smoke.integration.ZentralyApi(token="synthetic-token")
                fixtures = [DEVICE, DEVICE | {"serial": "SYNTHETIC-SIBLING"}]
                with patch.object(api, "get_device_config", side_effect=[error, CONFIG]) as get:
                    with self.assertLogs("custom_components.zentraly.api", level="DEBUG") as logs:
                        devices = await self.enrich(api, fixtures)
                self.assertEqual(2, get.await_count)
                self.assertFalse(devices[0]["connected"])
                self.assertEqual("unavailable", devices[0]["data_source"])
                self.assertTrue(devices[1]["connected"])
                self.assertEqual(23.1, devices[1]["current_temperature"])
                self.assertNotIn("synthetic-secret", "\n".join(logs.output))

    async def test_auth_aborts_cycle_without_retrying_concurrent_sibling_reads(self):
        api = smoke.integration.ZentralyApi(token="synthetic-token")
        fixtures = [DEVICE, DEVICE | {"serial": "SYNTHETIC-SIBLING", "parent_serial": "OTHER-PARENT"},
                    smoke.DEVICES[1]]
        with patch.object(api, "get_device_config", side_effect=[AuthError("expired"), CONFIG]) as get, \
                patch.object(api, "read_zttin01_raw_attrs") as read16:
            with self.assertRaises(AuthError):
                await self.enrich(api, fixtures)
        # Upstream starts type-2 reads together; either may finish before auth
        # aborts the cycle, but neither target is read a second time.
        self.assertEqual(["SYNTHETIC-PARENT", "OTHER-PARENT"], [call.args[0] for call in get.await_args_list])
        read16.assert_not_awaited()

    async def test_optional_type16_17_read_failure_does_not_change_availability(self):
        api = smoke.integration.ZentralyApi(token="synthetic-token")
        with patch.object(api, "get_device_config") as get, \
                patch.object(api, "read_zttin01_raw_attrs", new=AsyncMock(side_effect=ApiError("offline"))), \
                patch.object(api, "read_boiler_raw_attrs", new=AsyncMock(side_effect=ApiError("offline"))):
            with self.assertLogs(smoke.integration.__name__, level="WARNING"):
                devices = await self.enrich(api, smoke.DEVICES[1:])
        get.assert_not_awaited()
        self.assertTrue(all(device["connected"] for device in devices))


class ZttwfModeTests(unittest.TestCase):
    def entity(self, state):
        coordinator = smoke.FakeCoordinator([DEVICE | state])
        return smoke.PLATFORMS["climate"].ZentralyThermostat(coordinator, object(), coordinator.data[0])

    def test_type2_zero_is_off_every_integer_nonzero_is_heat(self):
        mode_type = smoke.PLATFORMS["climate"].HVACMode
        for mode in (0, 1, 2, 3, 4, -1, 99):
            with self.subTest(mode=mode):
                self.assertEqual(mode_type.OFF if mode == 0 else mode_type.HEAT,
                                 self.entity({"mode": mode}).hvac_mode)
        for mode in (None, True, "4", 4.0):
            with self.subTest(mode_type=type(mode).__name__):
                self.assertIsNone(self.entity({"mode": mode}).hvac_mode)

    def test_activity_follows_output_not_temperature_delta(self):
        action = smoke.PLATFORMS["climate"].HVACAction
        cases = [(0, True, 10, 25, action.OFF), (4, False, 10, 25, action.IDLE),
                 (4, True, 30, 10, action.HEATING), (2, False, 30, 10, action.IDLE),
                 (None, True, 10, 25, None), (4, None, 10, 25, None)]
        for mode, output, current, target, expected in cases:
            with self.subTest(mode=mode, output=output):
                entity = self.entity({"mode": mode, "is_on": output,
                                      "current_temperature": current, "target_temperature": target})
                self.assertEqual(expected, entity.hvac_action)

    def test_type16_modes_demand_and_temperature_fallback_remain_unchanged(self):
        climate = smoke.PLATFORMS["climate"]
        for mode, expected_mode, expected_action in (
            (climate.ZTTIN01_MODE_OFF, climate.HVACMode.OFF, climate.HVACAction.OFF),
            (climate.ZTTIN01_MODE_AUTO, climate.HVACMode.AUTO, climate.HVACAction.HEATING),
            (climate.ZTTIN01_MODE_MANUAL, climate.HVACMode.HEAT, climate.HVACAction.HEATING),
            (climate.ZTTIN01_MODE_AWAY, climate.HVACMode.HEAT, climate.HVACAction.HEATING),
        ):
            data = dict(smoke.DEVICES[1], mode=mode, heat_demand=True,
                        current_temperature=30, target_temperature=20)
            coordinator = smoke.FakeCoordinator([data])
            entity = climate.ZentralyThermostat(coordinator, object(), data)
            self.assertEqual(expected_mode, entity.hvac_mode)
            self.assertEqual(expected_action, entity.hvac_action)
        data.update(mode=climate.ZTTIN01_MODE_MANUAL)
        data.pop("heat_demand")
        self.assertEqual(climate.HVACAction.IDLE, entity.hvac_action)
        data["current_temperature"] = 10
        self.assertEqual(climate.HVACAction.HEATING, entity.hvac_action)


class RecordingCoordinator(smoke.FakeCoordinator):
    def __init__(self, data):
        super().__init__(data)
        self.refresh_count = 0
        self.publications = []

    async def async_request_refresh(self):
        self.refresh_count += 1

    def async_set_updated_data(self, data):
        self.publications.append(data)
        self.data = data


class ZttwfReadbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sleep_patcher = patch("asyncio.sleep", new_callable=AsyncMock)
        self.sleep = self.sleep_patcher.start()
        self.addCleanup(self.sleep_patcher.stop)

    async def refresh(self, api, coordinator, expected):
        self.assertTrue(hasattr(zttwf, "refresh_zttwf_after_write"), "Type-2 readback not implemented")
        return await zttwf.refresh_zttwf_after_write(api, coordinator, DEVICE, expected)

    async def test_climate_one_write_two_reads_and_only_child_updated(self):
        from test_api import FakeResponse, FakeSession
        session = FakeSession(
            FakeResponse(200, {"numStatus": 0, "ioData": {"status": 200}}),
            FakeResponse(200, {"numStatus": 0, "ioData": config_with(targetTemp=2100)}),
            FakeResponse(200, {"numStatus": 0, "ioData": config_with(targetTemp=2200)}),
        )
        api = smoke.integration.ZentralyApi(token="synthetic-token", session=session)
        child, sibling = copy.deepcopy(DEVICE), DEVICE | {"serial": "SYNTHETIC-SIBLING"}
        coordinator = RecordingCoordinator([child, sibling])
        entity = smoke.PLATFORMS["climate"].ZentralyThermostat(coordinator, api, child)
        await entity.async_set_temperature(temperature=22.0)
        commands = [request["json"]["vioBody"] for request in session.requests]
        self.assertEqual(["setConfig", "getConfig", "getConfig"], [c["data"]["cmd"] for c in commands])
        self.assertEqual(["SYNTHETIC-PARENT"] * 3, [c["deviceId"] for c in commands])
        self.assertEqual([0, 1, 2], [c["data"]["rid"] for c in commands])
        self.assertEqual(22.0, coordinator.data[0]["target_temperature"])
        self.assertEqual(2.0, child["target_temperature"])
        self.assertIs(sibling, coordinator.data[1])
        self.assertEqual("zentraly_SYNTHETIC-CHILD", entity._attr_unique_id)
        self.assertEqual(0, coordinator.refresh_count)
        self.sleep.assert_awaited_once_with(1)

    async def test_hvac_write_contract_manual_2_off_0_and_mode_4_confirms_heat(self):
        from test_api import FakeResponse, FakeSession
        climate = smoke.PLATFORMS["climate"]
        for mode, requested, returned in ((climate.HVACMode.HEAT, 2, 4), (climate.HVACMode.OFF, 0, 0)):
            with self.subTest(mode=mode):
                session = FakeSession(
                    FakeResponse(200, {"numStatus": 0, "ioData": {"status": 200}}),
                    FakeResponse(200, {"numStatus": 0, "ioData": config_with(thermostatMode=returned)}),
                )
                api = smoke.integration.ZentralyApi(token="synthetic-token", session=session)
                coordinator = RecordingCoordinator([copy.deepcopy(DEVICE)])
                entity = climate.ZentralyThermostat(coordinator, api, DEVICE)
                await entity.async_set_hvac_mode(mode)
                self.assertEqual(2, len(session.requests))
                self.assertEqual([{"thermostatMode": requested}], session.requests[0]["json"]["vioBody"]["data"]["ids"])
                self.assertEqual(returned, coordinator.data[0]["mode"])
                self.assertEqual(0, coordinator.refresh_count)

    async def test_final_mismatch_publishes_real_value_not_requested(self):
        api = smoke.integration.ZentralyApi(token="synthetic-token")
        coordinator = RecordingCoordinator([copy.deepcopy(DEVICE)])
        with patch.object(api, "get_device_config", return_value=config_with(targetTemp=2100)) as get:
            with self.assertRaises(ApiError):
                await self.refresh(api, coordinator, {"target_temperature": 24})
        self.assertEqual(2, get.await_count)
        self.assertEqual(21.0, coordinator.data[0]["target_temperature"])
        self.assertTrue(coordinator.data[0]["connected"])
        self.assertEqual(0, coordinator.refresh_count)

    async def test_read_failure_without_valid_state_is_unavailable(self):
        for error in (TimeoutError("synthetic-secret"), ApiError("synthetic-secret"),
                      OSError("synthetic-secret"), {"status": 200, "ids": []}):
            with self.subTest(error_type=type(error).__name__):
                api = smoke.integration.ZentralyApi(token="synthetic-token")
                coordinator = RecordingCoordinator([copy.deepcopy(DEVICE)])
                with patch.object(api, "get_device_config", side_effect=[error]) as get:
                    with self.assertRaises(ApiError) as caught:
                        await self.refresh(api, coordinator, {"target_temperature": 24})
                self.assertEqual(1, get.await_count)
                self.assertFalse(coordinator.data[0]["connected"])
                self.assertEqual("unavailable", coordinator.data[0]["data_source"])
                self.assertNotEqual(24, coordinator.data[0]["target_temperature"])
                self.assertNotIn("synthetic-secret", str(caught.exception))
                self.assertEqual(0, coordinator.refresh_count)

    async def test_second_read_error_keeps_previous_real_read_and_raises(self):
        for error in (TimeoutError("synthetic-secret"), AuthError("expired")):
            api = smoke.integration.ZentralyApi(token="synthetic-token")
            coordinator = RecordingCoordinator([copy.deepcopy(DEVICE)])
            with patch.object(api, "get_device_config", side_effect=[config_with(targetTemp=2100), error]) as get:
                with self.assertRaises(AuthError if isinstance(error, AuthError) else ApiError):
                    await self.refresh(api, coordinator, {"target_temperature": 24})
            self.assertEqual(2, get.await_count)
            self.assertEqual(21.0, coordinator.data[0]["target_temperature"])
            self.assertEqual(0, coordinator.refresh_count)

    async def test_auth_first_read_has_no_retry(self):
        api = smoke.integration.ZentralyApi(token="synthetic-token")
        coordinator = RecordingCoordinator([copy.deepcopy(DEVICE)])
        with patch.object(api, "get_device_config", side_effect=AuthError("expired")) as get:
            with self.assertRaises(AuthError):
                await self.refresh(api, coordinator, {"mode": 2})
        self.assertEqual(1, get.await_count)
        self.sleep.assert_not_awaited()
        self.assertEqual(0, coordinator.refresh_count)

    async def test_confirmation_tolerance_is_absolute_centesimal(self):
        for actual, expected, confirms in ((2300, 23.009, True), (2300, 23.011, False),
                                            (2301, 23.0, True), (2299, 23.0, True),
                                            (10000000005, 100000000.0, False)):
            with self.subTest(confirms=confirms):
                api = smoke.integration.ZentralyApi(token="synthetic-token")
                coordinator = RecordingCoordinator([copy.deepcopy(DEVICE)])
                with patch.object(api, "get_device_config", return_value=config_with(targetTemp=actual)) as get:
                    if confirms:
                        await self.refresh(api, coordinator, {"target_temperature": expected})
                    else:
                        with self.assertRaises(ApiError):
                            await self.refresh(api, coordinator, {"target_temperature": expected})
                self.assertEqual(1 if confirms else 2, get.await_count)

    async def test_type2_http_auth_write_or_readback_starts_reauth_directly(self):
        from test_api import FakeResponse, FakeSession
        from test_session_lifecycle import ACCOUNT, SESSION, INVENTORY, LifecycleCoordinator, AuthFailed
        for during_read in (False, True):
            with self.subTest(during_read=during_read):
                entry = smoke.ConfigEntry("synthetic-entry")
                entry.data = ACCOUNT | SESSION
                hass = smoke.FakeHass(entry.entry_id, smoke.FakeCoordinator([]))
                responses = [FakeResponse(200, INVENTORY)]
                if during_read:
                    responses.append(FakeResponse(200, {"numStatus": 0, "ioData": {"status": 200}}))
                responses.extend([FakeResponse(401, {}), FakeResponse(200, INVENTORY)])
                session = FakeSession(*responses)
                with patch.object(smoke.integration, "DataUpdateCoordinator", LifecycleCoordinator), \
                        patch.object(smoke.integration.aiohttp_client, "async_get_clientsession", return_value=session):
                    await smoke.integration.async_setup_entry(hass, entry)
                coordinator = hass.test_coordinator
                coordinator.data = [copy.deepcopy(DEVICE)]
                api = hass.data["zentraly"][entry.entry_id]["api"]
                entity = smoke.PLATFORMS["climate"].ZentralyThermostat(coordinator, api, DEVICE)
                with self.assertRaises(AuthFailed):
                    await entity.async_set_temperature(temperature=22)
                self.assertEqual(1, entry.reauth_requests)
                self.assertEqual(0, coordinator.refresh_count)
                self.assertEqual(3 if during_read else 2, len(session.requests))
                commands = [r["json"]["vioBody"]["data"]["cmd"] for r in session.requests if "vioBody" in r["json"]]
                self.assertEqual(["setConfig", "getConfig"] if during_read else ["setConfig"], commands)

    async def test_setpoint_rounds_wire_value_and_publishes_only_readback(self):
        from test_api import FakeResponse, FakeSession
        for actual, confirms in ((1606, True), (1605, True), (1607, True), (1604, False), (1608, False)):
            with self.subTest(actual=actual):
                read = FakeResponse(200, {"numStatus": 0, "ioData": config_with(targetTemp=actual)})
                session = FakeSession(
                    FakeResponse(200, {"numStatus": 0, "ioData": {"status": 200}}), read, read)
                api = smoke.integration.ZentralyApi(token="synthetic-token", session=session)
                coordinator = RecordingCoordinator([copy.deepcopy(DEVICE)])
                entity = smoke.PLATFORMS["climate"].ZentralyThermostat(coordinator, api, DEVICE)
                if confirms:
                    await entity.async_set_temperature(temperature=16.06)
                else:
                    with self.assertRaises(ApiError):
                        await entity.async_set_temperature(temperature=16.06)
                commands = [r["json"]["vioBody"]["data"] for r in session.requests]
                self.assertEqual([{"targetTemp": 1606}], commands[0]["ids"])
                self.assertEqual(["setConfig"] + ["getConfig"] * (1 if confirms else 2),
                                 [command["cmd"] for command in commands])
                self.assertEqual(actual / 100, coordinator.data[0]["target_temperature"])
                self.assertEqual(0, coordinator.refresh_count)
