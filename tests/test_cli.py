from __future__ import annotations

from pathlib import Path

import pytest

from patchpilot import __version__
from patchpilot.cli import EXIT_INTERRUPTED, main


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run every test from an empty directory with no PatchPilot environment variables."""
    monkeypatch.chdir(tmp_path)
    for name in ("GROQ_API_KEY", "PATCHPILOT_MODEL", "PATCHPILOT_MAX_ATTEMPTS"):
        # Register the variable with monkeypatch before removing it. load_dotenv() writes to
        # os.environ directly, and this guarantees teardown clears anything it adds.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


def test_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_missing_api_key_fails_fast_with_exit_code_2(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_if_prompted(_: str = "") -> str:
        raise AssertionError("must not prompt the user before validating configuration")

    monkeypatch.setattr("builtins.input", fail_if_prompted)

    code = main([str(project)])

    assert code == 2
    assert "GROQ_API_KEY" in capsys.readouterr().err


def test_invalid_numeric_setting_exits_with_code_2(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "key")
    monkeypatch.setenv("PATCHPILOT_MAX_ATTEMPTS", "many")

    assert main([str(project)]) == 2
    assert "PATCHPILOT_MAX_ATTEMPTS" in capsys.readouterr().err


def test_nonexistent_workspace_exits_with_code_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "key")

    assert main([str(tmp_path / "missing")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_workspace_without_code_files_exits_with_code_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "key")
    (tmp_path / "readme.txt").write_text("no code here")

    assert main([str(tmp_path)]) == 1
    assert "no C/C++ source files" in capsys.readouterr().err


def test_empty_bug_description_exits_cleanly(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "key")
    monkeypatch.setattr("builtins.input", lambda _="": "   ")

    assert main([str(project)]) == 0
    output = capsys.readouterr().out
    assert "main.cpp" in output
    assert "No bug given" in output


def test_ctrl_c_exits_with_the_conventional_code(
    project: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "key")

    def interrupt(_: str = "") -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)

    assert main([str(project)]) == EXIT_INTERRUPTED
    assert "Interrupted" in capsys.readouterr().out


def test_dotenv_in_the_current_directory_is_picked_up(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("GROQ_API_KEY=from-dotenv\n")
    monkeypatch.setattr("builtins.input", lambda _="": "")

    assert main([str(project)]) == 0  # Reaching the prompt proves configuration loaded.
