"""Headless tests of the Streamlit page."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from conftest import ScriptedLLM, final_reply, line_number_of, tool_reply
from patchpilot import web
from patchpilot.llm import LLMClient

REPOSITORY = Path(__file__).resolve().parent.parent
APP = str(REPOSITORY / "webapp" / "app.py")
DEMO = REPOSITORY / "examples" / "cpp_demo" / "main.cpp"


@pytest.fixture(autouse=True)
def no_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "sandbox_available", lambda: False)
    monkeypatch.setattr("patchpilot.sandbox.sandbox_available", lambda: False)


def open_app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    return at


def use_model(monkeypatch: pytest.MonkeyPatch, llm: LLMClient) -> None:
    monkeypatch.setattr(web, "create_client", lambda _: llm)


def demo_fix() -> ScriptedLLM:
    number = line_number_of(DEMO, "return a + b;")
    return ScriptedLLM(
        [
            tool_reply(
                "replace_line", path="main.cpp", line_number=number, new_line="return a + b;"
            ),
            final_reply("Restored the addition."),
        ]
    )


def test_opens_with_the_demo_and_warns_about_the_missing_sandbox() -> None:
    at = open_app()

    assert at.title[0].value == "PatchPilot"
    assert "return a - b;" in at.text_area[0].value
    assert "never compiled or run" in at.sidebar.warning[0].value


def test_a_missing_key_shows_an_error_and_does_not_use_up_a_run() -> None:
    at = open_app()

    at.button[0].click().run()

    assert "Enter an API key" in at.error[0].value
    assert at.session_state["runs"] == 0


def test_a_run_shows_the_diff_and_says_it_was_not_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_model(monkeypatch, demo_fix())
    at = open_app()
    at.sidebar.text_input[1].input("key")

    at.button[0].click().run()

    assert not at.exception
    assert any("was not compiled or run" in warning.value for warning in at.warning)
    assert any("+    return a + b;" in block.value for block in at.code)


def test_the_run_limit_disables_the_button() -> None:
    at = open_app()
    at.session_state["runs"] = web.MAX_RUNS_PER_SESSION

    at.run()

    assert at.button[0].disabled
    assert "runs per session" in at.info[0].value


def test_uploaded_or_pasted_code_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    use_model(monkeypatch, ScriptedLLM([]))
    at = open_app()
    at.sidebar.text_input[1].input("key")
    at.radio[0].set_value("Paste code").run()

    at.button[0].click().run()

    assert "at least one" in at.error[0].value
