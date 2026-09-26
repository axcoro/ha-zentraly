"""Session replacement preserves the existing account and hides credentials."""
import json
import sys
import types
import unittest
from unittest.mock import Mock, patch

from test_api import PACKAGE_PATH, FakeResponse, FakeSession, _load_module
from test_setup import client, module


class ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        pass


class TextSelector:
    def __init__(self, config):
        self.config = config

    def __call__(self, value):
        return value


sys.modules['homeassistant.config_entries'].ConfigFlow = ConfigFlow
module('homeassistant.data_entry_flow', FlowResult=dict)
module('homeassistant.helpers.selector', TextSelector=TextSelector,
       TextSelectorConfig=dict, TextSelectorType=types.SimpleNamespace(PASSWORD='password'))
flow_module = _load_module('custom_components.zentraly.config_flow', PACKAGE_PATH / 'config_flow.py')


class SessionFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session_data = {'token': 'synthetic-jwt', 'user_id': 42,
                             'firebase_token': 'synthetic-firebase', 'device_guid': 'synthetic-guid'}
        self.user_input = {'session': json.dumps(self.session_data)}
        self.entry = types.SimpleNamespace(entry_id='existing-entry', data={'email': 'test@example.invalid'})
        self.flow = flow_module.ZentralyConfigFlow()
        self.flow.hass = types.SimpleNamespace(data={})
        self.flow._get_reauth_entry = Mock(return_value=self.entry)
        self.flow.async_show_form = Mock(side_effect=lambda **kwargs: kwargs)
        self.flow.async_update_reload_and_abort = Mock(return_value={'type': 'abort', 'reason': 'reauth_successful'})

    async def submit(self, account):
        session = FakeSession(FakeResponse(200, {'numStatus': 0, 'ioData': {'ioUser': {'ioDCModel': account}}}))
        with patch.object(client, 'async_get_clientsession', return_value=session):
            return await self.flow.async_step_reauth_confirm(self.user_input)

    async def test_valid_session_updates_existing_entry_only(self):
        result = await self.submit({'ivlngUser': 42, 'ivstrUserEmail': 'Test@Example.Invalid'})
        self.assertEqual('reauth_successful', result['reason'])
        self.flow.async_update_reload_and_abort.assert_called_once_with(self.entry, data_updates=self.session_data)

    async def test_different_account_is_not_saved(self):
        for account in ({'ivlngUser': 99, 'ivstrUserEmail': 'test@example.invalid'},
                        {'ivlngUser': 42, 'ivstrUserEmail': 'someone-else@example.invalid'}):
            with self.subTest(account=account):
                result = await self.submit(account)
                self.assertEqual('wrong_account', result['errors']['base'])
                self.flow.async_update_reload_and_abort.assert_not_called()

    async def test_invalid_session_does_not_call_api_or_save(self):
        for value in ('not-json', '{}', json.dumps({**self.session_data, 'user_id': True}),
                      json.dumps({**self.session_data, 'firebase_token': ''})):
            with self.subTest(value=value), patch.object(client, 'async_get_clientsession') as get_session:
                result = await self.flow.async_step_reauth_confirm({'session': value})
                self.assertEqual('invalid_session', result['errors']['base'])
                get_session.assert_not_called()
                self.flow.async_update_reload_and_abort.assert_not_called()

    async def test_rejected_session_is_not_saved_or_echoed(self):
        with patch.object(client, 'async_get_clientsession', return_value=FakeSession(FakeResponse(401, {}))):
            result = await self.flow.async_step_reauth_confirm(self.user_input)
        self.assertEqual('invalid_auth', result['errors']['base'])
        self.assertNotIn('synthetic-jwt', str(result))
        self.flow.async_update_reload_and_abort.assert_not_called()

    async def test_reauth_form_masks_session_and_has_no_stored_default(self):
        result = await self.flow.async_step_reauth({})
        self.assertEqual('reauth_confirm', result['step_id'])
        selector = next(iter(result['data_schema'].schema.values()))
        self.assertEqual('password', selector.config['type'])
        self.flow.async_update_reload_and_abort.assert_not_called()


if __name__ == '__main__':
    unittest.main()
