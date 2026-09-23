"""Offline session lifecycle and HA action-boundary contracts; no real network."""
from __future__ import annotations

import copy
import sys
import unittest
from unittest.mock import patch

from test_api import FakeResponse, FakeSession
import test_platform_smoke as smoke

integration = smoke.integration
api_module = sys.modules["custom_components.zentraly.api"]
AuthError = api_module.ZentralyAuthError
AuthFailed = sys.modules["homeassistant.exceptions"].ConfigEntryAuthFailed
UpdateFailed = sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed

SESSION = {"token": "synthetic-token", "user_id": 42,
           "firebase_token": "synthetic-fb", "device_guid": "SYNTHETIC-GUID"}
ACCOUNT = {"email": "synthetic@example.invalid", "password": "synthetic-password",
           "unrelated_option": "preserve-me"}
LOGIN = {"numStatus": 0, "ioData": {"ivstrToken": "synthetic-token",
         "ioUser": {"ioDCModel": {"ivlngUser": 42}}}}
INVENTORY = {"numStatus": 0, "ioData": {"ioUser": {"coUbications": []}}}


class LifecycleCoordinator(smoke.FakeCoordinator):
    """Model only HA's documented first-refresh and read-only reauth boundary."""
    def __init__(self, hass, logger, *, update_method, **kwargs):
        super().__init__([])
        self.hass = hass
        self.config_entry = kwargs["config_entry"]
        self.update_method = update_method
        self.refresh_count = 0
        self.reauth_requested = False
        self.last_exception = None
        hass.test_coordinator = self

    async def async_config_entry_first_refresh(self):
        try:
            self.data = await self.update_method()
        except UpdateFailed:
            raise integration.ConfigEntryNotReady("Initial update failed") from None

    async def async_request_refresh(self):
        self.refresh_count += 1
        try:
            self.data = await self.update_method()
        except (AuthFailed, UpdateFailed) as err:
            self.last_exception = err
            self.last_update_success = False
            self.reauth_requested = isinstance(err, AuthFailed)


class SessionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = smoke.ConfigEntry("synthetic-entry")
        self.hass = smoke.FakeHass(self.entry.entry_id, smoke.FakeCoordinator([]))
        self.patcher = patch.object(integration, "DataUpdateCoordinator", LifecycleCoordinator)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def setup_with(self, data, responses):
        self.entry.data = copy.deepcopy(data)
        session = FakeSession(*responses)
        with patch.object(integration.aiohttp_client, "async_get_clientsession", return_value=session):
            result = await integration.async_setup_entry(self.hass, self.entry)
        return result, session

    async def test_saved_complete_session_never_logs_in(self):
        result, session = await self.setup_with(ACCOUNT | SESSION, [FakeResponse(200, INVENTORY)])
        self.assertTrue(result)
        self.assertEqual(1, len(session.requests))
        self.assertEqual(42, session.requests[0]["json"]["ioDCModel"]["ivlngUser"])
        self.assertEqual(ACCOUNT | SESSION, self.entry.data)
        self.assertEqual(7, len(self.hass.config_entries.forwards[0][1]))

    async def test_legacy_login_once_persists_session_and_stable_guid(self):
        result, session = await self.setup_with(ACCOUNT, [FakeResponse(200, LOGIN), FakeResponse(200, INVENTORY)])
        self.assertTrue(result)
        self.assertEqual(2, len(session.requests))
        self.assertEqual("preserve-me", self.entry.data["unrelated_option"])
        self.assertEqual(42, self.entry.data["user_id"])
        self.assertEqual(self.entry.data["device_guid"], self.entry.data["firebase_token"])
        saved = dict(self.entry.data)
        _, session = await self.setup_with(saved, [FakeResponse(200, INVENTORY)])
        self.assertEqual(1, len(session.requests))
        self.assertEqual(saved, self.entry.data)

    async def test_incomplete_saved_session_fails_without_any_request(self):
        invalid = []
        for key in SESSION:
            value = dict(SESSION)
            if key == "token":
                value[key] = " "
            else:
                del value[key]
            invalid.append(value)
        invalid += [SESSION | {"user_id": v} for v in (True, 0, "42", 42.0)]
        invalid += [SESSION | {key: " "} for key in ("device_guid", "firebase_token")]
        for data in invalid:
            with self.subTest(fields=sorted(data)):
                self.entry.data = ACCOUNT | data
                session = FakeSession()
                with patch.object(integration.aiohttp_client, "async_get_clientsession", return_value=session):
                    with self.assertRaises(AuthFailed):
                        await integration.async_setup_entry(self.hass, self.entry)
                self.assertEqual([], session.requests)

    async def test_http_auth_setup_vs_transient_setup(self):
        for saved in (False, True):
            for status in (401, 403, 503):
                with self.subTest(saved=saved, status=status):
                    expected = AuthFailed if status in (401, 403) else integration.ConfigEntryNotReady
                    with self.assertRaises(expected):
                        await self.setup_with(ACCOUNT | (SESSION if saved else {}), [FakeResponse(status, {})])
                    self.assertIn("device_guid", self.entry.data)
                    if saved:
                        self.assertEqual(SESSION["token"], self.entry.data["token"])

    async def test_missing_inventory_is_failure_not_empty_success(self):
        malformed = [{}, {"ioUser": {}}, {"ioUser": None},
                     {"ioUser": {"coUbications": None}},
                     {"ioUser": {"coUbications": {}}}]
        for io_data in malformed:
            response = {"numStatus": 0, "ioData": io_data}
            with self.subTest(io_data=io_data):
                with self.assertRaises(integration.ConfigEntryNotReady):
                    await self.setup_with(ACCOUNT | SESSION, [FakeResponse(200, response)])
                await self.setup_with(ACCOUNT | SESSION,
                                      [FakeResponse(200, INVENTORY), FakeResponse(200, response)])
                coordinator = self.hass.test_coordinator
                previous = copy.deepcopy(smoke.DEVICES)
                coordinator.data = previous
                await coordinator.async_request_refresh()
                self.assertIs(previous, coordinator.data)
                self.assertFalse(coordinator.last_update_success)
                self.assertIsInstance(coordinator.last_exception, UpdateFailed)
                self.assertFalse(coordinator.reauth_requested)

    async def test_boiler_service_auth_starts_reauth_without_refresh(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.entry.reauth_requests = 0
                _, session = await self.setup_with(ACCOUNT | SESSION, [FakeResponse(200, INVENTORY),
                    FakeResponse(status, {}), FakeResponse(503, {})])
                device = dict(smoke.DEVICES[2])
                entry_data = self.hass.data["zentraly"][self.entry.entry_id]
                key = smoke.advanced.BOILER_H2O_TEMPERATURE
                call = sys.modules["homeassistant.core"].ServiceCall({"device_id": "synthetic-id", key: 50})
                callback = self.hass.services.registered["apply_boiler_settings"][1]
                with patch.object(integration, "_device_from_service_call", return_value=(entry_data, device)):
                    with self.assertRaises(AuthFailed):
                        await callback(call)
                self.assertEqual(1, self.entry.reauth_requests)
                self.assertEqual(0, self.hass.test_coordinator.refresh_count)
                self.assertEqual(2, len(session.requests))
                self.assertEqual("writeAttr", session.requests[1]["json"]["vioBody"]["data"]["cmd"])

    async def test_poll_network_api_and_auth_are_distinct(self):
        await self.setup_with(ACCOUNT | SESSION, [FakeResponse(200, INVENTORY)])
        api = self.hass.data["zentraly"][self.entry.entry_id]["api"]
        coordinator = self.hass.test_coordinator
        for error in (AuthError("synthetic-secret"), api_module.ZentralyApiError("synthetic-secret"),
                      TimeoutError("synthetic-secret"), OSError("synthetic-secret")):
            with self.subTest(error=type(error).__name__):
                with patch.object(api, "get_devices", side_effect=error):
                    with self.assertRaises(AuthFailed if isinstance(error, AuthError) else UpdateFailed) as caught:
                        await coordinator.update_method()
                self.assertNotIn("synthetic-secret", str(caught.exception))
                self.assertEqual(SESSION["device_guid"], self.entry.data["device_guid"])

    async def test_enrichment_auth_escapes_both_families(self):
        for device_type, method in ((16, "read_zttin01_raw_attrs"), (17, "read_boiler_raw_attrs")):
            with self.subTest(device_type=device_type):
                api = api_module.ZentralyApi(**SESSION)
                with patch.object(api, method, side_effect=AuthError("synthetic-secret")):
                    with self.assertRaises(AuthError):
                        await integration._async_enrich_device_state(api, [{"serial": "synthetic-child", "device_type": device_type}])

    async def test_entity_write_auth_starts_reauth_without_inventory_refresh(self):
        _, session = await self.setup_with(ACCOUNT | SESSION, [FakeResponse(200, INVENTORY),
                       FakeResponse(401, {}), FakeResponse(200, INVENTORY)])
        coordinator = self.hass.test_coordinator
        coordinator.data = [dict(smoke.DEVICES[1])]
        api = self.hass.data["zentraly"][self.entry.entry_id]["api"]
        entity = smoke.PLATFORMS["climate"].ZentralyThermostat(coordinator, api, coordinator.data[0])
        with self.assertRaises(AuthFailed):
            await entity.async_set_temperature(temperature=22)
        self.assertEqual(1, self.entry.reauth_requests)
        self.assertEqual(0, coordinator.refresh_count)
        self.assertEqual(2, len(session.requests))
        self.assertEqual("writeAttr", session.requests[1]["json"]["vioBody"]["data"]["cmd"])

    async def test_service_readback_auth_preserves_draft_and_requests_reauth(self):
        _, session = await self.setup_with(ACCOUNT | SESSION, [FakeResponse(200, INVENTORY),
            FakeResponse(200, {"numStatus": 0, "ioData": {"status": 200}}),
            FakeResponse(403, {}), FakeResponse(503, {})])
        coordinator = self.hass.test_coordinator
        device = dict(smoke.DEVICES[1])
        coordinator.data = [device]
        entry_data = self.hass.data["zentraly"][self.entry.entry_id]
        key = smoke.advanced.THERMOSTAT_DISPLAY_BRIGHTNESS
        entry_data["drafts"].set(device["serial"], key, 40)
        call = sys.modules["homeassistant.core"].ServiceCall({"device_id": "synthetic-registry-id", key: 40})
        callback = self.hass.services.registered["apply_thermostat_advanced_settings"][1]
        with patch.object(integration, "_device_from_service_call", return_value=(entry_data, device)):
            with self.assertRaises(AuthFailed):
                await callback(call)
        self.assertEqual(1, self.entry.reauth_requests)
        self.assertEqual(0, coordinator.refresh_count)
        self.assertEqual(3, len(session.requests))
        self.assertEqual(["writeAttr", "readAttr"], [r["json"]["vioBody"]["data"]["cmd"] for r in session.requests[1:3]])
        self.assertIn(key, entry_data["drafts"].dirty_keys(device["serial"]))

    async def test_each_public_writer_boundary_starts_reauth_directly(self):
        cases = [("lock", "async_lock"), ("lock", "async_unlock"),
                 ("number", "async_set_native_value"), ("select", "async_select_option"),
                 ("climate", "async_set_hvac_mode"), ("climate", "async_set_preset_mode"),
                 ("button16", "async_press"), ("button17", "async_press")]
        for platform, method in cases:
            with self.subTest(platform=platform, method=method):
                self.entry.reauth_requests = 0
                _, session = await self.setup_with(ACCOUNT | SESSION, [FakeResponse(200, INVENTORY),
                                   FakeResponse(403, {}), FakeResponse(200, INVENTORY)])
                coordinator = self.hass.test_coordinator
                data = self.hass.data["zentraly"][self.entry.entry_id]
                api, drafts = data["api"], data["drafts"]
                device = dict(smoke.DEVICES[2 if platform == "button17" else 1])
                coordinator.data = [device]
                args = ()
                if platform == "lock":
                    entity = smoke.PLATFORMS[platform].ZentralyLock(coordinator, api, device)
                elif platform == "number":
                    entity = smoke.PLATFORMS[platform].ZentralyTargetTemperatureNumber(coordinator, api, device)
                    args = (22,)
                elif platform == "select":
                    entity = smoke.PLATFORMS[platform].ZentralyModeSelect(coordinator, api, device)
                    args = ("manual",)
                elif platform == "climate":
                    entity = smoke.PLATFORMS[platform].ZentralyThermostat(coordinator, api, device)
                    args = ((smoke.PLATFORMS[platform].HVACMode.HEAT,) if method == "async_set_hvac_mode"
                            else (smoke.PLATFORMS[platform].ZENTRALY_PRESET_AWAY,))
                else:
                    key = "boiler_h2o_temperature" if platform == "button17" else "display_brightness"
                    drafts.set(device["serial"], key, 50)
                    entity = smoke.PLATFORMS["button"].ZentralyAdvancedButton(
                        coordinator, api, drafts, device, smoke.PLATFORMS["button"].BUTTON_DESCRIPTIONS[1])
                with self.assertRaises(AuthFailed):
                    await getattr(entity, method)(*args)
                self.assertEqual(1, self.entry.reauth_requests)
                self.assertEqual(0, coordinator.refresh_count)
                self.assertEqual(2, len(session.requests))
                self.assertEqual("writeAttr", session.requests[1]["json"]["vioBody"]["data"]["cmd"])
