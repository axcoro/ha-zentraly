"""Config flow for Zentraly integration."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import ZentralyApi, ZentralyApiError, ZentralyAuthError
from .const import CONF_DEVICE_GUID, CONF_FIREBASE_TOKEN, CONF_TOKEN, CONF_USER_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ZentralyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Zentraly."""

    VERSION = 1

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Recover the existing account using a valid official session."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Validate the account before saving a replacement session."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                data = json.loads(user_input["session"])
                session_data = {key: data[key] for key in (
                    CONF_TOKEN, CONF_USER_ID, CONF_FIREBASE_TOKEN, CONF_DEVICE_GUID,
                )}
                if type(session_data[CONF_USER_ID]) is not int or session_data[CONF_USER_ID] <= 0:
                    raise ValueError
                if any(not isinstance(session_data[key], str) or not session_data[key].strip()
                       for key in (CONF_TOKEN, CONF_FIREBASE_TOKEN, CONF_DEVICE_GUID)):
                    raise ValueError
                api = ZentralyApi(
                    **session_data, session=aiohttp_client.async_get_clientsession(self.hass),
                )
                result = await api.get_user_data()
                account = result["ioData"]["ioUser"]["ioDCModel"]
                if (account["ivlngUser"] != session_data[CONF_USER_ID]
                    or account["ivstrUserEmail"].strip().casefold() != entry.data[CONF_EMAIL].strip().casefold()):
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(entry, data_updates=session_data)
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_session"
            except ZentralyAuthError:
                errors["base"] = "invalid_auth"
            except ZentralyApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                # Do not include session input or server bodies in logs.
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required("session"): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            )}),
            errors=errors,
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            session = aiohttp_client.async_get_clientsession(self.hass)
            device_guid = str(uuid.uuid4()).upper()
            api = ZentralyApi(
                email=user_input[CONF_EMAIL],
                password=user_input[CONF_PASSWORD],
                session=session,
                device_guid=device_guid,
            )

            try:
                await api.authenticate()

                # Check if already configured
                await self.async_set_unique_id(user_input[CONF_EMAIL])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Zentraly ({user_input[CONF_EMAIL]})",
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_DEVICE_GUID: device_guid,
                    },
                )
            except ZentralyAuthError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during Zentraly authentication")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
