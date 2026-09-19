"""Configuration flows for hosted and self-hosted System One servers."""

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.jev.api import JevAuthError, JevConnectionError
from custom_components.jev.const import (
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_DAILY_TOKEN_BUDGET,
    CONF_MODEL,
    CONF_PRICE_PER_MILLION,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DOMAIN,
)

from .conftest import API_KEY


async def _start_user_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_typesafe_with_token_creates_entry(hass, mock_client):
    result = await _start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BASE_URL: "https://api.typesafe.ai/v1/systemone",
            CONF_API_TOKEN: API_KEY,
            CONF_MODEL: "jev-latest",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SystemOne (api.typesafe.ai)"
    assert result["data"] == {
        CONF_BASE_URL: DEFAULT_BASE_URL,
        CONF_API_TOKEN: API_KEY,
        CONF_MODEL: DEFAULT_MODEL,
    }
    mock_client.async_validate_connection.assert_awaited_once_with()
    mock_client.ask.assert_not_awaited()


async def test_self_hosted_without_token_creates_entry(hass, mock_client):
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BASE_URL: "http://192.168.1.50:8000/v1/",
            CONF_API_TOKEN: "",
            CONF_MODEL: "qwen3.8-flash-next",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_BASE_URL: "http://192.168.1.50:8000",
        CONF_API_TOKEN: "",
        CONF_MODEL: "qwen3.8-flash-next",
    }


async def test_invalid_url_stays_on_form_without_calling_server(hass, mock_client):
    result = await _start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BASE_URL: "server:8000",
            CONF_API_TOKEN: "",
            CONF_MODEL: DEFAULT_MODEL,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BASE_URL: "invalid_url"}
    mock_client.async_validate_connection.assert_not_awaited()


async def test_connection_and_auth_errors_are_distinct(hass, mock_client):
    result = await _start_user_flow(hass)
    mock_client.async_validate_connection.side_effect = JevAuthError("no")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_API_TOKEN: "wrong",
            CONF_MODEL: DEFAULT_MODEL,
        },
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_client.async_validate_connection.side_effect = JevConnectionError("offline")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_API_TOKEN: API_KEY,
            CONF_MODEL: DEFAULT_MODEL,
        },
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_updates_server_settings(hass, mock_client, loaded_entry):
    result = await loaded_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_BASE_URL: "http://r9v-server:8000/v1",
            CONF_API_TOKEN: "",
            CONF_MODEL: "Qwen/Qwen3.8-Flash-Next",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert loaded_entry.data[CONF_BASE_URL] == "http://r9v-server:8000"
    assert loaded_entry.data[CONF_API_TOKEN] == ""
    assert loaded_entry.data[CONF_MODEL] == "Qwen/Qwen3.8-Flash-Next"


async def test_options_flow_keeps_existing_budget_behavior(hass, loaded_entry):
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_DAILY_TOKEN_BUDGET: 50_000, CONF_PRICE_PER_MILLION: 0.042},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert loaded_entry.options[CONF_DAILY_TOKEN_BUDGET] == 50_000
