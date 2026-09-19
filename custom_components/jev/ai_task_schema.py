"""Translate Home Assistant structured AI Tasks into SystemOne questions."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .api import (
    MAX_SCORE_LEVELS,
    Answer,
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Question,
    Score,
    ScoreAnswer,
)

DEFAULT_NOUL_THRESHOLD = 0.5


class UnsupportedSchemaError(ValueError):
    """A requested output cannot be represented by SystemOne primitives."""


@dataclass(frozen=True, slots=True)
class _BooleanField:
    threshold: float


@dataclass(frozen=True, slots=True)
class _ChoiceField:
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _NumberField:
    minimum: float
    maximum: float
    step: float | None
    integer: bool
    level_count: int


type _Field = _BooleanField | _ChoiceField | _NumberField


@dataclass(frozen=True, slots=True)
class SchemaTranslation:
    """SystemOne questions plus strict decoding rules for their answers."""

    questions: dict[str, Question]
    fields: dict[str, _Field]

    def decode(self, answers: Mapping[str, Answer]) -> dict[str, Any]:
        """Convert typed answers back into the exact requested HA value types."""
        result: dict[str, Any] = {}
        for name, field in self.fields.items():
            if name not in answers:
                raise ValueError(f"SystemOne response is missing answer {name!r}")
            answer = answers[name]
            if isinstance(field, _BooleanField):
                if not isinstance(answer, NoulAnswer):
                    raise ValueError(f"field {name!r} requires a noul answer")
                result[name] = answer.noul >= field.threshold
            elif isinstance(field, _ChoiceField):
                if not isinstance(answer, ChoiceAnswer):
                    raise ValueError(f"field {name!r} requires a choice answer")
                if answer.choice not in field.options:
                    raise ValueError(
                        f"field {name!r} returned unknown choice {answer.choice!r}"
                    )
                result[name] = answer.choice
            else:
                if not isinstance(answer, ScoreAnswer):
                    raise ValueError(f"field {name!r} requires a score answer")
                if not math.isfinite(answer.score) or not 0 <= answer.score <= (
                    field.level_count - 1
                ):
                    raise ValueError(f"field {name!r} is outside the legal score range")
                fraction = answer.score / (field.level_count - 1)
                value = field.minimum + fraction * (field.maximum - field.minimum)
                if field.step is not None:
                    steps = round((value - field.minimum) / field.step)
                    value = field.minimum + steps * field.step
                value = min(field.maximum, max(field.minimum, value))
                result[name] = round(value) if field.integer else float(value)
        return result


def _number_levels(
    minimum: float, maximum: float, step: float | None
) -> tuple[list[float], int]:
    if step is not None:
        intervals = (maximum - minimum) / step
        rounded_intervals = round(intervals)
        if math.isclose(intervals, rounded_intervals, rel_tol=1e-9, abs_tol=1e-9):
            count = rounded_intervals + 1
            if 2 <= count <= MAX_SCORE_LEVELS:
                return [minimum + index * step for index in range(count)], count

    count = MAX_SCORE_LEVELS
    width = maximum - minimum
    return [minimum + width * index / (count - 1) for index in range(count)], count


def _format_level(value: float, integer: bool) -> str:
    if integer or value.is_integer():
        return str(round(value))
    return format(value, ".12g")


def build_translation(
    schema: Mapping[str, Any],
    *,
    noul_threshold: float = DEFAULT_NOUL_THRESHOLD,
) -> SchemaTranslation:
    """Build all questions before any network request is made."""
    if schema.get("type") != "object" or not isinstance(
        properties := schema.get("properties"), Mapping
    ):
        raise UnsupportedSchemaError("AI Task output must be an object with fields")
    if not properties:
        raise UnsupportedSchemaError("AI Task output must contain at least one field")

    questions: dict[str, Question] = {}
    fields: dict[str, _Field] = {}
    unsupported: list[str] = []

    for raw_name, raw_field in properties.items():
        name = str(raw_name)
        if not isinstance(raw_field, Mapping):
            unsupported.append(name)
            continue
        field_type = raw_field.get("type")
        instructions = raw_field.get("description") or name.replace("_", " ")

        if field_type == "boolean":
            questions[name] = Noul(instructions=instructions)
            fields[name] = _BooleanField(noul_threshold)
            continue

        options = raw_field.get("enum")
        if field_type == "string" and isinstance(options, list) and len(options) >= 2:
            if not all(isinstance(option, str) for option in options):
                unsupported.append(name)
                continue
            labels = raw_field.get("x-systemone-option-labels", {})
            criteria = {
                option: labels.get(option) if isinstance(labels, Mapping) else None
                for option in options
            }
            questions[name] = Choice(instructions=instructions, criteria=criteria)
            fields[name] = _ChoiceField(tuple(options))
            continue

        if field_type in {"number", "integer"}:
            minimum = raw_field.get("minimum")
            maximum = raw_field.get("maximum")
            step = raw_field.get("multipleOf")
            if (
                not isinstance(minimum, (int, float))
                or isinstance(minimum, bool)
                or not isinstance(maximum, (int, float))
                or isinstance(maximum, bool)
                or not math.isfinite(float(minimum))
                or not math.isfinite(float(maximum))
                or minimum >= maximum
                or (
                    step is not None
                    and (
                        not isinstance(step, (int, float))
                        or isinstance(step, bool)
                        or not math.isfinite(float(step))
                        or step <= 0
                    )
                )
            ):
                unsupported.append(name)
                continue
            numeric_step = float(step) if step is not None else None
            levels, level_count = _number_levels(
                float(minimum), float(maximum), numeric_step
            )
            integer = field_type == "integer"
            questions[name] = Score(
                instructions=instructions,
                criteria=[_format_level(value, integer) for value in levels],
            )
            fields[name] = _NumberField(
                float(minimum),
                float(maximum),
                numeric_step,
                integer,
                level_count,
            )
            continue

        unsupported.append(name)

    if unsupported:
        joined = ", ".join(repr(name) for name in unsupported)
        raise UnsupportedSchemaError(
            "SystemOne supports boolean, select/enum, and bounded numeric fields; "
            f"unsupported field(s): {joined}"
        )

    return SchemaTranslation(questions=questions, fields=fields)


__all__ = [
    "DEFAULT_NOUL_THRESHOLD",
    "SchemaTranslation",
    "UnsupportedSchemaError",
    "build_translation",
]
