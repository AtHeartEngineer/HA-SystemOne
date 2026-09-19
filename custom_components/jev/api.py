"""Async client for the generic SystemOne API contract."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 30.0
SYSTEM_ONE_PATH = "/v1/systemone"
MODELS_PATH = "/v1/models"
MIN_CHOICE_OPTIONS = 2
MAX_CHOICE_OPTIONS = 255
MIN_SCORE_LEVELS = 2
MAX_SCORE_LEVELS = 10
USD_PER_MILLION_INPUT_TOKENS = 0.042

type EntryType = str | Mapping[str, Any] | Sequence[Any] | None


class JevError(Exception):
    """Base class for System One client errors."""


class JevConnectionError(JevError):
    """The request did not complete."""


class JevTimeoutError(JevConnectionError):
    """The server did not answer before the configured timeout."""


class JevSSLError(JevConnectionError):
    """TLS validation or negotiation failed."""


class JevAuthError(JevError):
    """The server rejected authentication."""


class JevValidationError(JevError):
    """The server rejected the request shape."""


class JevRateLimitError(JevError):
    """The server rate limited the request."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class JevOverloadedError(JevError):
    """The server is temporarily overloaded."""


class JevResponseError(JevError):
    """The server returned an unreadable response."""


@dataclass(slots=True)
class Noul:
    """A yes/no question returning a probability."""

    instructions: EntryType
    true: EntryType = None
    false: EntryType = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "noul", "instructions": self.instructions}
        if self.true is not None or self.false is not None:
            payload["criteria"] = {"true": self.true or "", "false": self.false or ""}
        return payload


@dataclass(slots=True)
class Choice:
    """A question that selects one named option."""

    instructions: EntryType
    criteria: Mapping[str, EntryType]

    def __post_init__(self) -> None:
        if not MIN_CHOICE_OPTIONS <= len(self.criteria) <= MAX_CHOICE_OPTIONS:
            raise ValueError(
                f"a choice takes {MIN_CHOICE_OPTIONS} to {MAX_CHOICE_OPTIONS} "
                f"options, got {len(self.criteria)}"
            )

    def as_payload(self) -> dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }


@dataclass(slots=True)
class Score:
    """A question rated against ordered levels."""

    instructions: EntryType
    criteria: Sequence[EntryType]

    def __post_init__(self) -> None:
        if not MIN_SCORE_LEVELS <= len(self.criteria) <= MAX_SCORE_LEVELS:
            raise ValueError(
                f"a score takes {MIN_SCORE_LEVELS} to {MAX_SCORE_LEVELS} levels, "
                f"got {len(self.criteria)}"
            )

    def as_payload(self) -> dict[str, Any]:
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": list(self.criteria),
        }


type Question = Noul | Choice | Score


@dataclass(slots=True)
class NoulAnswer:
    noul: float

    @property
    def value(self) -> float:
        return self.noul


@dataclass(slots=True)
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float]
    confidence: float

    @property
    def value(self) -> str:
        return self.choice


@dataclass(slots=True)
class ScoreAnswer:
    score: float
    legend: dict[str, str]
    probabilities: dict[str, float]
    confidence: float

    @property
    def value(self) -> float:
        return self.score

    @property
    def nearest_level(self) -> str:
        return self.legend.get(str(round(self.score)), "")

    @property
    def normalized(self) -> float:
        return self.score / max(len(self.legend) - 1, 1)


type Answer = NoulAnswer | ChoiceAnswer | ScoreAnswer


