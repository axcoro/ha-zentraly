"""RAM-only draft handoff through real integration reauth/unload/setup callbacks."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from test_api import FakeResponse, FakeSession
import test_platform_smoke as smoke
from test_reauth import ENTRY, SESSION, identity
from test_session_lifecycle import INVENTORY, LifecycleCoordinator

integration = smoke.integration
PENDING = "zentraly_reauth_drafts"


class ReauthDraftTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = smoke.ConfigEntry("synthetic-entry")
        self.entry.data = dict(ENTRY)
        self.hass = smoke.FakeHass(self.entry.entry_id, smoke.FakeCoordinator([]))
        self.store = self.hass.data["zentraly"][self.entry.entry_id]["drafts"]
        self.store.set("synthetic-device", "temperature_offset", 1.5)
        self.store.set("synthetic-device", "display_brightness", 40)
        self.store.clear("synthetic-device", {"display_brightness"})
        self.result = smoke.advanced.ApplyResult(
            wrote=True, applied_keys={"temperature_offset", "display_brightness"},
            confirmed_keys={"display_brightness"}, unconfirmed_keys={"temperature_offset"})
        self.store.set_apply_result("synthetic-device", self.result)
        self.flow = smoke.config_flow.ZentralyConfigFlow()
        self.flow.hass = self.hass
        self.flow.reauth_entry = self.entry
        patcher = patch.object(integration, "DataUpdateCoordinator", LifecycleCoordinator)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def reauth(self, response=None):
        session = FakeSession(FakeResponse(200, response or identity()))
        with patch.object(integration.aiohttp_client, "async_get_clientsession", return_value=session):
            return await self.flow.async_step_reauth_confirm({"session": json.dumps(SESSION)})

    async def setup_entry(self, status=200):
        session = FakeSession(FakeResponse(status, INVENTORY))
        with patch.object(integration.aiohttp_client, "async_get_clientsession", return_value=session):
            result = await integration.async_setup_entry(self.hass, self.entry)
        self.assertTrue(result)
        self.assertEqual(1, len(session.requests))  # Inventory read, no automatic apply.

    def assert_preserved(self):
        store = self.hass.data["zentraly"][self.entry.entry_id]["drafts"]
        self.assertIs(self.store, store)
        self.assertEqual(1.5, store.get("synthetic-device", "temperature_offset"))
        self.assertEqual({"temperature_offset"}, store.dirty_keys("synthetic-device"))
        self.assertIsNone(store.get("synthetic-device", "display_brightness"))
        self.assertIs(self.result, store.last_apply_result("synthetic-device"))

    async def test_reauth_reload_preserves_store_but_next_manual_reload_discards_it(self):
        self.assertEqual("reauth_successful", (await self.reauth())["reason"])
        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))
        await self.setup_entry()
        self.assert_preserved()
        self.assertNotIn(PENDING, self.hass.data)
        await integration.async_unload_entry(self.hass, self.entry)
        await self.setup_entry()
        self.assertIsNot(self.store, self.hass.data["zentraly"][self.entry.entry_id]["drafts"])

    async def test_handoff_survives_failed_setup_reauth_retry_and_platform_forward(self):
        await self.reauth()
        await integration.async_unload_entry(self.hass, self.entry)
        with self.assertRaises(integration.ConfigEntryNotReady):
            await self.setup_entry(status=503)
        # Entry is unloaded; a second validated import must not overwrite the store.
        await self.reauth()
        with patch.object(self.hass.config_entries, "async_forward_entry_setups",
                          side_effect=integration.ConfigEntryNotReady("synthetic failure")):
            with self.assertRaises(integration.ConfigEntryNotReady):
                await self.setup_entry()
        await integration.async_unload_entry(self.hass, self.entry)
        await self.setup_entry()
        self.assert_preserved()
        self.assertNotIn(PENDING, self.hass.data)

    async def test_failed_unload_keeps_original_and_reauth_handoff(self):
        await self.reauth()
        with patch.object(self.hass.config_entries, "async_unload_platforms", return_value=False):
            self.assertFalse(await integration.async_unload_entry(self.hass, self.entry))
        self.assert_preserved()
        await integration.async_unload_entry(self.hass, self.entry)
        await self.setup_entry()
        self.assert_preserved()

    async def test_wrong_account_and_cancelled_form_do_not_stash_drafts(self):
        await self.flow.async_step_reauth(self.entry.data)
        self.assertNotIn(PENDING, self.hass.data)
        result = await self.reauth(identity(email="other@example.invalid"))
        self.assertEqual({"base": "wrong_account"}, result["errors"])
        self.assertNotIn(PENDING, self.hass.data)
        self.assert_preserved()

    async def test_remove_cleans_only_that_entry_and_restart_has_no_handoff(self):
        await self.reauth()
        other_entry = smoke.ConfigEntry("other-entry")
        other_entry.data = dict(ENTRY)
        other_store = smoke.advanced.AdvancedDraftStore()
        self.hass.data["zentraly"][other_entry.entry_id] = {"drafts": other_store}
        self.flow.reauth_entry = other_entry
        await self.reauth()
        await integration.async_remove_entry(self.hass, self.entry)
        self.assertEqual({other_entry.entry_id: other_store}, self.hass.data[PENDING])
        await integration.async_remove_entry(self.hass, other_entry)
        self.assertNotIn(PENDING, self.hass.data)
        # A new HA process restores config-entry data, never the in-memory draft map.
        self.hass = smoke.FakeHass(self.entry.entry_id, smoke.FakeCoordinator([]))
        await self.setup_entry()
        self.assertFalse(self.hass.data["zentraly"][self.entry.entry_id]["drafts"].is_dirty("synthetic-device"))
