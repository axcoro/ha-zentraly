"""Offline startup checks; all credentials and server responses are synthetic."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_api import FakeResponse, FakeSession, ZentralyApi, const_module
import base64
import json
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


class ConfigEntryAuthFailed(Exception):
    pass


class FakeCoordinator:
    def __init__(self, hass, logger, *, name, update_method, update_interval):
        self.update_method = update_method

    async def async_config_entry_first_refresh(self):
        self.data = await self.update_method()


def module(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    sys.modules[name] = result
    return result


const = sys.modules['homeassistant.const']
const.CONF_EMAIL = 'email'
const.CONF_PASSWORD = 'password'
module('homeassistant.components', zeroconf=types.SimpleNamespace())
module('homeassistant.config_entries', ConfigEntry=object)
module('homeassistant.core', HomeAssistant=object)
module('homeassistant.exceptions', ConfigEntryAuthFailed=ConfigEntryAuthFailed)
client = types.SimpleNamespace(async_get_clientsession=Mock())
module('homeassistant.helpers', aiohttp_client=client)
module('homeassistant.helpers.update_coordinator', DataUpdateCoordinator=FakeCoordinator,
       UpdateFailed=type('UpdateFailed', (Exception,), {}))
module('zeroconf.asyncio', AsyncServiceBrowser=object, AsyncServiceInfo=object)

path = Path(__file__).resolve().parents[1] / 'custom_components/zentraly/__init__.py'
spec = importlib.util.spec_from_file_location('custom_components.zentraly', path,
                                            submodule_search_locations=[str(path.parent)])
setup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = setup
spec.loader.exec_module(setup)


class SessionStartupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hass = types.SimpleNamespace(data={}, config_entries=types.SimpleNamespace(
            async_forward_entry_setups=AsyncMock(), async_update_entry=Mock()))
        self.entry = types.SimpleNamespace(entry_id='synthetic-entry', data={
            'email': 'test@example.invalid', 'password': 'unused-test-password',
            'token': 'synthetic-session-token', 'user_id': 42,
            'firebase_token': 'synthetic-firebase-token', 'device_guid': 'synthetic-device-guid',
        })

    async def test_saved_session_starts_without_password_login(self):
        session = FakeSession(FakeResponse(200, {'numStatus': 0, 'ioData': {
            'ioUser': {'coUbications': []}}}))
        with patch.object(client, 'async_get_clientsession', return_value=session), \
             patch.object(ZentralyApi, 'authenticate', new_callable=AsyncMock) as login:
            self.assertTrue(await setup.async_setup_entry(self.hass, self.entry))
        login.assert_not_awaited()
        self.assertEqual('ztv2Tokensynthetic-session-token',
                         session.requests[0]['headers']['Authorization'])
        self.assertEqual(42, session.requests[0]['json']['ioDCModel']['ivlngUser'])
        decryptor = Cipher(algorithms.AES(bytes.fromhex(const_module.API_FIREBASE_KEY)),
                           modes.CBC(bytes.fromhex(const_module.API_FIREBASE_IV))).decryptor()
        padded = decryptor.update(base64.b64decode(session.requests[0]['headers']['Firebase'])) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        envelope = json.loads(unpadder.update(padded) + unpadder.finalize())
        metadata = json.loads(base64.b64decode(envelope['data']))
        self.assertEqual('synthetic-firebase-token', metadata['ivstrUserFBToken'])
        self.assertEqual('synthetic-device-guid', metadata['ivstrUserGuid'])
        self.hass.config_entries.async_forward_entry_setups.assert_awaited_once()

    async def test_rejected_session_requires_reauthentication_without_password_retry(self):
        session = FakeSession(FakeResponse(401, {}))
        with patch.object(client, 'async_get_clientsession', return_value=session), \
             patch.object(ZentralyApi, 'authenticate', new_callable=AsyncMock) as login:
            with self.assertRaises(ConfigEntryAuthFailed):
                await setup.async_setup_entry(self.hass, self.entry)
        login.assert_not_awaited()
        self.hass.config_entries.async_forward_entry_setups.assert_not_awaited()

    async def test_existing_password_entry_keeps_login_flow(self):
        del self.entry.data['token']
        del self.entry.data['user_id']
        api = types.SimpleNamespace(authenticate=AsyncMock(), get_devices=AsyncMock(return_value=[]))
        with patch.object(setup, 'ZentralyApi', return_value=api):
            self.assertTrue(await setup.async_setup_entry(self.hass, self.entry))
        api.authenticate.assert_awaited_once()
        api.get_devices.assert_awaited_once()

    async def test_incomplete_saved_session_requires_reauthentication(self):
        del self.entry.data['firebase_token']
        with patch.object(client, 'async_get_clientsession') as get_session:
            with self.assertRaises(ConfigEntryAuthFailed):
                await setup.async_setup_entry(self.hass, self.entry)
        get_session.assert_not_called()

    async def test_legacy_entry_keeps_identity_across_reloads(self):
        self.entry.data = {'email': 'test@example.invalid', 'password': 'synthetic-password'}
        api = types.SimpleNamespace(authenticate=AsyncMock(), get_devices=AsyncMock(return_value=[]))
        with patch.object(setup, 'ZentralyApi', return_value=api) as factory:
            await setup.async_setup_entry(self.hass, self.entry)
            first = factory.call_args.kwargs['device_guid']
            await setup.async_setup_entry(self.hass, self.entry)
            self.assertTrue(first)
            self.assertEqual(first, factory.call_args.kwargs['device_guid'])


if __name__ == '__main__':
    unittest.main()
