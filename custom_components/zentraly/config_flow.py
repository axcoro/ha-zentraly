"""Config flow for Zentraly integration."""
from __future__ import annotations

import json
import secrets
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult, section
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import ZentralyApi, ZentralyApiError, ZentralyAuthError, validate_auth_profile
from .const import (
    AUTH_PROFILE_FIELDS, CONF_AUTH_PROFILE, CONF_DEVICE_GUID, CONF_FIREBASE_TOKEN,
    CONF_TOKEN, CONF_USER_ID, DATA_REAUTH_DRAFTS, DOMAIN, ZENTRALY_APP_VERSION,
)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
STEP_REAUTH_DATA_SCHEMA = vol.Schema({vol.Required("session"): PASSWORD_SELECTOR})
SESSION_KEYS = (CONF_TOKEN, CONF_USER_ID, CONF_FIREBASE_TOKEN, CONF_DEVICE_GUID)
DATA_AUTH_IN_PROGRESS = f"{DOMAIN}_auth_in_progress"


def _valid_secret(value: Any) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 8192
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _secret_field(key: str, current: Any) -> dict:
    """Show a configured indicator; expanding Change reveals an empty input."""
    if _valid_secret(current):
        return {vol.Optional(f"change_{key}"): section(
            vol.Schema({vol.Optional(key): PASSWORD_SELECTOR}), {"collapsed": True},
        )}
    return {vol.Required(key): PASSWORD_SELECTOR}


def _replacement(user_input: dict[str, Any], key: str) -> Any:
    """Empty replacement keeps the server-side value; never use a mask as data."""
    change = user_input.get(f"change_{key}", {})
    if not isinstance(change, dict):
        return None
    return change.get(key, user_input.get(key, ""))


class ZentralyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Authenticate explicitly, then atomically replace the verified session."""

    VERSION = 5

    def __init__(self) -> None:
        super().__init__()
        self._entry = None
        self._entry_data: dict[str, Any] = {}
        self._candidate: dict[str, Any] = {}
        self._profile: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Collect credentials without making a request."""
        return await self._async_credentials(user_input, step_id="user")

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Offer profile login or an imported session for the existing account."""
        self._entry = self._get_reauth_entry()
        return self._auth_menu("reauth")

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Edit authentication from the integration menu."""
        self._entry = self._get_reconfigure_entry()
        return self._auth_menu("reconfigure")

    def _auth_menu(self, step_id: str) -> FlowResult:
        self._entry_data = dict(self._entry.data)
        self._candidate = {}
        self._profile = {}
        return self.async_show_menu(step_id=step_id, menu_options=["credentials", "reauth_confirm"])

    async def async_step_credentials(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Keep the configured email fixed while allowing a password replacement."""
        return await self._async_credentials(user_input, step_id="credentials")

    async def _async_credentials(self, user_input: dict[str, Any] | None, *, step_id: str) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            saved = self._entry_data
            email = saved.get(CONF_EMAIL) if self._entry else user_input.get(CONF_EMAIL)
            password = _replacement(user_input, CONF_PASSWORD)
            if password == "":
                password = self._candidate.get(CONF_PASSWORD, saved.get(CONF_PASSWORD))
            if not _valid_secret(email) or ":" in email or not _valid_secret(password):
                errors["base"] = "invalid_credentials"
            else:
                if self._entry is None:
                    if self._account_configured(email):
                        return self.async_abort(reason="already_configured")
                    await self.async_set_unique_id(email.strip().casefold())
                    self._abort_if_unique_id_configured()
                self._candidate.update({CONF_EMAIL: email if self._entry else email.strip(), CONF_PASSWORD: password})
                if not self._candidate.get(CONF_DEVICE_GUID):
                    self._candidate[CONF_DEVICE_GUID] = saved.get(CONF_DEVICE_GUID)
                    if self._entry is None:
                        self._candidate[CONF_DEVICE_GUID] = secrets.token_hex(8)
                self._candidate.setdefault(CONF_FIREBASE_TOKEN, saved.get(CONF_FIREBASE_TOKEN))
                if not self._profile:
                    profile = saved.get(CONF_AUTH_PROFILE)
                    self._profile = dict(profile) if isinstance(profile, dict) else {}
                return await self.async_step_auth_profile()
        fields = {} if self._entry else {vol.Required(CONF_EMAIL): str}
        fields.update(_secret_field(CONF_PASSWORD, self._candidate.get(CONF_PASSWORD, self._entry_data.get(CONF_PASSWORD))))
        return self.async_show_form(
            step_id=step_id, data_schema=vol.Schema(fields), errors=errors, last_step=False,
        )

    async def async_step_auth_profile(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Make one explicit login only after local profile validation."""
        if not self._candidate.get(CONF_EMAIL):
            return await self.async_step_credentials()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                profile = validate_auth_profile({key: user_input[key]
                                                 for key in AUTH_PROFILE_FIELDS.keys() | {"user_agent"}})
                candidate = dict(self._candidate)
                for key in (CONF_DEVICE_GUID, CONF_FIREBASE_TOKEN):
                    value = _replacement(user_input, key)
                    if value != "":
                        candidate[key] = value
                    if not _valid_secret(candidate.get(key)):
                        raise ValueError
                candidate[CONF_AUTH_PROFILE] = profile
            except (KeyError, ValueError, TypeError):
                errors["base"] = "invalid_profile"
            else:
                self._candidate, self._profile = candidate, profile
                result, error = await self._async_validate_and_save(candidate, login=True)
                if result is not None:
                    return result
                errors["base"] = error

        config = getattr(self.hass, "config", None)
        language = getattr(config, "language", "es") or "es"
        defaults = {"app_version": ZENTRALY_APP_VERSION, "mobile_os": 1,
                    "language": language.split("-")[0].lower(), "user_agent": "zentralyRN/420"}
        country = getattr(config, "country", None)
        if isinstance(country, str) and len(country) == 2:
            defaults["country"] = country.upper()
        defaults.update(self._profile)
        fields = {}
        for key in (CONF_DEVICE_GUID, CONF_FIREBASE_TOKEN):
            fields.update(_secret_field(key, self._candidate.get(key)))
        for key in (*AUTH_PROFILE_FIELDS, "user_agent"):
            options = {"default": defaults[key]} if key in defaults else {}
            fields[vol.Required(key, **options)] = int if key in ("mobile_os", "mobile_os_version") else str
        return self.async_show_form(
            step_id="auth_profile", data_schema=vol.Schema(fields), errors=errors, last_step=True,
        )

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Keep importing a session available without requiring a new profile."""
        if self._entry is None:
            self._entry = self._get_reauth_entry()
            self._entry_data = dict(self._entry.data)
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                supplied = json.loads(user_input["session"])
                candidate = {key: supplied[key] for key in SESSION_KEYS}
                if (type(candidate[CONF_USER_ID]) is not int or candidate[CONF_USER_ID] <= 0
                        or not all(_valid_secret(candidate[key])
                                   for key in (CONF_TOKEN, CONF_FIREBASE_TOKEN, CONF_DEVICE_GUID))):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_session"
            else:
                result, error = await self._async_validate_and_save(candidate, login=False)
                if result is not None:
                    return result
                errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_REAUTH_DATA_SCHEMA, errors=errors,
        )

    def _account_configured(self, email: str) -> bool:
        """Recognize legacy email IDs without changing existing identities."""
        normalized = email.strip().casefold()
        return any(
            isinstance(value, str) and value.strip().casefold() == normalized
            for entry in self._async_current_entries()
            for value in (entry.data.get(CONF_EMAIL), entry.unique_id)
        )

    async def _async_validate_and_save(self, candidate: dict[str, Any], *, login: bool) -> tuple[FlowResult | None, str]:
        """Share same-account validation and serialize edits for this account."""
        saved = self._entry_data
        email = saved.get(CONF_EMAIL) if self._entry else candidate[CONF_EMAIL]
        if not _valid_secret(email):
            return None, "wrong_account"
        if self._entry is None and self._account_configured(email):
            return self.async_abort(reason="already_configured"), ""
        account_key = email.strip().casefold()
        busy = self.hass.data.setdefault(DATA_AUTH_IN_PROGRESS, set())
        if account_key in busy:
            return None, "auth_in_progress"
        if self._entry and dict(self._entry.data) != saved:
            return None, "configuration_changed"
        busy.add(account_key)
        try:
            profile = candidate.get(CONF_AUTH_PROFILE) if login else saved.get(CONF_AUTH_PROFILE)
            if CONF_AUTH_PROFILE in saved and not login:
                profile = validate_auth_profile(profile)
            api = ZentralyApi(
                email=email.strip(), password=candidate.get(CONF_PASSWORD),
                token=None if login else candidate[CONF_TOKEN],
                user_id=None if login else candidate[CONF_USER_ID],
                firebase_token=candidate[CONF_FIREBASE_TOKEN], device_guid=candidate[CONF_DEVICE_GUID],
                auth_profile=profile, session=aiohttp_client.async_get_clientsession(self.hass),
            )
            if login:
                await api.authenticate()
            session_data = api.session_data()
            response = await api.get_user_data()
            user = response["ioData"].get("ioUser")
            account = user.get("ioDCModel") if isinstance(user, dict) else None
            account = account if isinstance(account, dict) else {}
            user_id, actual_email = account.get("ivlngUser"), account.get("ivstrUserEmail")
            saved_id = saved.get(CONF_USER_ID)
            if (type(user_id) is not int or user_id != session_data[CONF_USER_ID]
                    or (type(saved_id) is int and saved_id > 0 and user_id != saved_id)
                    or not isinstance(actual_email, str)
                    or actual_email.strip().casefold() != account_key):
                return None, "wrong_account"
            if login:
                api.parse_devices(response)
            updates = {**candidate, **session_data}
            if self._entry is None:
                if self._account_configured(email):
                    return self.async_abort(reason="already_configured"), ""
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"Zentraly ({email})", data=updates), ""
            loaded = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
            if loaded is not None:
                self.hass.data.setdefault(DATA_REAUTH_DRAFTS, {})[self._entry.entry_id] = loaded["drafts"]
            result = self.async_update_reload_and_abort(self._entry, data_updates=updates)
            self._entry_data = dict(self._entry.data)
            return result, ""
        except ValueError:
            return None, "invalid_profile"
        except ZentralyAuthError:
            return None, "invalid_auth"
        except (ZentralyApiError, aiohttp.ClientError, TimeoutError, OSError):
            return None, "cannot_connect"
        finally:
            busy.remove(account_key)
