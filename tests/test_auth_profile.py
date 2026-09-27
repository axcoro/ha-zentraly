"""Synthetic Android profile, editable authentication and restart continuity."""
from __future__ import annotations

import asyncio
import copy
import unittest
from unittest.mock import patch

from test_api import FakeResponse, FakeSession, ZentralyApi, api_module, decode_firebase_header
from test_reauth import ENTRY, SESSION, identity
from test_session_lifecycle import LifecycleCoordinator
import test_platform_smoke as smoke

flow_module = smoke.config_flow


PROFILE = {
    "app_version": "7.2.0", "mobile_os": 1, "mobile_trade": "Synthetic",
    "mobile_model": "Teléfono Ω", "mobile_os_version": 33,
    "language": "es", "country": "AR", "user_agent": "synthetic/1",
}


class AuthProfileApiTests(unittest.TestCase):
    def test_profile_survives_encryption_and_request_wrapper_changes(self):
        profile = copy.deepcopy(PROFILE)
        api = ZentralyApi(device_guid="synthetic-guid", firebase_token="synthetic-fcm",
                          auth_profile=profile)
        profile["mobile_model"] = "mutated outside client"
        with patch.object(api_module.time, "time", return_value=1750000000), \
                patch.object(api_module.secrets, "randbelow", return_value=0):
            first, second = api._get_headers("login"), api._get_headers("token")
        self.assertEqual("synthetic/1", first["User-Agent"])
        self.assertNotEqual(first["Firebase"], second["Firebase"])
        self.assertEqual({
            "ivstrUserFBToken": "synthetic-fcm", "ivstrUserGuid": "synthetic-guid",
            "ivstrUserZtVersion": "7.2.0", "ivnroUserMobileOS": 1,
            "ivstrUserMobileTrade": "Synthetic", "ivstrUserMobileModel": "Teléfono Ω",
            "ivstrUserMobileOSVersion": 33, "ivstrUserLanguage": "es",
            "ivstrUserCountry": "AR",
        }, decode_firebase_header(first["Firebase"]))

    def test_invalid_stored_profiles_cannot_silently_use_legacy_defaults(self):
        invalid = [[], {}, "invalid", PROFILE | {"extra": "value"}]
        invalid += [PROFILE | {"mobile_os_version": value}
                    for value in (True, 0, -1, 33.5, "33", None)]
        invalid += [PROFILE | {"mobile_os": value} for value in (True, 2, "1")]
        invalid += [PROFILE | {key: value} for key, value in (
            ("user_agent", "a\r\nb"), ("user_agent", ""), ("app_version", " "),
            ("mobile_model", None), ("country", "419"), ("language", "es_AR"))]
        for value in invalid:
            with self.subTest(profile=value):
                with self.assertRaises(ValueError):
                    ZentralyApi(auth_profile=value)

    def test_language_country_normalize_without_mutating_input(self):
        profile = PROFILE | {"language": " ES ", "country": " ar "}
        api = ZentralyApi(auth_profile=profile)
        fields = decode_firebase_header(api._generate_firebase_header())
        self.assertEqual("es", fields["ivstrUserLanguage"])
        self.assertEqual("AR", fields["ivstrUserCountry"])
        self.assertEqual(" ar ", profile["country"])


def responses():
    account = identity()
    account["ioData"]["ioUser"]["coUbications"] = []
    return [FakeResponse(200, {"numStatus": 0, "ioData": {
        "ivstrToken": "synthetic-new-token", "ioUser": {"ioDCModel": {"ivlngUser": 42}}}}),
        FakeResponse(200, account)]


class AuthProfileFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = smoke.ConfigEntry("synthetic-profile-entry")
        self.entry.data = copy.deepcopy(ENTRY | {"auth_profile": PROFILE})
        self.flow = flow_module.ZentralyConfigFlow()
        self.flow.hass = smoke.FakeHass(self.entry.entry_id, smoke.FakeCoordinator([]))
        self.flow.reconfigure_entry = self.entry
        self.before = copy.deepcopy(self.entry.data)

    async def start_edit(self, credentials=None):
        menu = await self.flow.async_step_reconfigure()
        self.assertEqual("menu", menu["type"])
        self.assertEqual(["credentials", "reauth_confirm"], menu["menu_options"])
        return await self.flow.async_step_credentials(credentials or {})

    async def submit(self, values, queued=None):
        session = FakeSession(*(responses() if queued is None else queued))
        with patch.object(flow_module.aiohttp_client, "async_get_clientsession", return_value=session):
            result = await self.flow.async_step_auth_profile(values)
        return result, session

    async def test_reconfigure_keeps_empty_secrets_and_validates_before_atomic_save(self):
        form = await self.start_edit()
        self.assertEqual("auth_profile", form["step_id"])
        self.assertEqual(self.before, self.entry.data)
        result, session = await self.submit(PROFILE | {"mobile_model": "Edited", "device_guid": "", "firebase_token": ""})
        self.assertEqual("reconfigure_successful", result["reason"])
        self.assertEqual(self.before | {"token": "synthetic-new-token",
                         "auth_profile": PROFILE | {"mobile_model": "Edited"}}, self.entry.data)
        self.assertEqual(1, len(self.flow.hass.config_entries.updates))
        self.assertEqual([self.entry.entry_id], self.flow.hass.config_entries.reloads)
        self.assertEqual(2, len(session.requests))
        self.assertTrue(session.requests[0]["url"].endswith("/Login"))
        self.assertTrue(session.requests[1]["url"].endswith("/App"))
        self.assertEqual("Edited", decode_firebase_header(session.requests[1]["headers"]["Firebase"])["ivstrUserMobileModel"])

    async def test_reconfigure_replaces_supplied_secrets_without_echoing(self):
        await self.start_edit({"password": "new-synthetic-password"})
        result, session = await self.submit(PROFILE | {"device_guid": SESSION["device_guid"],
                                                     "firebase_token": SESSION["firebase_token"]})
        self.assertEqual("reconfigure_successful", result["reason"])
        self.assertEqual("new-synthetic-password", self.entry.data["password"])
        self.assertEqual(SESSION["device_guid"], self.entry.data["device_guid"])
        self.assertEqual(SESSION["firebase_token"], self.entry.data["firebase_token"])
        self.assertTrue(session.requests[0]["headers"]["Authorization"].endswith(":new-synthetic-password"))

    async def test_initial_flow_does_not_login_until_profile_submission(self):
        self.flow = flow_module.ZentralyConfigFlow()
        self.flow.hass = smoke.FakeHass("new", smoke.FakeCoordinator([]))
        with patch.object(flow_module.ZentralyApi, "authenticate", side_effect=AssertionError("early login")):
            result = await self.flow.async_step_user({"email": "synthetic@example.invalid", "password": "synthetic-pw"})
        self.assertEqual("auth_profile", result["step_id"])
        result, session = await self.submit(PROFILE | {"firebase_token": "synthetic-fcm"})
        saved = result["data"]
        self.assertRegex(saved["device_guid"], r"^[0-9a-f]{16}$")
        self.assertEqual(PROFILE, saved["auth_profile"])
        self.assertEqual(saved["device_guid"], decode_firebase_header(session.requests[0]["headers"]["Firebase"])["ivstrUserGuid"])

    async def test_legacy_mixed_case_account_cannot_be_added_again(self):
        self.entry.unique_id = "  Synthetic@Example.Invalid "
        self.flow.hass.config_entries.entries = [self.entry]
        result = await self.flow.async_step_user({"email": "Synthetic@Example.Invalid", "password": "synthetic-pw"})
        self.assertEqual({"type": "abort", "reason": "already_configured"}, result)
        self.assertEqual("  Synthetic@Example.Invalid ", self.entry.unique_id)
        self.assertEqual(self.before, self.entry.data)

    async def test_account_added_while_profile_open_is_detected_before_login(self):
        await self.flow.async_step_user({"email": "synthetic@example.invalid", "password": "synthetic-pw"})
        self.flow.hass.config_entries.entries = [self.entry]
        result, session = await self.submit(PROFILE | {"firebase_token": "synthetic-fcm"})
        self.assertEqual({"type": "abort", "reason": "already_configured"}, result)
        self.assertEqual([], session.requests)

    async def test_failed_login_read_or_identity_keeps_all_saved_data(self):
        bad_account = identity(email="other@example.invalid")
        for queued, error in (
            ([FakeResponse(200, {"numStatus": 1})], "invalid_auth"),
            (responses()[:1] + [FakeResponse(527, {})], "cannot_connect"),
            (responses()[:1] + [FakeResponse(200, identity())], "cannot_connect"),
            (responses()[:1] + [FakeResponse(200, bad_account)], "wrong_account"),
        ):
            with self.subTest(error=error):
                await self.start_edit({"password": "candidate-password"})
                result, _ = await self.submit(PROFILE, queued)
                self.assertEqual({"base": error}, result["errors"])
                self.assertEqual(self.before, self.entry.data)
                self.assertEqual([], self.flow.hass.config_entries.updates)
                self.assertNotIn("candidate-password", repr(result))
                self.assertNotIn(ENTRY["firebase_token"], repr(result))

    async def test_bad_inputs_make_no_request_and_secrets_have_no_defaults(self):
        await self.start_edit()
        for value in (True, 0, "33", 33.5):
            result, session = await self.submit(PROFILE | {"mobile_os_version": value}, [])
            self.assertEqual({"base": "invalid_profile"}, result["errors"])
            self.assertEqual([], session.requests)
            for key, selector in result["data_schema"].value.items():
                if key.key in ("firebase_token", "device_guid"):
                    self.assertEqual("password", selector.config["type"])
                    self.assertFalse(hasattr(key, "default"))
            self.assertEqual(self.before, self.entry.data)

    async def test_concurrent_flow_cannot_start_a_second_login(self):
        await self.start_edit()
        second = flow_module.ZentralyConfigFlow()
        second.hass, second.reconfigure_entry = self.flow.hass, self.entry
        await second.async_step_reconfigure()
        await second.async_step_credentials({})
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused_login(api):
            entered.set()
            await release.wait()
            raise api_module.ZentralyAuthError("synthetic rejection")

        with patch.object(flow_module.ZentralyApi, "authenticate", paused_login):
            first = asyncio.create_task(self.flow.async_step_auth_profile(PROFILE))
            await entered.wait()
            try:
                result = await second.async_step_auth_profile(PROFILE)
                self.assertEqual({"base": "auth_in_progress"}, result["errors"])
            finally:
                release.set()
                await first


class AuthProfileSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_restart_retains_profile_and_never_logs_in(self):
        for profile_data in ({}, {"auth_profile": PROFILE}):
            entry = smoke.ConfigEntry("saved-entry")
            entry.data = copy.deepcopy(ENTRY | profile_data)
            before = copy.deepcopy(entry.data)
            with patch.object(ZentralyApi, "authenticate", side_effect=AssertionError("login at restart")), \
                    patch.object(ZentralyApi, "get_devices", return_value=[]), \
                    patch.object(smoke.integration, "DataUpdateCoordinator", LifecycleCoordinator):
                for _ in range(2):
                    hass = smoke.FakeHass(entry.entry_id, smoke.FakeCoordinator([]))
                    await smoke.integration.async_setup_entry(hass, entry)
                    api = hass.data["zentraly"][entry.entry_id]["api"]
                    fields = decode_firebase_header(api._generate_firebase_header())
                    self.assertEqual("Teléfono Ω" if profile_data else "Integration", fields["ivstrUserMobileModel"])
                    self.assertEqual(before, entry.data)

    async def test_corrupt_profile_or_missing_new_session_requests_reauth_without_login(self):
        for data in (ENTRY | {"auth_profile": {}}, ENTRY | {"auth_profile": None},
                     {key: value for key, value in (ENTRY | {"auth_profile": PROFILE}).items() if key != "token"}):
            entry = smoke.ConfigEntry("saved-entry")
            entry.data = data
            hass = smoke.FakeHass(entry.entry_id, smoke.FakeCoordinator([]))
            with patch.object(ZentralyApi, "authenticate", side_effect=AssertionError("unexpected login")):
                with self.assertRaises(smoke.integration.ConfigEntryAuthFailed):
                    await smoke.integration.async_setup_entry(hass, entry)
