from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from conftest import FakeVerifier, ScriptedLLM, final_reply, tool_reply
from patchpilot import web
from patchpilot.config import PROVIDERS, Settings
from patchpilot.errors import ConfigError
from patchpilot.ui import CONFIRM_FIX_PROMPT
from patchpilot.verifier import CppVerifier, VerificationResult

BUGGY = "#include <iostream>\nint main() { std::cout << 1 + 2; }\n"
requires_gpp = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ is not installed")


def settings() -> Settings:
    return web.web_settings("groq", "", "key")


class TestValidation:
    def test_drops_directory_parts_from_names(self) -> None:
        assert list(web.validate_files({"../../etc/evil.cpp": "x"})) == ["evil.cpp"]
        assert list(web.validate_files({"..\\..\\evil.cpp": "x"})) == ["evil.cpp"]

    @pytest.mark.parametrize("name", ["run.sh", "secrets.env", "main", "main.cpp.exe"])
    def test_rejects_anything_but_code(self, name: str) -> None:
        with pytest.raises(ConfigError, match="not a C or C"):
            web.validate_files({name: "x"})

    def test_requires_at_least_one_file(self) -> None:
        with pytest.raises(ConfigError, match="at least one"):
            web.validate_files({})

    def test_limits_the_number_of_files(self) -> None:
        with pytest.raises(ConfigError, match="at most"):
            web.validate_files({f"{i}.cpp": "x" for i in range(web.MAX_FILES + 1)})

    def test_limits_the_total_size(self) -> None:
        with pytest.raises(ConfigError, match="KB"):
            web.validate_files({"a.cpp": "x" * (web.MAX_TOTAL_BYTES + 1)})


class TestSettings:
    def test_are_limited_and_auto_approved(self) -> None:
        chosen = web.web_settings("gemini", "  ", " key ")

        assert chosen.api_key == "key"
        assert chosen.model == PROVIDERS["gemini"].default_model
        assert chosen.base_url == PROVIDERS["gemini"].base_url
        assert chosen.auto_approve
        assert chosen.max_attempts == web.WEB_MAX_ATTEMPTS

    def test_ollama_is_not_offered(self) -> None:
        with pytest.raises(ConfigError, match="not available"):
            web.web_settings("ollama", "", "key")

    def test_a_key_is_required(self) -> None:
        with pytest.raises(ConfigError, match="API key"):
            web.web_settings("groq", "", "   ")


class TestRunFix:
    def test_without_a_sandbox_nothing_runs_and_the_result_says_so(self) -> None:
        llm = ScriptedLLM(
            [
                tool_reply(
                    "replace_line",
                    path="main.cpp",
                    line_number=2,
                    new_line="int main() { std::cout << 1 + 1; }",
                ),
                final_reply("Changed the sum."),
            ]
        )

        outcome = web.run_fix(
            {"main.cpp": BUGGY},
            "wrong sum",
            settings(),
            client=llm,
            verifier_factory=lambda _: web.NotExecutedVerifier(),
        )

        assert outcome.solved
        assert not outcome.executed
        assert "+int main() { std::cout << 1 + 1; }" in outcome.diff
        assert "Not compiled" in outcome.log

    def test_the_default_verifier_never_falls_back_to_running_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(web, "sandbox_available", lambda: False)

        assert isinstance(web.default_verifier(tmp_path), web.NotExecutedVerifier)

    def test_expected_output_is_ignored_when_nothing_ran(self) -> None:
        llm = ScriptedLLM([final_reply()])

        outcome = web.run_fix(
            {"main.cpp": BUGGY},
            "bug",
            settings(),
            expected_output="3",
            client=llm,
            verifier_factory=lambda _: web.NotExecutedVerifier(),
        )

        assert outcome.solved

    @requires_gpp
    def test_expected_output_drives_the_retry_loop(self) -> None:
        llm = ScriptedLLM(
            [
                final_reply("Nothing to do."),
                tool_reply(
                    "replace_line",
                    path="main.cpp",
                    line_number=2,
                    new_line="int main() { std::cout << 2 + 2; }",
                ),
                final_reply("Now it prints 4."),
            ]
        )

        outcome = web.run_fix(
            {"main.cpp": BUGGY},
            "it should print 4",
            settings(),
            expected_output="4",
            client=llm,
            verifier_factory=lambda _: CppVerifier(),
        )

        assert outcome.solved
        assert "2 + 2" in outcome.diff
        assert "The tests still fail" not in outcome.log
        assert "Tests: FAILED" in outcome.log

    def test_a_failed_run_has_no_diff(self) -> None:
        wrong = VerificationResult(True, "wrong", tests_passed=False)
        llm = ScriptedLLM([final_reply(), final_reply()])

        outcome = web.run_fix(
            {"main.cpp": BUGGY},
            "bug",
            settings(),
            client=llm,
            verifier_factory=lambda _: FakeVerifier([wrong], automated=True),
        )

        assert not outcome.solved
        assert outcome.diff == ""

    def test_streams_each_line_to_the_page(self) -> None:
        seen: list[str] = []

        web.run_fix(
            {"main.cpp": BUGGY},
            "bug",
            settings(),
            client=ScriptedLLM([final_reply()]),
            verifier_factory=lambda _: web.NotExecutedVerifier(),
            on_line=seen.append,
        )

        assert any("ATTEMPT 1" in line for line in seen)


def test_web_ui_answers_only_the_final_question() -> None:
    ui = web.WebUI()

    assert ui.ask(CONFIRM_FIX_PROMPT) == "y"
    assert ui.ask("What is still wrong? (Enter to skip): ") == ""