@dataclass(slots=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(slots=True)
class JevResponse:
    model: str
    answers: dict[str, Answer] = field(default_factory=dict)
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    latency_ms: float = 0.0

    def __getitem__(self, key: str) -> Answer:
        return self.answers[key]


def normalize_base_url(value: str) -> str:
    """Return a canonical API base without a known System One suffix."""
    raw = value.strip().rstrip("/")
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("base URL must be a valid HTTP URL without credentials")

    path = parsed.path.rstrip("/")
    if path.endswith(SYSTEM_ONE_PATH):
        path = path[: -len(SYSTEM_ONE_PATH)]
    elif path.endswith("/v1"):
        path = path[:-3]
    path = path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def system_one_url(base_url: str) -> str:
    """Return the System One request endpoint for a user-supplied base URL."""
    return f"{normalize_base_url(base_url)}{SYSTEM_ONE_PATH}"


def models_url(base_url: str) -> str:
    """Return the optional model-discovery endpoint."""
    return f"{normalize_base_url(base_url)}{MODELS_PATH}"


class SystemOneClient:
    """Client for the common TypeSafe Jev-compatible SystemOne contract."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        base_url: str = DEFAULT_BASE_URL,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.model = model
        self.authenticated = bool(token and token.strip())
        self._token = token.strip() if token else None
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def _headers(self) -> dict[str, str]:
        if not self._token:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    async def async_validate_connection(self) -> list[str] | None:
        """Check optional model discovery without spending an inference request."""
        try:
            async with self._session.get(
                models_url(self.base_url),
                headers=self._headers,
                timeout=self._timeout,
            ) as response:
                body = await response.text()
                if response.status in {404, 405}:
                    return None
                self._raise_for_status(response, body)
                try:
                    data = await response.json(content_type=None)
                except (TypeError, ValueError) as err:
                    raise JevResponseError(
                        "the models endpoint returned invalid JSON"
                    ) from err
        except JevError:
            raise
        except TimeoutError as err:
            raise JevTimeoutError(
                f"no answer from {self.base_url} within {self._timeout.total}s"
            ) from err
        except aiohttp.ClientConnectorCertificateError as err:
            raise JevSSLError(
                f"TLS validation failed for {self.base_url}: {err}"
            ) from err
        except aiohttp.ClientSSLError as err:
            raise JevSSLError(f"TLS failed for {self.base_url}: {err}") from err
        except aiohttp.ClientError as err:
            raise JevConnectionError(f"request to {self.base_url} failed: {err}") from err

        raw_models = (
            data.get("data", data.get("models")) if isinstance(data, Mapping) else None
        )
        if not isinstance(raw_models, list):
            raise JevResponseError("the models endpoint returned no model list")
        models = []
        for item in raw_models:
            if isinstance(item, str):
                models.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("id"), str):
                models.append(item["id"])
            elif isinstance(item, Mapping) and isinstance(item.get("name"), str):
                models.append(item["name"])
            else:
                raise JevResponseError("the models endpoint returned an invalid model")
        return models

    async def system_one(
        self,
        state: Any,
        questions: Mapping[str, Question],
        *,
        model: str | None = None,
    ) -> JevResponse:
        """Evaluate all questions in one request against a shared state."""
        if not questions:
            raise ValueError("system_one() needs at least one question")

        payload = {
            "state": state,
            "model": model or self.model,
            "questions": {
                key: question.as_payload() for key, question in questions.items()
            },
        }
        started = time.monotonic()
        try:
            async with self._session.post(
                system_one_url(self.base_url),
                json=payload,
                headers=self._headers,
                timeout=self._timeout,
            ) as response:
                body = await response.text()
                self._raise_for_status(response, body)
                try:
                    data = await response.json(content_type=None)
                except (TypeError, ValueError) as err:
                    raise JevResponseError("the API returned invalid JSON") from err
        except JevError:
            raise
        except TimeoutError as err:
            raise JevTimeoutError(
                f"no answer from {self.base_url} within {self._timeout.total}s"
            ) from err
        except aiohttp.ClientConnectorCertificateError as err:
            raise JevSSLError(
                f"TLS validation failed for {self.base_url}: {err}"
            ) from err
        except aiohttp.ClientSSLError as err:
            raise JevSSLError(f"TLS failed for {self.base_url}: {err}") from err
        except aiohttp.ClientError as err:
            raise JevConnectionError(f"request to {self.base_url} failed: {err}") from err

        return self._parse(data, (time.monotonic() - started) * 1000)

    async def ask(
        self,
        state: Any,
        questions: Mapping[str, Question],
        *,
        model: str | None = None,
    ) -> JevResponse:
        """Compatibility alias retained for the integration's existing callers."""
        return await self.system_one(state, questions, model=model)

    @staticmethod
    def _raise_for_status(response: aiohttp.ClientResponse, body: str) -> None:
        status = response.status
        if status < 400:
            return
        detail = body.strip()[:400]
        if status in {401, 403}:
            raise JevAuthError(f"authentication was rejected: {detail}")
        if status == 422:
            raise JevValidationError(f"the request was rejected as invalid: {detail}")
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            raise JevRateLimitError(
                f"rate limited: {detail}",
                retry_after=float(retry_after) if retry_after else None,
            )
        if status == 529:
            raise JevOverloadedError(f"the service is saturated: {detail}")
        raise JevResponseError(f"HTTP {status}: {detail}")

    @staticmethod
    def _parse(data: Any, latency_ms: float) -> JevResponse:
        """Parse typed answers, tolerating APIs that omit the redundant type tag."""
        if not isinstance(data, Mapping):
            raise JevResponseError(f"expected an object, got {type(data).__name__}")
        raw_answers = data.get("answers")
        if not isinstance(raw_answers, Mapping):
            raise JevResponseError("the reply carries no answers object")
        usage = data.get("usage") or {}
        if not isinstance(usage, Mapping):
            raise JevResponseError("the reply carries an invalid usage object")
        try:
            parsed_answers = {
                str(key): SystemOneClient._parse_answer(str(key), raw)
                for key, raw in raw_answers.items()
            }
            parsed_usage = Usage(
                input_tokens=int(usage.get("input_tokens", 0)),
                output_tokens=int(usage.get("output_tokens", 0)),
            )
        except (TypeError, ValueError) as err:
            raise JevResponseError(f"the reply is not readable: {err}") from err
        return JevResponse(
            model=str(data.get("model", "")),
            answers=parsed_answers,
            usage=parsed_usage,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _parse_answer(key: str, raw: Any) -> Answer:
        if not isinstance(raw, Mapping):
            raise JevResponseError(f"answer {key!r} is not an object")
        kind = raw.get("type")
        if kind is None:
            for candidate in ("noul", "choice", "score"):
                if candidate in raw:
                    kind = candidate
                    break
        try:
            if kind == "noul":
                value = float(raw["noul"])
                if not 0 <= value <= 1:
                    raise ValueError("noul must be between 0 and 1")
                return NoulAnswer(noul=value)
            if kind == "choice":
                probabilities = raw.get("probabilities", {})
                return ChoiceAnswer(
                    choice=str(raw["choice"]),
                    probabilities={str(k): float(v) for k, v in probabilities.items()},
                    confidence=float(raw.get("confidence", 0)),
                )
            if kind == "score":
                probabilities = raw.get("probabilities", {})
                return ScoreAnswer(
                    score=float(raw["score"]),
                    legend={str(k): str(v) for k, v in raw.get("legend", {}).items()},
                    probabilities={str(k): float(v) for k, v in probabilities.items()},
                    confidence=float(raw.get("confidence", 0)),
                )
        except (KeyError, TypeError, ValueError) as err:
            raise JevResponseError(f"answer {key!r} is not readable: {err}") from err
        raise JevResponseError(f"answer {key!r} has unknown type {kind!r}")


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT",
    "MAX_CHOICE_OPTIONS",
    "MAX_SCORE_LEVELS",
    "MIN_CHOICE_OPTIONS",
    "MIN_SCORE_LEVELS",
    "USD_PER_MILLION_INPUT_TOKENS",
    "Answer",
    "Choice",
    "ChoiceAnswer",
    "JevAuthError",
    "JevConnectionError",
    "JevError",
    "JevOverloadedError",
    "JevRateLimitError",
    "JevResponse",
    "JevResponseError",
    "JevSSLError",
    "JevTimeoutError",
    "JevValidationError",
    "Noul",
    "NoulAnswer",
    "Question",
    "Score",
    "ScoreAnswer",
    "SystemOneClient",
    "Usage",
    "models_url",
    "normalize_base_url",
    "system_one_url",
]
