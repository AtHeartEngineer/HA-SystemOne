"""Native Home Assistant AI Task provider for SystemOne decision APIs."""

from __future__ import annotations

import time
from typing import Any, override

import voluptuous as vol
from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm, selector
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from probatio import UNSUPPORTED, to_openapi

from .ai_task_schema import UnsupportedSchemaError, build_translation
from .api import JevError
from .const import CONF_MODEL, DEFAULT_MODEL
from .entity import build_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the SystemOne AI Task entity."""
    async_add_entities([SystemOneAITaskEntity(config_entry)])


def _system_one_selector_serializer(schema: Any) -> Any:
    """Preserve numeric steps and select labels omitted by HA's base serializer."""
    result = llm.selector_serializer(schema)
    if result is UNSUPPORTED:
        return UNSUPPORTED
    if not isinstance(result, dict):
        return result
    result = dict(result)
    if isinstance(schema, selector.NumberSelector):
        step = schema.config.get("step")
        if isinstance(step, (int, float)):
            result["multipleOf"] = step
    elif isinstance(schema, selector.SelectSelector):
        labels = {
            option["value"]: option["label"]
            for option in schema.config["options"]
            if isinstance(option, dict)
        }
        if labels:
            result["x-systemone-option-labels"] = labels
    return result


class SystemOneAITaskEntity(ai_task.AITaskEntity):
    """Turn structured HA fields into one parallel SystemOne request."""

    _attr_has_entity_name = True
    _attr_name = "AI Task"
    _attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_DATA

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_ai_task"
        self._attr_device_info = build_device_info(
            config_entry.entry_id, config_entry.runtime_data
        )

    @override
    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Evaluate every requested output field in one API call."""
        if task.structure is None:
            raise HomeAssistantError(
                "SystemOne AI Task requires structured output. SystemOne does not "
                "generate free-form text."
            )

        try:
            schema = to_openapi(
                task.structure,
                custom_serializer=_system_one_selector_serializer,
                strict=True,
            )
            translation = build_translation(schema)
        except UnsupportedSchemaError as err:
            raise HomeAssistantError(str(err)) from err
        except Exception as err:
            raise HomeAssistantError(
                f"SystemOne could not read the requested output schema: {err}"
            ) from err

        started = time.monotonic()
        try:
            response = await self._entry.runtime_data.client.system_one(
                state=task.instructions,
                questions=translation.questions,
                model=self._entry.data.get(CONF_MODEL, DEFAULT_MODEL),
            )
            data = translation.decode(response.answers)
            data = task.structure(data)
        except JevError as err:
            raise HomeAssistantError(f"SystemOne request failed: {err}") from err
        except (ValueError, vol.Invalid) as err:
            raise HomeAssistantError(
                f"SystemOne returned data that does not match the requested schema: {err}"
            ) from err

        self._entry.runtime_data.last_ai_task = {
            "latency_ms": round(
                response.latency_ms or (time.monotonic() - started) * 1000
            ),
            "input_tokens": response.usage.input_tokens,
            "question_count": len(translation.questions),
        }
        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )
