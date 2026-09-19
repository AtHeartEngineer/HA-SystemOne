"""Fixtures. Nothing here talks to TypeSafe: the client is replaced everywhere."""

from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jev.api import (
    ChoiceAnswer,
    JevResponse,
    NoulAnswer,
    ScoreAnswer,
    Usage,
)
from custom_components.jev.const import (
    CONF_API_TOKEN,
    CONF_BASE_URL,
    CONF_MODEL,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DOMAIN,
)

API_KEY = "test-key-not-a-real-one"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Without this the custom component is never loaded."""
    return


def build_response(**answers) -> JevResponse:
    return JevResponse(
        model="jev-1.13.0",
        answers=answers,
        usage=Usage(input_tokens=321, output_tokens=42),
        latency_ms=274.0,
    )


@pytest.fixture
def answers() -> dict:
    """One answer of each type, keyed the way the single-question actions key them."""
    return {
        "answer": NoulAnswer(noul=0.81),
    }


@pytest.fixture
def mock_client(answers):
    """Replace the generic client everywhere it is constructed."""
    client = AsyncMock()
    client.ask = AsyncMock(return_value=build_response(**answers))
    client.system_one = client.ask
    client.async_validate_connection = AsyncMock(return_value=[DEFAULT_MODEL])
    client.base_url = DEFAULT_BASE_URL
    client.model = DEFAULT_MODEL
    client.authenticated = True
    with (
        patch("custom_components.jev.SystemOneClient", return_value=client),
        patch("custom_components.jev.config_flow.SystemOneClient", return_value=client),
    ):
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="SystemOne (api.typesafe.ai)",
        data={
            CONF_BASE_URL: DEFAULT_BASE_URL,
            CONF_API_TOKEN: API_KEY,
            CONF_MODEL: DEFAULT_MODEL,
        },
        options={},
        unique_id="0123456789abcdef",
    )


@pytest.fixture
async def loaded_entry(hass, mock_client, config_entry):
    """A config entry that is set up, with no YAML contexts."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


__all__ = ["ChoiceAnswer", "ScoreAnswer", "build_response"]
