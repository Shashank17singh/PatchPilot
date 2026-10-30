"""Language model access.

The agent depends on the small :class:`LLMClient` protocol rather than on any one SDK,
so the provider can be swapped (or faked in tests) without touching the agent logic.
Groq uses its own SDK. OpenRouter, Gemini and Ollama all expose OpenAI-compatible
endpoints, so one client covers the three of them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import groq
import openai

from .config import Settings, get_provider
from .errors import LLMError, RejectedRequestError

logger = logging.getLogger(__name__)

Message = dict[str, Any]
ToolSchema = dict[str, Any]

# A local server is either up or it is not, so waiting through many retries only hides that.
_OLLAMA_MAX_RETRIES = 1


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: str  # Raw JSON exactly as produced by the model.
    # Opaque provider data that must be sent back unchanged. Gemini keeps its thought
    # signature here and answers 400 if a later request omits it.
    extra_content: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LLMReply:
    """One completed model turn."""

    content: str
    tool_calls: tuple[ToolCall, ...]
    total_tokens: int
    # Extra assistant-message fields to replay with the history, such as OpenRouter's
    # reasoning_details.
    provider_fields: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    """Minimal chat-completion interface required by the agent."""

    def complete(self, messages: list[Message], tools: list[ToolSchema]) -> LLMReply:
        """Return the model's next turn for the given conversation.

        Raises:
            RejectedRequestError: the provider rejected the request (recoverable).
            LLMError: any other unrecoverable API failure.
        """


class GroqClient:
    """:class:`LLMClient` implementation backed by the Groq chat completions API."""

    def __init__(self, api_key: str, model: str, max_retries: int = 10) -> None:
        # The SDK waits and retries on rate limits, which are tight on the free tier.
        self._client = groq.Groq(api_key=api_key, max_retries=max_retries)
        self._model = model

    def complete(self, messages: list[Message], tools: list[ToolSchema]) -> LLMReply:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
            )
        except groq.BadRequestError as exc:
            # gpt-oss occasionally produces an invalid tool call, which Groq rejects with a 400.
            raise RejectedRequestError(str(exc)) from exc
        except groq.AuthenticationError as exc:
            raise LLMError("Groq rejected the API key (HTTP 401). Check GROQ_API_KEY.") from exc
        except groq.APIError as exc:
            raise LLMError(f"Groq API request failed: {exc}") from exc

        return _parse_reply(response, "Groq")


class OpenAICompatibleClient:
    """:class:`LLMClient` for any provider that implements the OpenAI chat completions API."""

    def __init__(
        self, *, api_key: str, base_url: str, model: str, label: str, max_retries: int = 10
    ) -> None:
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=max_retries)
        self._model = model
        self._label = label

    def complete(self, messages: list[Message], tools: list[ToolSchema]) -> LLMReply:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                tools=tools,  # type: ignore[arg-type]
            )
        except openai.BadRequestError as exc:
            raise RejectedRequestError(str(exc)) from exc
        except openai.AuthenticationError as exc:
            raise LLMError(f"{self._label} rejected the API key (HTTP 401).") from exc
        except openai.APIConnectionError as exc:
            raise LLMError(f"Could not reach {self._label}: {exc}") from exc
        except openai.APIError as exc:
            raise LLMError(f"{self._label} API request failed: {exc}") from exc

        return _parse_reply(response, self._label)


def create_client(settings: Settings) -> LLMClient:
    """Build the client for the provider named in ``settings``."""
    provider = get_provider(settings.provider)
    if provider.base_url is None:
        return GroqClient(settings.api_key, settings.model, settings.api_max_retries)

    is_local = provider.name == "ollama"
    return OpenAICompatibleClient(
        api_key=settings.api_key,
        base_url=settings.base_url or provider.base_url,
        model=settings.model,
        label=provider.name.capitalize(),
        max_retries=_OLLAMA_MAX_RETRIES if is_local else settings.api_max_retries,
    )


def _parse_reply(response: Any, label: str) -> LLMReply:
    """Convert an SDK response into an :class:`LLMReply`."""
    # Routers can answer 200 with no choices when the upstream model failed.
    if not response.choices:
        raise LLMError(f"{label} returned an empty response.")

    message = response.choices[0].message
    usage = response.usage
    extra_fields = message.model_extra or {}
    reasoning = extra_fields.get("reasoning_details")

    reply = LLMReply(
        content=message.content or "",
        tool_calls=tuple(
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments,
                extra_content=(call.model_extra or {}).get("extra_content"),
            )
            for call in message.tool_calls or ()
        ),
        total_tokens=usage.total_tokens if usage else 0,
        provider_fields={"reasoning_details": reasoning} if reasoning else {},
    )
    logger.debug(
        "LLM reply: %d tool call(s), %d total tokens", len(reply.tool_calls), reply.total_tokens
    )
    return reply
