"""Discovery wiring and non-destructive upgrades over upstream config entries."""
from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from test_session_lifecycle import ACCOUNT, SESSION, LifecycleCoordinator, smoke

integration = smoke.integration


class UpstreamSetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.entry = smoke.ConfigEntry("synthetic-entry")
        self.entry.data = ACCOUNT | SESSION
        self.entry.options = {"synthetic_option": True}
        self.entry.unique_id = "synthetic-account"
        self.hass = smoke.FakeHass(self.entry.entry_id, smoke.FakeCoordinator([]))
        self.hass.data["zentraly"].clear()
        self.api_options = {}
        self.get_devices = AsyncMock(return_value=[])
        self.api = types.SimpleNamespace(get_devices=self.get_devices)
        self.patchers = [
            patch.object(integration, "ZentralyApi", self.make_api),
            patch.object(integration, "DataUpdateCoordinator", LifecycleCoordinator),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_api(self, **options):
        self.api_options = options
        return self.api

    async def test_incomplete_session_is_rejected_before_client_resources(self):
        del self.entry.data["firebase_token"]
        with (
            patch.object(integration.aiohttp_client, "async_get_clientsession") as session,
            self.assertRaises(integration.ConfigEntryAuthFailed),
        ):
            await integration.async_setup_entry(self.hass, self.entry)
        session.assert_not_called()
        self.assertFalse(self.api_options)

    async def test_migrations_preserve_existing_registry_identity_and_options(self):
        registry = {
            name: types.SimpleNamespace(unique_id=f"synthetic_{name}", entity_id=name)
            for name in ("is_on", "schedule", "target_temperature", "is_locked")
        }
        original = dict(registry)
        fake_registry = types.SimpleNamespace(async_remove=registry.pop)
        er = sys.modules["homeassistant.helpers.entity_registry"]
        with patch.object(er, "async_get", return_value=fake_registry), patch.object(
            er, "async_entries_for_config_entry", side_effect=lambda *args: list(registry.values())
        ):
            for version in range(1, 6):
                with self.subTest(version=version):
                    registry.clear()
                    registry.update(original)
                    self.entry.version = version
                    saved_data = self.entry.data
                    saved_options = self.entry.options
                    self.assertTrue(await integration.async_migrate_entry(self.hass, self.entry))
                    self.assertEqual(5, self.entry.version)
                    self.assertEqual(original, registry)
                    self.assertIs(saved_data, self.entry.data)
                    self.assertIs(saved_options, self.entry.options)
                    self.assertEqual("synthetic-account", self.entry.unique_id)
                    self.assertEqual("synthetic-entry", self.entry.entry_id)
        self.entry.version = 6
        self.assertFalse(await integration.async_migrate_entry(self.hass, self.entry))
        self.assertEqual(6, self.entry.version)

    async def setup_local_client(self):
        self.assertTrue(await integration.async_setup_entry(self.hass, self.entry))
        self.assertIsNotNone(self.api_options.get("local_client"), "setup must wire upstream LAN discovery")
        return self.api_options["local_client"]

    async def test_discovery_resolves_upstream_advertised_service_without_browser(self):
        client = await self.setup_local_client()
        service_info = types.SimpleNamespace(
            async_request=AsyncMock(return_value=True),
            parsed_scoped_addresses=lambda: ["192.0.2.1"], port=8080,
        )
        with patch.object(integration.zeroconf, "async_get_instance", AsyncMock(), create=True), \
             patch.object(integration, "AsyncServiceInfo", return_value=service_info) as factory, \
             patch.object(integration, "AsyncServiceBrowser") as browser:
            self.assertEqual(("192.0.2.1", 8080), await client._resolve("synthetic-hub"))
        factory.assert_called_once_with("_zentraly._tcp.local.", "synthetic-hub._zentraly._tcp.local.")
        browser.assert_not_called()
        self.assertTrue(await integration.async_unload_entry(self.hass, self.entry))
        self.assertNotIn(self.entry.entry_id, self.hass.data["zentraly"])

    async def test_failing_initial_read_disposes_browser_before_setup_retry(self):
        active = set()
        created = []
        class Browser:
            def __init__(self, *args, **kwargs):
                active.add(self)
                created.append(self)
            async def async_cancel(self):
                active.remove(self)

        service_info = types.SimpleNamespace(async_request=AsyncMock(return_value=False))
        async def fail_inventory():
            client = self.api_options.get("local_client")
            self.assertIsNotNone(client, "setup must wire upstream LAN discovery")
            await client._resolver("synthetic-hub")
            raise integration.ZentralyApiError("synthetic failure")
        self.get_devices.side_effect = fail_inventory
        with patch.object(integration.zeroconf, "async_get_instance", AsyncMock(), create=True), \
             patch.object(integration, "AsyncServiceInfo", return_value=service_info), \
             patch.object(integration, "AsyncServiceBrowser", Browser), \
             patch.object(integration, "ZEROCONF_TIMEOUT_MS", 0):
            for _ in range(2):
                with self.assertRaises(integration.ConfigEntryNotReady):
                    await integration.async_setup_entry(self.hass, self.entry)
                self.assertFalse(active)
                self.assertNotIn(self.entry.entry_id, self.hass.data["zentraly"])
        self.assertEqual(2, len(created))

    async def test_platform_failure_retains_pending_drafts_and_cleans_loaded_entry(self):
        pending_store = smoke.advanced.AdvancedDraftStore()
        pending_store.set("synthetic-device", "temperature_offset", 1.5)
        self.hass.data[integration.DATA_REAUTH_DRAFTS] = {self.entry.entry_id: pending_store}
        with (
            patch.object(self.hass.config_entries, "async_forward_entry_setups", AsyncMock(
                side_effect=integration.ConfigEntryNotReady("synthetic platform failure")
            )),
            self.assertRaises(integration.ConfigEntryNotReady),
        ):
            await integration.async_setup_entry(self.hass, self.entry)
        self.assertNotIn(self.entry.entry_id, self.hass.data["zentraly"])
        self.assertFalse(self.hass.services.registered)
        self.assertIs(pending_store, self.hass.data[integration.DATA_REAUTH_DRAFTS][self.entry.entry_id])
        self.assertTrue(await integration.async_setup_entry(self.hass, self.entry))
        self.assertIs(pending_store, self.hass.data["zentraly"][self.entry.entry_id]["drafts"])
        self.assertNotIn(integration.DATA_REAUTH_DRAFTS, self.hass.data)
