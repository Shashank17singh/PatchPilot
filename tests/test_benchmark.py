from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import ScriptedLLM, final_reply, tool_reply
from patchpilot.benchmark import (
    BenchmarkCase,
    CaseResult,
    ClientFactory,
    HiddenTestVerifier,
    ModelSpec,
    format_report,
    load_cases,
    main,
    parse_model_spec,
    run_benchmark,
    run_case,
)
from patchpilot.config import Settings
from patchpilot.errors import ConfigError, LLMError
from patchpilot.llm import LLMClient, LLMReply

CASES_DIRECTORY = Path(__file__).resolve().parent.parent / "benchmarks" / "cases"
ENV = {"GROQ_API_KEY": "key"}
SPEC = ModelSpec("groq", "test-model")

requires_gpp = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ is not installed")

ALL_CASES = load_cases(CASES_DIRECTORY)


def apply_reference_fix(case: BenchmarkCase, project: Path) -> None:
    for fix in case.fixes:
        path = project / fix.file
        text = path.read_bytes().decode("utf-8")
        assert fix.find in text, f"{case.id}: {fix.find!r} not in {fix.file}"
        path.write_bytes(text.replace(fix.find, fix.replace, 1).encode("utf-8"))


def scripted(*replies: LLMReply) -> ClientFactory:
    def factory(_: Settings) -> LLMClient:
        return ScriptedLLM(replies)

    return factory


def test_the_suite_has_ten_cases_with_complete_metadata() -> None:
    assert len(ALL_CASES) == 10
    for case in ALL_CASES:
        assert case.report and case.title and case.kind and case.fixes
        assert case.hidden_test.is_file()


@requires_gpp
@pytest.mark.parametrize("case", ALL_CASES, ids=lambda case: case.id)
def test_each_bug_fails_its_hidden_test_and_the_reference_fix_passes(
    case: BenchmarkCase, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    shutil.copytree(case.project, project)
    verifier = HiddenTestVerifier(case, project)

    assert verifier.verify([]).tests_passed is not True

    apply_reference_fix(case, project)
    result = verifier.verify([])

    assert result.compiled, result.output
    assert result.tests_passed is True, result.output


@requires_gpp
def test_a_correct_fix_is_scored_as_solved() -> None:
    case = next(c for c in ALL_CASES if c.id == "off_by_one")
    source = (case.project / "main.cpp").read_text(encoding="utf-8").splitlines()
    number = next(i for i, line in enumerate(source, 1) if "i < high" in line)
    factory = scripted(
        tool_reply(
            "replace_line",
            path="main.cpp",
            line_number=number,
            new_line="for (int i = low; i <= high; i++) {",
        ),
        final_reply("Made the bound inclusive."),
    )

    result = run_case(case, SPEC, factory, env=ENV)

    assert result.solved
    assert result.attempts == 1
    assert result.llm_calls == 2
    assert result.tokens == 20
    assert result.error is None


@requires_gpp
def test_a_model_that_changes_nothing_is_scored_as_unsolved() -> None:
    case = next(c for c in ALL_CASES if c.id == "off_by_one")
    factory = scripted(final_reply(), final_reply(), final_reply())

    result = run_case(case, SPEC, factory, env=ENV)

    assert not result.solved
    assert result.attempts == 3


@requires_gpp
def test_an_api_failure_is_recorded_and_does_not_stop_the_run() -> None:
    case = next(c for c in ALL_CASES if c.id == "off_by_one")

    results = run_benchmark(
        [case],
        [SPEC],
        repeat=2,
        client_factory=lambda _: ScriptedLLM([LLMError("down")]),
        env=ENV,
    )

    assert len(results) == 2
    assert all(result.error == "down" and not result.solved for result in results)


def test_the_model_spec_splits_only_on_the_first_colon() -> None:
    assert parse_model_spec("ollama:gpt-oss:20b") == ModelSpec("ollama", "gpt-oss:20b")


def test_a_bare_provider_uses_its_default_model() -> None:
    assert parse_model_spec("gemini") == ModelSpec("gemini", "gemini-3.8-flash")


def test_an_unknown_provider_is_rejected() -> None:
    with pytest.raises(ConfigError, match="Unknown provider"):
        parse_model_spec("nope:model")


def test_the_report_summarises_each_model_and_case() -> None:
    results = [
        CaseResult("groq:a", "case_1", 1, True, 1, 4, 100, 2.0),
        CaseResult("groq:a", "case_2", 1, False, 3, 8, 300, 4.0, "down"),
    ]

    report = format_report(results)

    assert "| `groq:a` | 1/2 (50%) | 200 | 6.0 | 3.0 | 1 |" in report
    assert "| `case_1` | 1/1 |" in report
    assert "| `case_2` | 0/1 |" in report


def test_loading_an_empty_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="No benchmark cases"):
        load_cases(tmp_path)


def test_a_missing_key_fails_before_any_case_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GROQ_API_KEY", "")

    code = main(["--models", "groq", "--cases", str(CASES_DIRECTORY)])

    assert code == 2
    assert "GROQ_API_KEY" in capsys.readouterr().err


def test_case_files_are_valid_json() -> None:
    for path in CASES_DIRECTORY.glob("*/case.json"):
        assert set(json.loads(path.read_text(encoding="utf-8"))) == {
            "title",
            "kind",
            "report",
            "fix",
        }
