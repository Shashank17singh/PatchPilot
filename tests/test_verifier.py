from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from patchpilot.verifier import CppVerifier

requires_gpp = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ is not installed")


def write(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.write_text(source)
    return path


@requires_gpp
def test_compiles_and_returns_program_output(tmp_path: Path) -> None:
    source = write(
        tmp_path, "ok.cpp", '#include <iostream>\nint main() { std::cout << "hello"; }\n'
    )

    result = CppVerifier().verify([source])

    assert result.compiled
    assert not result.skipped
    assert result.output == "hello"


@requires_gpp
def test_reports_compiler_errors(tmp_path: Path) -> None:
    source = write(tmp_path, "bad.cpp", "int main() { return undefined_symbol; }\n")

    result = CppVerifier().verify([source])

    assert not result.compiled
    assert "undefined_symbol" in result.output


@requires_gpp
def test_captures_stderr_and_nonzero_exit_code(tmp_path: Path) -> None:
    source = write(
        tmp_path,
        "fail.cpp",
        '#include <iostream>\nint main() { std::cerr << "boom"; return 3; }\n',
    )

    result = CppVerifier().verify([source])

    assert result.compiled
    assert "boom" in result.output
    assert "[process exited with code 3]" in result.output


@requires_gpp
def test_kills_programs_that_run_too_long(tmp_path: Path) -> None:
    source = write(tmp_path, "loop.cpp", "int main() { while (true) {} }\n")

    result = CppVerifier(run_timeout=1).verify([source])

    assert result.compiled
    assert "did not finish in 1 seconds" in result.output


@requires_gpp
def test_programs_reading_stdin_do_not_hang(tmp_path: Path) -> None:
    source = write(
        tmp_path,
        "reader.cpp",
        '#include <iostream>\nint main() { int x = 7; std::cin >> x; std::cout << "done"; }\n',
    )
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from patchpilot.verifier import CppVerifier\n"
        "print(CppVerifier(run_timeout=3).verify([Path(sys.argv[1])]).output)\n"
    )

    # Simulate an interactive terminal: stdin is an open pipe that never delivers EOF.
    # Under pytest stdin is already closed, which would hide a missing stdin=DEVNULL.
    read_end, write_end = os.pipe()
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(source)],
            stdin=read_end,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        os.close(read_end)
        os.close(write_end)

    assert completed.stdout.strip() == "done", completed.stderr


@requires_gpp
def test_long_output_is_truncated(tmp_path: Path) -> None:
    source = write(
        tmp_path,
        "spam.cpp",
        "#include <iostream>\n"
        'int main() { for (int i = 0; i < 5000; i++) std::cout << "xxxxxxxxxx\\n"; }\n',
    )

    result = CppVerifier(output_limit=100).verify([source])

    assert result.output.endswith("[output truncated]")
    assert len(result.output) < 200


@requires_gpp
def test_reports_missing_sources() -> None:
    result = CppVerifier().verify([])

    assert not result.compiled
    assert "No C++ source files" in result.output


@requires_gpp
def test_leaves_no_files_behind(tmp_path: Path) -> None:
    source = write(tmp_path, "ok.cpp", "int main() {}\n")

    CppVerifier().verify([source])

    assert sorted(p.name for p in tmp_path.iterdir()) == ["ok.cpp"]


def test_skips_gracefully_when_the_compiler_is_missing(tmp_path: Path) -> None:
    source = write(tmp_path, "ok.cpp", "int main() {}\n")

    result = CppVerifier(compiler="definitely-not-a-real-compiler").verify([source])

    assert result.compiled
    assert result.skipped
    assert "definitely-not-a-real-compiler not found" in result.output
