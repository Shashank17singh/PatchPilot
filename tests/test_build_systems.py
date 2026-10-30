from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from conftest import FakeVerifier
from patchpilot.config import Settings
from patchpilot.errors import ConfigError
from patchpilot.verifier import (
    CMAKE_BUILD_DIRECTORY,
    CMakeVerifier,
    CppVerifier,
    MakeVerifier,
    TestCommandVerifier,
    VerificationResult,
    build_verifier,
    detect_build_system,
    run_command,
)

requires_gpp = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ is not installed")
requires_make = pytest.mark.skipif(
    shutil.which("make") is None and shutil.which("mingw32-make") is None,
    reason="make is not installed",
)
requires_cmake = pytest.mark.skipif(shutil.which("cmake") is None, reason="cmake is not installed")

PYTHON = f'"{sys.executable}"'
PROGRAM = """int main() { return 0; }\n"""


def settings(**overrides: Any) -> Settings:
    return Settings(api_key="k", **overrides)


class TestDetection:
    def test_prefers_cmake_when_both_exist(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("")
        (tmp_path / "Makefile").write_text("")

        assert detect_build_system(tmp_path) == "cmake"

    def test_finds_a_makefile(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("")

        assert detect_build_system(tmp_path) == "make"

    def test_defaults_to_the_compiler(self, tmp_path: Path) -> None:
        assert detect_build_system(tmp_path) == "g++"


class TestChoosingAVerifier:
    def test_a_plain_project_uses_the_compiler(self, tmp_path: Path) -> None:
        verifier = build_verifier(tmp_path, settings())

        assert isinstance(verifier, CppVerifier)
        assert not verifier.automated

    def test_the_build_flag_overrides_detection(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("")

        assert isinstance(build_verifier(tmp_path, settings(build_system="g++")), CppVerifier)

    def test_a_makefile_is_detected(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:\n")

        assert isinstance(build_verifier(tmp_path, settings()), MakeVerifier)

    def test_a_test_command_is_automated(self, tmp_path: Path) -> None:
        verifier = build_verifier(tmp_path, settings(test_command="run-tests"))

        assert isinstance(verifier, TestCommandVerifier)
        assert verifier.automated

    def test_a_test_command_alone_skips_the_all_files_compile(self, tmp_path: Path) -> None:
        verifier = build_verifier(tmp_path, settings(test_command="run-tests"))

        assert verifier.name == "the test command"

    def test_build_none_requires_a_test_command(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="--test-cmd"):
            build_verifier(tmp_path, settings(build_system="none"))


class TestRunCommand:
    def test_reports_success_and_combined_output(self, tmp_path: Path) -> None:
        result = run_command(f'{PYTHON} -c "print(1)"', tmp_path, 30, 1000)

        assert result.succeeded
        assert result.output.strip() == "1"

    def test_reports_failure(self, tmp_path: Path) -> None:
        result = run_command(f'{PYTHON} -c "exit(3)"', tmp_path, 30, 1000)

        assert not result.succeeded
        assert not result.timed_out

    def test_runs_in_the_project_directory(self, tmp_path: Path) -> None:
        (tmp_path / "marker.txt").write_text("x")
        code = "import os; print(os.listdir('.'))"

        assert "marker.txt" in run_command(f'{PYTHON} -c "{code}"', tmp_path, 30, 1000).output

    def test_a_hanging_command_with_a_child_is_killed_promptly(self, tmp_path: Path) -> None:
        # The child inherits the output pipe, which is what hangs a plain subprocess.run.
        sleeper = "import time; time.sleep(60)"
        child = f"import subprocess, sys; subprocess.run([sys.executable, '-c', '{sleeper}'])"
        started = time.monotonic()

        result = run_command(f'{PYTHON} -c "{child}"', tmp_path, 1, 1000)

        assert result.timed_out
        assert not result.succeeded
        assert time.monotonic() - started < 20

    def test_long_output_is_truncated(self, tmp_path: Path) -> None:
        result = run_command(f"{PYTHON} -c \"print('x' * 5000)\"", tmp_path, 30, 100)

        assert result.output.endswith("[output truncated]")


class TestTestCommandVerifier:
    def test_exit_code_zero_passes(self, tmp_path: Path) -> None:
        verifier = TestCommandVerifier(None, f'{PYTHON} -c "exit(0)"', tmp_path, 30, 1000)

        assert verifier.verify([]).tests_passed is True

    def test_nonzero_exit_fails_with_the_output(self, tmp_path: Path) -> None:
        command = f"{PYTHON} -c \"print('boom'); exit(1)\""

        result = TestCommandVerifier(None, command, tmp_path, 30, 1000).verify([])

        assert result.compiled
        assert result.tests_passed is False
        assert "boom" in result.output

    def test_a_timeout_counts_as_a_failure(self, tmp_path: Path) -> None:
        command = f'{PYTHON} -c "import time; time.sleep(60)"'

        result = TestCommandVerifier(None, command, tmp_path, 1, 1000).verify([])

        assert result.tests_passed is False
        assert "did not finish in 1 seconds" in result.output

    def test_a_failed_build_stops_before_the_tests(self, tmp_path: Path) -> None:
        build = FakeVerifier([VerificationResult(False, "syntax error")])
        command = f"{PYTHON} -c \"open('ran.txt', 'w')\""

        result = TestCommandVerifier(build, command, tmp_path, 30, 1000).verify([])

        assert not result.compiled
        assert not (tmp_path / "ran.txt").exists()


@requires_make
@requires_gpp
class TestMake:
    def write_project(self, root: Path, body: str, recipe: str) -> None:
        (root / "main.cpp").write_text(body)
        (root / "Makefile").write_text(
            f"program.exe: main.cpp\n\tg++ main.cpp -o program.exe\n\n{recipe}"
        )

    def test_builds_a_project_without_a_test_target(self, tmp_path: Path) -> None:
        self.write_project(tmp_path, PROGRAM, "")

        verifier = MakeVerifier(tmp_path, 60, 3000)
        result = verifier.verify([])

        assert not verifier.automated
        assert result.compiled
        assert result.tests_passed is None

    def test_reports_a_build_failure(self, tmp_path: Path) -> None:
        self.write_project(tmp_path, "int main( {\n", "")

        result = MakeVerifier(tmp_path, 60, 3000).verify([])

        assert not result.compiled
        assert "error" in result.output

    @pytest.mark.parametrize(("exit_code", "expected"), [(0, True), (1, False)])
    def test_runs_the_test_target(self, tmp_path: Path, exit_code: int, expected: bool) -> None:
        self.write_project(
            tmp_path,
            f"int main() {{ return {exit_code}; }}\n",
            "test: program.exe\n\t./program.exe\n",
        )

        verifier = MakeVerifier(tmp_path, 60, 3000)

        assert verifier.automated
        assert verifier.verify([]).tests_passed is expected


@requires_cmake
@requires_gpp
class TestCMake:
    LISTS = (
        "cmake_minimum_required(VERSION 3.10)\n"
        "project(demo CXX)\n"
        "add_executable(program main.cpp)\n"
    )

    def write_project(self, root: Path, body: str, *, with_tests: bool) -> None:
        tests = "enable_testing()\nadd_test(NAME runs COMMAND program)\n" if with_tests else ""
        (root / "main.cpp").write_text(body)
        (root / "CMakeLists.txt").write_text(self.LISTS + tests)

    def configure_or_skip(self, root: Path) -> None:
        probe = subprocess.run(
            ["cmake", "-S", str(root), "-B", str(root / "probe")],
            capture_output=True,
            text=True,
            check=False,
        )
        shutil.rmtree(root / "probe", ignore_errors=True)
        if probe.returncode != 0:
            pytest.skip("CMake found no usable compiler")

    def test_a_project_without_tests_gets_no_verdict(self, tmp_path: Path) -> None:
        self.write_project(tmp_path, PROGRAM, with_tests=False)
        self.configure_or_skip(tmp_path)

        verifier = CMakeVerifier(tmp_path, 120, 3000)
        result = verifier.verify([])

        assert not verifier.automated
        assert result.compiled, result.output
        assert result.tests_passed is None

    @pytest.mark.parametrize(("exit_code", "expected"), [(0, True), (1, False)])
    def test_runs_ctest(self, tmp_path: Path, exit_code: int, expected: bool) -> None:
        self.write_project(tmp_path, f"int main() {{ return {exit_code}; }}\n", with_tests=True)
        self.configure_or_skip(tmp_path)

        verifier = CMakeVerifier(tmp_path, 120, 3000)
        result = verifier.verify([])

        assert verifier.automated
        assert result.compiled, result.output
        assert result.tests_passed is expected

    def test_a_syntax_error_is_a_build_failure(self, tmp_path: Path) -> None:
        self.write_project(tmp_path, PROGRAM, with_tests=True)
        self.configure_or_skip(tmp_path)
        (tmp_path / "main.cpp").write_text("int main( {\n")

        assert not CMakeVerifier(tmp_path, 120, 3000).verify([]).compiled

    def test_the_build_tree_ignores_itself_in_git(self, tmp_path: Path) -> None:
        self.write_project(tmp_path, PROGRAM, with_tests=False)
        self.configure_or_skip(tmp_path)

        CMakeVerifier(tmp_path, 120, 3000).verify([])

        assert (tmp_path / CMAKE_BUILD_DIRECTORY / ".gitignore").read_text() == "*\n"
