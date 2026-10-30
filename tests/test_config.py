from __future__ import annotations

import pytest

from patchpilot.config import DEFAULT_MODEL, Settings
from patchpilot.errors import ConfigError


def test_defaults_match_the_documented_values() -> None:
    settings = Settings.from_env({"GROQ_API_KEY": "key"})

    assert settings.api_key == "key"
    assert settings.model == DEFAULT_MODEL
    assert settings.max_attempts == 3
    assert settings.max_steps == 10
    assert settings.run_timeout == 10


def test_environment_overrides_are_applied() -> None:
    settings = Settings.from_env(
        {
            "GROQ_API_KEY": "key",
            "PATCHPILOT_MODEL": "some/other-model",
            "PATCHPILOT_MAX_ATTEMPTS": "5",
            "PATCHPILOT_MAX_STEPS": "20",
            "PATCHPILOT_RUN_TIMEOUT": "30",
        }
    )

    assert settings.model == "some/other-model"
    assert settings.max_attempts == 5
    assert settings.max_steps == 20
    assert settings.run_timeout == 30


def test_blank_overrides_fall_back_to_defaults() -> None:
    settings = Settings.from_env(
        {"GROQ_API_KEY": "key", "PATCHPILOT_MODEL": "  ", "PATCHPILOT_MAX_STEPS": ""}
    )

    assert settings.model == DEFAULT_MODEL
    assert settings.max_steps == 10


@pytest.mark.parametrize("env", [{}, {"GROQ_API_KEY": ""}, {"GROQ_API_KEY": "   "}])
def test_missing_api_key_is_rejected_with_a_helpful_message(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        Settings.from_env(env)


@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-2"])
def test_invalid_numbers_are_rejected(value: str) -> None:
    with pytest.raises(ConfigError, match="PATCHPILOT_MAX_ATTEMPTS"):
        Settings.from_env({"GROQ_API_KEY": "key", "PATCHPILOT_MAX_ATTEMPTS": value})


def test_api_key_never_appears_in_repr() -> None:
    settings = Settings.from_env({"GROQ_API_KEY": "super-secret-value"})

    assert "super-secret-value" not in repr(settings)
