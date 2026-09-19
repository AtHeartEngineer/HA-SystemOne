"""Tests for the native Home Assistant AI Task entity."""

from types import SimpleNamespace

import pytest
import voluptuous as vol
from homeassistant.components import ai_task
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from custom_components.jev.ai_task import SystemOneAITaskEntity
from custom_components.jev.api import NoulAnswer

from .conftest import ChoiceAnswer, ScoreAnswer, build_response


def _entity(config_entry, mock_client) -> SystemOneAITaskEntity:
    config_entry.runtime_data = SimpleNamespace(
        client=mock_client,
        model_version=None,
        last_ai_task=None,
    )
    return SystemOneAITaskEntity(config_entry)


@pytest.mark.asyncio
async def test_three_fields_become_one_system_one_request(
    config_entry, mock_client
) -> None:
    mock_client.system_one.return_value = build_response(
        occupied=NoulAnswer(0.94),
        activity=ChoiceAnswer("working", {}, 0.91),
        confidence_needed=ScoreAnswer(4.0, {}, {}, 0.8),
    )
    structure = vol.Schema(
        {
            vol.Required(
                "occupied", description="Is someone currently in the office?"
            ): selector.BooleanSelector(),
            vol.Required(
                "activity", description="What is the most likely state?"
            ): selector.SelectSelector(
                {"options": ["empty", "working", "relaxing", "uncertain"]}
            ),
            vol.Required(
                "confidence_needed", description="How strongly should we rely on it?"
            ): selector.NumberSelector({"min": 0, "max": 5, "step": 1}),
        }
    )
    task = ai_task.GenDataTask(
        name="test_house",
        instructions="Office presence sensor: on.",
        structure=structure,
    )

    result = await _entity(config_entry, mock_client)._async_generate_data(
        task, SimpleNamespace(conversation_id="conversation-1")
    )

    assert result.data == {
        "occupied": True,
        "activity": "working",
        "confidence_needed": 4.0,
    }
    mock_client.system_one.assert_awaited_once()
    request = mock_client.system_one.await_args.kwargs
    assert request["state"] == task.instructions
    assert set(request["questions"]) == {
        "occupied",
        "activity",
        "confidence_needed",
    }


@pytest.mark.asyncio
async def test_select_display_labels_are_preserved(config_entry, mock_client) -> None:
    mock_client.system_one.return_value = build_response(
        priority=ChoiceAnswer("urgent", {}, 0.9)
    )
    task = ai_task.GenDataTask(
        name="priority",
        instructions="Smoke detected.",
        structure=vol.Schema(
            {
                vol.Required("priority", description="How urgent?"): (
                    selector.SelectSelector(
                        {
                            "options": [
                                {"value": "normal", "label": "Normal priority"},
                                {
                                    "value": "urgent",
                                    "label": "Urgent attention required",
                                },
                            ]
                        }
                    )
                )
            }
        ),
    )

    await _entity(config_entry, mock_client)._async_generate_data(
        task, SimpleNamespace(conversation_id="conversation-2")
    )

    question = mock_client.system_one.await_args.kwargs["questions"]["priority"]
    assert question.criteria == {
        "normal": "Normal priority",
        "urgent": "Urgent attention required",
    }


@pytest.mark.asyncio
async def test_missing_structure_is_rejected_before_api_call(
    config_entry, mock_client
) -> None:
    task = ai_task.GenDataTask(name="free_text", instructions="Tell me a story")
    with pytest.raises(HomeAssistantError, match="requires structured output"):
        await _entity(config_entry, mock_client)._async_generate_data(
            task, SimpleNamespace(conversation_id="conversation-3")
        )
    mock_client.system_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_field_is_rejected_before_api_call(config_entry, mock_client) -> None:
    task = ai_task.GenDataTask(
        name="free_text",
        instructions="Summarize this",
        structure=vol.Schema(
            {
                vol.Required("summary", description="Write a summary"): (
                    selector.TextSelector()
                )
            }
        ),
    )
    with pytest.raises(HomeAssistantError, match=r"unsupported.*summary"):
        await _entity(config_entry, mock_client)._async_generate_data(
            task, SimpleNamespace(conversation_id="conversation-4")
        )
    mock_client.system_one.assert_not_awaited()
