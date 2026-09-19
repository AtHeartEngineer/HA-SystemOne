"""System One transport behavior at the real HTTP boundary."""

import aiohttp
import pytest
from aiohttp import web

from custom_components.jev.api import (
    Choice,
    JevAuthError,
    JevResponseError,
    Noul,
    SystemOneClient,
    models_url,
    normalize_base_url,
    system_one_url,
)


@pytest.mark.parametrize(
    ("raw", "base", "endpoint"),
    [
        (
            "https://api.typesafe.ai",
            "https://api.typesafe.ai",
            "https://api.typesafe.ai/v1/systemone",
        ),
        (
            "https://api.typesafe.ai/",
            "https://api.typesafe.ai",
            "https://api.typesafe.ai/v1/systemone",
        ),
        (
            "https://api.typesafe.ai/v1",
            "https://api.typesafe.ai",
            "https://api.typesafe.ai/v1/systemone",
        ),
        (
            "https://api.typesafe.ai/v1/systemone",
            "https://api.typesafe.ai",
            "https://api.typesafe.ai/v1/systemone",
        ),
        (
            "http://server:8000/v1/",
            "http://server:8000",
            "http://server:8000/v1/systemone",
        ),
    ],
)
def test_url_normalization(raw, base, endpoint):
    """Suffix handling must not duplicate `/v1/systemone`."""
    assert normalize_base_url(raw) == base
    assert system_one_url(raw) == endpoint
    assert models_url(raw) == f"{base}/v1/models"


@pytest.mark.parametrize("raw", ["", "server:8000", "ftp://server", "http:///missing"])
def test_invalid_base_url_is_rejected(raw):
    with pytest.raises(ValueError, match="HTTP URL"):
        normalize_base_url(raw)


@pytest.mark.parametrize(
    ("token", "expected_header"),
    [("secret", "Bearer secret"), ("", None), ("   ", None), (None, None)],
)
async def test_auth_header_and_payload(
    aiohttp_server, socket_enabled, token, expected_header
):
    """A blank token must omit Authorization instead of sending a bare bearer."""
    captured = {}

    async def handle(request):
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = await request.json()
        return web.json_response(
            {
                "model": "qwen-self-hosted",
                "answers": {
                    "occupied": {"noul": 0.75},
                    "activity": {
                        "choice": "working",
                        "probabilities": {"empty": 0.1, "working": 0.9},
                        "confidence": 0.8,
                    },
                },
                "usage": {"input_tokens": 12, "output_tokens": 1},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/systemone", handle)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SystemOneClient(
            session=session,
            base_url=str(server.make_url("/")),
            token=token,
            model="qwen-flash",
        )
        response = await client.system_one(
            state="Office presence: on",
            questions={
                "occupied": Noul("Is occupied?"),
                "activity": Choice("What activity?", {"empty": None, "working": None}),
            },
        )

    assert captured["authorization"] == expected_header
    assert captured["payload"] == {
        "state": "Office presence: on",
        "model": "qwen-flash",
        "questions": {
            "occupied": {"type": "noul", "instructions": "Is occupied?"},
            "activity": {
                "type": "choice",
                "instructions": "What activity?",
                "criteria": {"empty": None, "working": None},
            },
        },
    }
    assert response.answers["occupied"].noul == 0.75
    assert response.answers["activity"].choice == "working"


async def test_auth_and_malformed_response_errors(aiohttp_server, socket_enabled):
    response_mode = "auth"

    async def handle(_request):
        if response_mode == "auth":
            return web.json_response({"detail": "no"}, status=401)
        return web.json_response({"model": "jev-latest"})

    app = web.Application()
    app.router.add_post("/v1/systemone", handle)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SystemOneClient(session=session, base_url=str(server.make_url("/")))
        with pytest.raises(JevAuthError):
            await client.system_one("state", {"answer": Noul("True?")})
        response_mode = "malformed"
        with pytest.raises(JevResponseError, match="answers"):
            await client.system_one("state", {"answer": Noul("True?")})
