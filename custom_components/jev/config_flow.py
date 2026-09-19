"""Config and options flow for System One compatible servers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    USD_PER_MILLION_INPUT_TOKENS,
    JevAuthError,
    JevConnectionError,
    JevResponseError,
    JevSSLError,
    JevTimeoutError,
    SystemOneClient,
    normalize_base_url,
)
from .const import (
    CONF_ALLOW_WHOLE_HOME,
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_DAILY_TOKEN_BUDGET,
    CONF_FALLBACK_AGENT,
    CONF_MIN_CONFIDENCE,
    CONF_MODEL,
    CONF_PRICE_PER_MILLION,
    DEFAULT_BASE_URL,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MODEL,
    DOMAIN,
)

SERVER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
        vol.Optional(CONF_API_TOKEN, default=""): str,
        vol.Required(CONF_MODEL, default=DEFAULT_MODEL): str,
    }
)
TOKEN_SCHEMA = vol.Schema({vol.Optional(CONF_API_TOKEN, default=""): str})


class JevConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a System One compatible API."""

    VERSION = 2

    async def _async_validate(self, data: Mapping[str, Any]) -> str | None:
        """Use optional model discovery without consuming inference."""
        client = SystemOneClient(
            session=async_get_clientsession(self.hass),
            base_url=data[CONF_BASE_URL],
            token=data.get(CONF_API_TOKEN),
            model=data[CONF_MODEL],
        )
        try:
            await client.async_validate_connection()
        except JevAuthError:
            return "invalid_auth"
        except JevTimeoutError:
            return "timeout"
        except JevSSLError:
            return "ssl_error"
        except JevResponseError:
            return "incompatible_response"
        except JevConnectionError:
            return "cannot_connect"
        return None

    @staticmethod
    def _normalize_input(user_input: Mapping[str, Any]) -> dict[str, Any]:
        return {
            CONF_BASE_URL: normalize_base_url(user_input[CONF_BASE_URL]),
            CONF_API_TOKEN: user_input.get(CONF_API_TOKEN, "").strip(),
            CONF_MODEL: user_input[CONF_MODEL].strip(),
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = self._normalize_input(user_input)
            except ValueError:
                errors[CONF_BASE_URL] = "invalid_url"
            else:
                await self.async_set_unique_id(data[CONF_BASE_URL])
                self._abort_if_unique_id_configured()
                if error := await self._async_validate(data):
                    errors["base"] = error
                else:
                    hostname = urlsplit(data[CONF_BASE_URL]).hostname
                    return self.async_create_entry(
                        title=f"SystemOne ({hostname})", data=data
                    )
        return self.async_show_form(
            step_id="user", data_schema=SERVER_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            entry = self._get_reauth_entry()
            data = {**entry.data, CONF_API_TOKEN: user_input.get(CONF_API_TOKEN, "")}
            if error := await self._async_validate(data):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_TOKEN: data[CONF_API_TOKEN]}
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=TOKEN_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Swap the API key without removing the integration and losing its entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = self._normalize_input(user_input)
            except ValueError:
                errors[CONF_BASE_URL] = "invalid_url"
            else:
                if error := await self._async_validate(data):
                    errors["base"] = error
                else:
                    return self.async_update_reload_and_abort(
                        self._get_reconfigure_entry(), data_updates=data
                    )
        entry = self._get_reconfigure_entry()
        suggested = {
            CONF_BASE_URL: entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
            CONF_API_TOKEN: entry.data.get(CONF_API_TOKEN, ""),
            CONF_MODEL: entry.data.get(CONF_MODEL, DEFAULT_MODEL),
        }
        schema = self.add_suggested_values_to_schema(SERVER_SCHEMA, suggested)
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> JevOptionsFlow:
        return JevOptionsFlow()


class JevOptionsFlow(OptionsFlow):
    """Spending limits and how the conversation agent should behave.

    The budget is a tripwire, not a quota to run against: put it past anything a
    working configuration would ever use, so only a runaway touches it.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DAILY_TOKEN_BUDGET,
                    default=options.get(CONF_DAILY_TOKEN_BUDGET, 0),
                ): vol.All(vol.Coerce(int), vol.Range(min=0)),
                vol.Optional(
                    CONF_PRICE_PER_MILLION,
                    default=options.get(
                        CONF_PRICE_PER_MILLION, USD_PER_MILLION_INPUT_TOKENS
                    ),
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(
                    CONF_FALLBACK_AGENT,
                    description={"suggested_value": options.get(CONF_FALLBACK_AGENT)},
                ): selector.ConversationAgentSelector(
                    selector.ConversationAgentSelectorConfig()
                ),
                vol.Optional(
                    CONF_MIN_CONFIDENCE,
                    default=options.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
                vol.Optional(
                    CONF_ALLOW_WHOLE_HOME,
                    default=options.get(CONF_ALLOW_WHOLE_HOME, False),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
