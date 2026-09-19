"""Tests for translating Home Assistant structured output to SystemOne."""

import pytest

from custom_components.jev.ai_task_schema import (
    UnsupportedSchemaError,
    build_translation,
)
from custom_components.jev.api import (
    Choice,
    ChoiceAnswer,
    Noul,
    NoulAnswer,
    Score,
    ScoreAnswer,
)


def test_boolean_becomes_noul_and_respects_threshold() -> None:
    translation = build_translation(
        {
            "type": "object",
            "properties": {
                "occupied": {
                    "type": "boolean",
                    "description": "Is the office occupied?",
                }
            },
            "required": ["occupied"],
        }
    )

    question = translation.questions["occupied"]
    assert isinstance(question, Noul)
    assert question.instructions == "Is the office occupied?"
    for probability, expected in (
        (0.0, False),
        (0.49, False),
        (0.5, True),
        (0.51, True),
        (1.0, True),
    ):
        assert translation.decode({"occupied": NoulAnswer(probability)}) == {
            "occupied": expected
        }


def test_enum_becomes_choice_with_labels_and_rejects_unknown_answer() -> None:
    translation = build_translation(
        {
            "type": "object",
            "properties": {
                "activity": {
                    "type": "string",
                    "enum": ["working", "relaxing"],
                    "description": "What is happening?",
                    "x-systemone-option-labels": {
                        "working": "Focused work",
                        "relaxing": "Taking a break",
                    },
                }
            },
            "required": ["activity"],
        }
    )

    question = translation.questions["activity"]
    assert isinstance(question, Choice)
    assert question.criteria == {
        "working": "Focused work",
        "relaxing": "Taking a break",
    }
    assert translation.decode({"activity": ChoiceAnswer("working", {}, 0.8)}) == {
        "activity": "working"
    }
    with pytest.raises(ValueError, match=r"activity.*unknown choice"):
        translation.decode({"activity": ChoiceAnswer("sleeping", {}, 0.8)})


def test_small_integer_scale_becomes_exact_score_levels() -> None:
    translation = build_translation(
        {
            "type": "object",
            "properties": {
                "urgency": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "multipleOf": 1,
                    "description": "How urgent is this?",
                }
            },
            "required": ["urgency"],
        }
    )

    question = translation.questions["urgency"]
    assert isinstance(question, Score)
    assert question.criteria == ["1", "2", "3", "4", "5"]
    assert translation.decode({"urgency": ScoreAnswer(3.0, {}, {}, 0.9)}) == {
        "urgency": 4
    }


def test_large_float_range_uses_bounded_representative_scale() -> None:
    translation = build_translation(
        {
            "type": "object",
            "properties": {
                "level": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 100,
                    "multipleOf": 0.5,
                    "description": "Choose a level",
                }
            },
            "required": ["level"],
        }
    )

    question = translation.questions["level"]
    assert isinstance(question, Score)
    assert len(question.criteria) == 10
    result = translation.decode({"level": ScoreAnswer(4.5, {}, {}, 0.8)})
    assert result == {"level": 50.0}

    with pytest.raises(ValueError, match=r"outside.*score range"):
        translation.decode({"level": ScoreAnswer(10.0, {}, {}, 0.8)})


@pytest.mark.parametrize(
    ("properties", "field"),
    [
        ({"notes": {"type": "string"}}, "notes"),
        ({"unbounded": {"type": "number", "minimum": 0}}, "unbounded"),
        ({"items": {"type": "array"}}, "items"),
    ],
)
def test_unsupported_fields_reject_entire_task(properties, field) -> None:
    with pytest.raises(UnsupportedSchemaError, match=field):
        build_translation(
            {"type": "object", "properties": properties, "required": [field]}
        )


def test_multiple_fields_share_one_question_mapping() -> None:
    translation = build_translation(
        {
            "type": "object",
            "properties": {
                "occupied": {"type": "boolean", "description": "Occupied?"},
                "activity": {
                    "type": "string",
                    "enum": ["empty", "working"],
                    "description": "Activity?",
                },
                "urgency": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 5,
                    "multipleOf": 1,
                    "description": "Urgency?",
                },
            },
            "required": ["occupied", "activity", "urgency"],
        }
    )

    assert set(translation.questions) == {"occupied", "activity", "urgency"}


def test_missing_or_wrong_answer_type_is_rejected() -> None:
    translation = build_translation(
        {
            "type": "object",
            "properties": {"occupied": {"type": "boolean"}},
            "required": ["occupied"],
        }
    )
    with pytest.raises(ValueError, match=r"missing.*occupied"):
        translation.decode({})
    with pytest.raises(ValueError, match=r"occupied.*noul"):
        translation.decode({"occupied": ChoiceAnswer("yes", {}, 0.5)})
