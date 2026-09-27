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


class Section:
    """Native HA sections validate their nested schema."""
    def __init__(self, schema, options):
        self.schema, self.options = schema, options

    def __call__(self, value):
        return self.schema(value)


sys.modules['homeassistant.data_entry_flow'].section = Section
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
        self.flow.async_show_menu = Mock(side_effect=lambda **kwargs: kwargs)
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
        self.assertEqual(['credentials', 'reauth_confirm'], result['menu_options'])
        result = await self.flow.async_step_reauth_confirm()
        self.assertEqual('reauth_confirm', result['step_id'])
        selector = next(iter(result['data_schema'].schema.values()))
        self.assertEqual('password', selector.config['type'])
        self.flow.async_update_reload_and_abort.assert_not_called()

    async def test_profile_form_real_schema_and_blank_secret_submission(self):
        """Exercise real voluptuous defaults as HA does, not just form stubs."""
        self.entry.data.update(self.session_data | {'password': 'synthetic-password'})
        await self.flow.async_step_reauth({})
        credentials = await self.flow.async_step_credentials()
        form = await self.flow.async_step_credentials(credentials['data_schema']({}))
        self.assertEqual('auth_profile', form['step_id'])
        values = form['data_schema']({
            'mobile_trade': 'Synthetic', 'mobile_model': 'Synthetic phone',
            'mobile_os_version': 33, 'country': 'AR',
        })
        self.assertEqual(1, values['mobile_os'])
        self.assertEqual('7.2.0', values['app_version'])
        for secret in ('device_guid', 'firebase_token', 'password'):
            self.assertNotIn(secret, values)
        account = {'ivlngUser': 42, 'ivstrUserEmail': 'test@example.invalid'}
        session = FakeSession(
            FakeResponse(200, {'numStatus': 0, 'ioData': {
                'ivstrToken': 'synthetic-replacement', 'ioUser': {'ioDCModel': account}}}),
            FakeResponse(200, {'numStatus': 0, 'ioData': {'ioUser': {
                'ioDCModel': account, 'coUbications': []}}}),
        )
        with patch.object(client, 'async_get_clientsession', return_value=session):
            result = await self.flow.async_step_auth_profile(values)
        self.assertEqual('reauth_successful', result['reason'])
        update = self.flow.async_update_reload_and_abort.call_args.kwargs['data_updates']
        self.assertEqual('synthetic-password', update['password'])
        self.assertEqual('synthetic-firebase', update['firebase_token'])
        self.assertEqual(33, update['auth_profile']['mobile_os_version'])

    async def test_configured_secrets_are_collapsed_and_replacements_are_nested(self):
        self.entry.data.update(self.session_data | {'password': 'synthetic-password'})
        await self.flow.async_step_reauth({})
        form = await self.flow.async_step_credentials()
        schema = form['data_schema'].schema
        self.assertNotIn('password', schema)
        section = schema['change_password']
        self.assertTrue(section.options['collapsed'])
        self.assertEqual({}, section.schema({}))
        for marker in section.schema.schema:
            self.assertIs(marker.default, __import__('voluptuous').UNDEFINED)
        profile_form = await self.flow.async_step_credentials(form['data_schema']({
            'change_password': {'password': 'replacement-password'},
        }))
        for key in ('firebase_token', 'device_guid'):
            section = profile_form['data_schema'].schema['change_' + key]
            self.assertTrue(section.options['collapsed'])
            self.assertEqual({}, section.schema({}))
        submitted = profile_form['data_schema']({
            'change_firebase_token': {'firebase_token': 'replacement-fcm'},
            'change_device_guid': {},
            'mobile_trade': 'Synthetic', 'mobile_model': 'Synthetic phone',
            'mobile_os_version': 33, 'country': 'AR',
        })
        account = {'ivlngUser': 42, 'ivstrUserEmail': 'test@example.invalid'}
        session = FakeSession(
            FakeResponse(200, {'numStatus': 0, 'ioData': {
                'ivstrToken': 'synthetic-replacement', 'ioUser': {'ioDCModel': account}}}),
            FakeResponse(200, {'numStatus': 0, 'ioData': {'ioUser': {
                'ioDCModel': account, 'coUbications': []}}}),
        )
        with patch.object(client, 'async_get_clientsession', return_value=session):
            await self.flow.async_step_auth_profile(submitted)
        update = self.flow.async_update_reload_and_abort.call_args.kwargs['data_updates']
        self.assertEqual('replacement-password', update['password'])
        self.assertEqual('replacement-fcm', update['firebase_token'])
        self.assertEqual('synthetic-guid', update['device_guid'])


if __name__ == '__main__':
    unittest.main()
