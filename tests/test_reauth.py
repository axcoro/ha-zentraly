"""Synthetic same-account reauthentication and secret-free error forms."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from unittest.mock import patch

from test_api import FakeResponse, FakeSession
import test_platform_smoke as smoke

flow_module = smoke.config_flow
SESSION = {"token": "synthetic-new-token", "user_id": 42,
           "firebase_token": "synthetic-new-fb", "device_guid": "SYNTHETIC-NEW-GUID"}
ENTRY = {"email": "  Synthetic@Example.Invalid ", "password": "synthetic-old-password",
         "token": "synthetic-old-token", "user_id": 42, "firebase_token": "synthetic-old-fb",
         "device_guid": "SYNTHETIC-OLD-GUID", "other_option": "unchanged"}


def identity(user_id=42, email="synthetic@example.invalid"):
    return {"numStatus": 0, "ioData": {"ioUser": {"ioDCModel": {
        "ivlngUser": user_id, "ivstrUserEmail": email}}}}


class ReauthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = smoke.ConfigEntry("synthetic-entry")
        self.entry.data = copy.deepcopy(ENTRY)
        self.entry.unique_id = "original-unique-id"
        self.flow = flow_module.ZentralyConfigFlow()
        self.flow.hass = smoke.FakeHass(self.entry.entry_id, smoke.FakeCoordinator([]))
        self.flow.reauth_entry = self.entry

    async def submit(self, value, responses=()):
        session = FakeSession(*responses)
        with patch.object(flow_module.aiohttp_client, "async_get_clientsession", return_value=session):
            result = await self.flow.async_step_reauth_confirm({"session": value})
        return result, session

    def assert_unchanged(self):
        self.assertEqual(ENTRY, self.entry.data)
        self.assertEqual("original-unique-id", self.entry.unique_id)
        self.assertEqual([], self.flow.hass.config_entries.updates)
        self.assertEqual([], self.flow.hass.config_entries.reloads)

    def assert_safe_error_form(self, result, error):
        self.assertEqual({"base": error}, result["errors"])
        self.assertEqual("reauth_confirm", result["step_id"])
        schema = result["data_schema"].value
        self.assertEqual(1, len(schema))
        key, selector = next(iter(schema.items()))
        self.assertEqual("session", key.key)
        self.assertEqual("password", selector.config["type"])
        self.assertFalse(hasattr(key, "default"))
        self.assertNotIn(SESSION["token"], repr(result))
        self.assertNotIn(SESSION["firebase_token"], repr(result))

    async def test_empty_form_masks_session_without_default(self):
        result = await self.flow.async_step_reauth(self.entry.data)
        self.assertEqual({}, result["errors"])
        self.assertEqual("reauth_confirm", result["step_id"])
        schema = result["data_schema"].value
        marker, selector = next(iter(schema.items()))
        self.assertEqual("session", marker.key)
        self.assertEqual("password", selector.config["type"])
        self.assertFalse(hasattr(marker, "default"))
        self.assert_unchanged()

    async def test_valid_same_account_import_is_atomic_and_reloads_once(self):
        result, session = await self.submit(json.dumps(SESSION | {"password": "do-not-import", "other_option": "ignore"}),
                                            [FakeResponse(200, identity())])
        self.assertEqual({"type": "abort", "reason": "reauth_successful"}, result)
        self.assertEqual(ENTRY | SESSION, self.entry.data)
        self.assertEqual("original-unique-id", self.entry.unique_id)
        self.assertEqual([self.entry.entry_id], self.flow.hass.config_entries.reloads)
        self.assertEqual(1, len(self.flow.hass.config_entries.updates))
        self.assertEqual(1, len(session.requests))
        self.assertEqual(1, session.requests[0]["json"]["eDcOper"])
        self.assertNotIn("vioBody", session.requests[0]["json"])

    async def test_malformed_sessions_never_make_a_request(self):
        invalid = ["invalid JSON", "[]", "null", "true", "42", json.dumps({})]
        invalid += [json.dumps(SESSION | {"user_id": v}) for v in (True, False, 0, -1, 42.0, "42", None)]
        for key in ("token", "firebase_token", "device_guid"):
            invalid += [json.dumps(SESSION | {key: v}) for v in ("", " ", 42, True, None)]
        for value in invalid:
            with self.subTest(kind=type(value).__name__):
                result, session = await self.submit(value)
                self.assert_safe_error_form(result, "invalid_session")
                self.assertEqual([], session.requests)
                self.assert_unchanged()

    async def test_wrong_or_unverifiable_account_never_updates(self):
        invalid = [identity(43), identity(email="someoneelse@example.invalid"), identity(True),
                   identity(email=None), identity(user_id=None), identity(email=" "),
                   {"numStatus": 0, "ioData": {}},
                   {"numStatus": 0, "ioData": {"ioUser": []}}]
        for data in invalid:
            with self.subTest(shape=type(data).__name__):
                result, session = await self.submit(json.dumps(SESSION), [FakeResponse(200, data)])
                self.assert_safe_error_form(result, "wrong_account")
                self.assertEqual(1, len(session.requests))
                self.assert_unchanged()
        result, _ = await self.submit(json.dumps(SESSION | {"user_id": 43}), [FakeResponse(200, identity(43))])
        self.assert_safe_error_form(result, "wrong_account")
        self.assert_unchanged()

    async def test_legacy_entry_without_saved_user_id_still_requires_email(self):
        del self.entry.data["user_id"]
        result, _ = await self.submit(json.dumps(SESSION), [FakeResponse(200, identity())])
        self.assertEqual("reauth_successful", result["reason"])
        self.assertEqual(42, self.entry.data["user_id"])

    async def test_corrupt_saved_user_id_can_recover_with_verified_identity(self):
        for saved_id in (None, True, False, 0, -1, "42", 42.0):
            with self.subTest(saved_id=saved_id):
                self.setUp()
                self.entry.data["user_id"] = saved_id
                result, _ = await self.submit(json.dumps(SESSION), [FakeResponse(200, identity())])
                self.assertEqual("reauth_successful", result.get("reason"))
                self.assertEqual(ENTRY | SESSION, self.entry.data)
                self.assertEqual("original-unique-id", self.entry.unique_id)
                self.assertEqual(1, len(self.flow.hass.config_entries.updates))
                self.assertEqual([self.entry.entry_id], self.flow.hass.config_entries.reloads)

    async def test_auth_and_transient_errors_are_safe_and_do_not_mutate(self):
        for status, error in ((401, "invalid_auth"), (403, "invalid_auth"), (503, "cannot_connect")):
            with self.subTest(status=status):
                result, session = await self.submit(json.dumps(SESSION), [FakeResponse(status, {"token": "synthetic-secret"})])
                self.assert_safe_error_form(result, error)
                self.assertEqual(1, len(session.requests))
                self.assert_unchanged()
        for error in (TimeoutError("synthetic-secret"), OSError("synthetic-secret"),
                      sys.modules["aiohttp"].ClientError("synthetic-secret")):
            with patch.object(flow_module.ZentralyApi, "get_user_data", side_effect=error):
                result, _ = await self.submit(json.dumps(SESSION))
            self.assert_safe_error_form(result, "cannot_connect")
            self.assert_unchanged()
