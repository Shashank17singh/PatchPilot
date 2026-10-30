"""Exercises the real SDKs against a stubbed HTTP transport, so no network is needed."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import openai
import pytest

from patchpilot.config import PROVIDERS, Settings
from patchpilot.errors import LLMError, RejectedRequestError
from patchpilot.llm import GroqClient, OpenAICompatibleClient, create_client

MESSAGES: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
Handler = Callable[[httpx.Request], httpx.Response]


def completion(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "completion-1",
        "object": "chat.completion",
        "created": 0,
        "model": "test",
        "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def client_with(handler: Handler) -> OpenAICompatibleClient:
    client = OpenAICompatibleClient(
        api_key="key", base_url="https://example.test/v1", model="test", label="Test", max_retries=0
    )
    client._client = openai.OpenAI(
        api_key="key",
        base_url="https://example.test/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return client


def respond(status: int, body: dict[str, Any]) -> Handler:
    return lambda _: httpx.Response(status, json=body)


def test_parses_text_and_usage() -> None:
    client = client_with(respond(200, completion({"role": "assistant", "content": "done"})))

    reply = client.complete(MESSAGES, [])

    assert reply.content == "done"
    assert reply.tool_calls == ()
    assert reply.total_tokens == 3


def test_parses_tool_calls_with_the_gemini_thought_signature() -> None:
    signature = {"google": {"thought_signature": "opaque=="}}
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "search_files", "arguments": '{"keyword": "x"}'},
                "extra_content": signature,
            }
        ],
    }
    client = client_with(respond(200, completion(message)))

    call = client.complete(MESSAGES, []).tool_calls[0]

    assert call.name == "search_files"
    assert call.extra_content == signature


def test_the_signature_reaches_the_server_on_the_next_request() -> None:
    signature = {"google": {"thought_signature": "opaque=="}}
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion({"role": "assistant", "content": "ok"}))

    history = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search_files", "arguments": "{}"},
                    "extra_content": signature,
                }
            ],
        }
    ]

    client_with(handler).complete(history, [])

    assert bodies[0]["messages"][0]["tool_calls"][0]["extra_content"] == signature


def test_keeps_openrouter_reasoning_details() -> None:
    details = [{"type": "reasoning.encrypted", "data": "abc"}]
    message = {"role": "assistant", "content": "ok", "reasoning_details": details}

    reply = client_with(respond(200, completion(message))).complete(MESSAGES, [])

    assert reply.provider_fields == {"reasoning_details": details}


def test_a_400_is_recoverable() -> None:
    client = client_with(respond(400, {"error": {"message": "bad tool call"}}))

    with pytest.raises(RejectedRequestError):
        client.complete(MESSAGES, [])


def test_a_401_explains_itself() -> None:
    client = client_with(respond(401, {"error": {"message": "nope"}}))

    with pytest.raises(LLMError, match="Test rejected the API key"):
        client.complete(MESSAGES, [])


def test_a_server_error_is_fatal_but_not_recoverable() -> None:
    client = client_with(respond(500, {"error": {"message": "broken"}}))

    with pytest.raises(LLMError, match="Test API request failed") as info:
        client.complete(MESSAGES, [])
    assert not isinstance(info.value, RejectedRequestError)


def test_an_unreachable_server_is_reported() -> None:
    def refuse(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LLMError, match="Could not reach Test"):
        client_with(refuse).complete(MESSAGES, [])


def test_a_200_without_choices_is_an_error() -> None:
    empty = completion({"role": "assistant", "content": ""})
    empty["choices"] = []

    with pytest.raises(LLMError, match="empty response"):
        client_with(respond(200, empty)).complete(MESSAGES, [])


def test_groq_uses_its_own_client() -> None:
    client = create_client(Settings(api_key="key"))

    assert isinstance(client, GroqClient)


@pytest.mark.parametrize("name", ["openrouter", "gemini", "ollama"])
def test_other_providers_use_their_documented_endpoints(name: str) -> None:
    provider = PROVIDERS[name]
    settings = Settings(api_key="key", provider=name, base_url=provider.base_url, model="m")

    client = create_client(settings)

    assert isinstance(client, OpenAICompatibleClient)
    assert str(client._client.base_url).rstrip("/") == str(provider.base_url).rstrip("/")


def test_ollama_does_not_wait_through_many_retries() -> None:
    client = create_client(Settings(api_key="k", provider="ollama"))

    assert isinstance(client, OpenAICompatibleClient)
    assert client._client.max_retries == 1
