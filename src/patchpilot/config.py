"""Runtime configuration, loaded from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .errors import ConfigError

DEFAULT_PROVIDER = "groq"
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_MAX_STEPS = 10
DEFAULT_RUN_TIMEOUT = 10
DEFAULT_COMMAND_TIMEOUT = 300

BUILD_SYSTEMS = ("auto", "g++", "make", "cmake", "none")


@dataclass(frozen=True, slots=True)
class Provider:
    """How to reach one language model provider.

    Attributes:
        name: identifier used by ``--provider`` and ``PATCHPILOT_PROVIDER``.
        key_variable: environment variable holding the API key, ``None`` if no key is needed.
        default_model: model used when none is configured.
        base_url: OpenAI-compatible endpoint, ``None`` for providers with their own SDK.
    """

    name: str
    key_variable: str | None
    default_model: str
    base_url: str | None = None


PROVIDERS: dict[str, Provider] = {
    provider.name: provider
    for provider in (
        Provider("groq", "GROQ_API_KEY", DEFAULT_MODEL),
        Provider(
            "openrouter",
            "OPENROUTER_API_KEY",
            "openai/gpt-oss-120b",
            "https://openrouter.ai/api/v1",
        ),
        Provider(
            "gemini",
            "GEMINI_API_KEY",
            "gemini-3.8-flash",
            "https://generativelanguage.googleapis.com/v1beta/openai/",
        ),
        Provider("ollama", None, "gpt-oss:20b", "http://localhost:11434/v1"),
    )
}


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable agent settings.

    Environment variables read by :meth:`from_env`:

    - ``PATCHPILOT_PROVIDER``: ``groq`` (default), ``openrouter``, ``gemini`` or ``ollama``.
    - ``GROQ_API_KEY``, ``OPENROUTER_API_KEY``, ``GEMINI_API_KEY``: key for the chosen provider.
    - ``OLLAMA_BASE_URL``: address of the Ollama server.
    - ``PATCHPILOT_MODEL``: model identifier (default: the provider's default model).
    - ``PATCHPILOT_MAX_ATTEMPTS``: fix attempts before giving up and rolling back.
    - ``PATCHPILOT_MAX_STEPS``: tool-calling steps allowed within a single attempt.
    - ``PATCHPILOT_RUN_TIMEOUT``: seconds the compiled program may run during verification.
    - ``PATCHPILOT_COMMAND_TIMEOUT``: seconds each build or test command may run.
    """

    api_key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_steps: int = DEFAULT_MAX_STEPS
    api_max_retries: int = 10
    run_timeout: int = DEFAULT_RUN_TIMEOUT
    output_limit: int = 3000
    provider: str = DEFAULT_PROVIDER
    base_url: str | None = None
    auto_approve: bool = False
    build_system: str = "auto"
    test_command: str | None = None
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> Settings:
        """Build settings from ``env`` (defaults to :data:`os.environ`).

        ``provider`` and ``model`` take precedence over the environment, which lets
        command-line flags decide which API key is required.

        Raises:
            ConfigError: if the provider is unknown, its API key is missing, or a numeric
                value is invalid.
        """
        source = os.environ if env is None else env

        name = provider or source.get("PATCHPILOT_PROVIDER", "").strip() or DEFAULT_PROVIDER
        chosen = get_provider(name)

        api_key = "ollama"  # Ollama requires a value but ignores it.
        if chosen.key_variable is not None:
            api_key = source.get(chosen.key_variable, "").strip()
            if not api_key:
                raise ConfigError(
                    f"{chosen.key_variable} is not set. Copy .env.example to .env and add "
                    "your key, or export it in your shell."
                )

        base_url = chosen.base_url
        if chosen.name == "ollama":
            base_url = source.get("OLLAMA_BASE_URL", "").strip() or chosen.base_url

        return cls(
            api_key=api_key,
            model=model or source.get("PATCHPILOT_MODEL", "").strip() or chosen.default_model,
            max_attempts=_positive_int(source, "PATCHPILOT_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS),
            max_steps=_positive_int(source, "PATCHPILOT_MAX_STEPS", DEFAULT_MAX_STEPS),
            run_timeout=_positive_int(source, "PATCHPILOT_RUN_TIMEOUT", DEFAULT_RUN_TIMEOUT),
            command_timeout=_positive_int(
                source, "PATCHPILOT_COMMAND_TIMEOUT", DEFAULT_COMMAND_TIMEOUT
            ),
            provider=chosen.name,
            base_url=base_url,
        )


def get_provider(name: str) -> Provider:
    """Look up a provider by name.

    Raises:
        ConfigError: if ``name`` is not a known provider.
    """
    try:
        return PROVIDERS[name.lower()]
    except KeyError:
        raise ConfigError(
            f"Unknown provider {name!r}. Choose one of: {', '.join(PROVIDERS)}."
        ) from None


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Read ``name`` from ``env`` as a positive integer, falling back to ``default``."""
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from None
    if value < 1:
        raise ConfigError(f"{name} must be at least 1, got {value}.")
    return value
