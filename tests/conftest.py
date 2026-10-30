"""Shared test doubles and fixtures."""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from patchpilot.config import Settings
from patchpilot.llm import LLMReply, Message, ToolCall, ToolSchema
from patchpilot.verifier import VerificationResult
from patchpilot.workspace import Workspace

DEMO_DIR = Path(__file__).resolve().parent.parent / "examples" / "cpp_demo"


def tool_reply(name: str, call_id: str = "call_1", **arguments: object) -> LLMReply:
    """A model turn that requests a single tool call."""
    call = ToolCall(id=call_id, name=name, arguments=json.dumps(arguments))
    return LLMReply(content="", tool_calls=(call,), total_tokens=10)


def final_reply(text: str = "Fixed it.") -> LLMReply:
    """A model turn that finishes without calling a tool."""
    return LLMReply(content=text, tool_calls=(), total_tokens=10)


class ScriptedLLM:
    """LLM double that replays a fixed script and records every conversation it receives.

    A script entry may be an exception instance, which is raised instead of returned.
    """

    def __init__(self, script: Sequence[LLMReply | Exception]) -> None:
        self._script = list(script)
        self.conversations: list[list[Message]] = []

    def complete(self, messages: list[Message], tools: list[ToolSchema]) -> LLMReply:
        self.conversations.append([dict(m) for m in messages])
        if not self._script:
            raise AssertionError("ScriptedLLM was called more times than scripted")
        entry = self._script.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return entry


class ScriptedUI:
    """UI double that records output and answers prompts from a script.

    A scripted answer may be an exception instance, which is raised from ``ask``.
    """

    def __init__(self, answers: Sequence[str | BaseException] = ()) -> None:
        self._answers = list(answers)
        self.lines: list[str] = []
        self.questions: list[str] = []

    def header(self, title: str, char: str = "=") -> None:
        self.lines.append(title)

    def say(self, message: str = "") -> None:
        self.lines.append(message)

    def block(self, text: str, max_lines: int = 15) -> None:
        self.lines.append(text)

    def ask(self, question: str) -> str:
        self.questions.append(question)
        if not self._answers:
            raise AssertionError(f"Unexpected prompt: {question!r}")
        answer = self._answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    @property
    def transcript(self) -> str:
        return "\n".join(self.lines)


class FakeVerifier:
    """Verifier double returning scripted results; the last result repeats forever."""

    name = "fake-cc"

    def __init__(self, results: Sequence[VerificationResult], *, automated: bool = False) -> None:
        self._results = list(results)
        self.automated = automated
        self.calls = 0

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        self.calls += 1
        index = min(self.calls - 1, len(self._results) - 1)
        return self._results[index]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A writable copy of the demo project."""
    destination = tmp_path / "project"
    shutil.copytree(DEMO_DIR, destination)
    return destination


@pytest.fixture
def workspace(project: Path) -> Workspace:
    return Workspace(project)


@pytest.fixture
def settings() -> Settings:
    return Settings(api_key="test-key", max_attempts=3, max_steps=4, auto_approve=True)


def line_number_of(path: Path, needle: str) -> int:
    """1-based number of the first line of ``path`` containing ``needle``."""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} not found in {path}")
