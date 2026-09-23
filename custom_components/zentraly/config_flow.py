"""Config flow for Zentraly integration."""
from __future__ import annotations

import json
import uuid
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import ZentralyApi, ZentralyApiError, ZentralyAuthError
from .const import (
    CONF_DEVICE_GUID, CONF_FIREBASE_TOKEN, CONF_TOKEN, CONF_USER_ID,
    DATA_REAUTH_DRAFTS, DOMAIN,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str}
)
STEP_REAUTH_DATA_SCHEMA = vol.Schema({
    vol.Required("session"): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
})


class ZentralyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zentraly."""

    VERSION = 5

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Log in once and persist the complete effective session."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api = ZentralyApi(
                email=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
                session=aiohttp_client.async_get_clientsession(self.hass),
                device_guid=str(uuid.uuid4()).upper(),
            )
            try:
                await api.authenticate()
                session_data = api.session_data()
            except ZentralyAuthError:
                errors["base"] = "invalid_auth"
            except (ZentralyApiError, aiohttp.ClientError, TimeoutError, OSError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_EMAIL])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Zentraly ({user_input[CONF_EMAIL]})",
                    data={CONF_EMAIL: user_input[CONF_EMAIL],
                          CONF_PASSWORD: user_input[CONF_PASSWORD], **session_data},
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Request a replacement official-app session locally in Home Assistant."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Validate identity read-only before replacing any saved session field."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                supplied = json.loads(user_input["session"])
                if not isinstance(supplied, dict):
                    raise ValueError
                session_data = {key: supplied[key] for key in
                                (CONF_TOKEN, CONF_USER_ID, CONF_FIREBASE_TOKEN, CONF_DEVICE_GUID)}
                if (type(session_data[CONF_USER_ID]) is not int
                        or session_data[CONF_USER_ID] <= 0
                        or not all(isinstance(session_data[key], str) and session_data[key].strip()
                                   for key in (CONF_TOKEN, CONF_FIREBASE_TOKEN, CONF_DEVICE_GUID))):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_session"
            else:
                api = ZentralyApi(
                    **session_data,
                    session=aiohttp_client.async_get_clientsession(self.hass),
                )
                try:
                    result = await api.get_user_data()
                except ZentralyAuthError:
                    errors["base"] = "invalid_auth"
                except (ZentralyApiError, aiohttp.ClientError, TimeoutError, OSError):
                    errors["base"] = "cannot_connect"
                else:
                    user = result["ioData"].get("ioUser")
                    account = user.get("ioDCModel") if isinstance(user, dict) else None
                    account = account if isinstance(account, dict) else {}
                    user_id, email = account.get("ivlngUser"), account.get("ivstrUserEmail")
                    saved_email = entry.data.get(CONF_EMAIL)
                    saved_user_id = entry.data.get(CONF_USER_ID)
                    if (
                        type(user_id) is not int or user_id != session_data[CONF_USER_ID]
                        or (type(saved_user_id) is int and saved_user_id > 0
                            and user_id != saved_user_id)
                        or not isinstance(email, str) or not email.strip()
                        or not isinstance(saved_email, str)
                        or email.strip().casefold() != saved_email.strip().casefold()
                    ):
                        errors["base"] = "wrong_account"
                    else:
                        # Keep the same RAM-only store through the reauth reload and
                        # setup retries. An unloaded entry may already have a handoff.
                        loaded = self.hass.data.get(DOMAIN, {}).get(entry.entry_id)
                        if loaded is not None:
                            self.hass.data.setdefault(DATA_REAUTH_DRAFTS, {})[entry.entry_id] = loaded["drafts"]
                        return self.async_update_reload_and_abort(entry, data_updates=session_data)
        # Never put submitted data in defaults, placeholders or exception logs.
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_REAUTH_DATA_SCHEMA, errors=errors,
        )
