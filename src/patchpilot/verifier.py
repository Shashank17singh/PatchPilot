"""Build, run and test the code after the model has edited it.

Every verifier returns a :class:`VerificationResult`. When it carries a test verdict the
agent can judge the fix itself; without one the agent asks the user.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Settings
from .errors import ConfigError

logger = logging.getLogger(__name__)

# Lives inside the project so that a --test-cmd can reach the build output.
CMAKE_BUILD_DIRECTORY = ".patchpilot-build"
# Multi-config generators such as Visual Studio need this named, or CTest finds no binary.
_CMAKE_CONFIGURATION = "Debug"

_MAKE_TEST_TARGET = re.compile(r"^(test|check)\s*:", re.MULTILINE)
_CMAKE_REGISTERS_TESTS = re.compile(r"\badd_test\s*\(|_discover_tests\s*\(", re.IGNORECASE)
_CTEST_TOTAL = re.compile(r"Total Tests:\s*(\d+)")
_KILL_GRACE_SECONDS = 5


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of building and testing the project.

    Attributes:
        compiled: ``True`` if the build succeeded (or was skipped).
        output: compiler errors if the build failed, otherwise the program or test output.
        skipped: ``True`` if no build tool was available and nothing was built.
        tests_passed: the automated verdict, or ``None`` if no test produced one.
    """

    compiled: bool
    output: str
    skipped: bool = False
    tests_passed: bool | None = None


class Verifier(Protocol):
    """Anything that can build and check a set of source files.

    ``automated`` is ``True`` when :meth:`verify` is expected to return a test verdict.
    """

    name: str
    automated: bool

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        """Build ``sources`` and, if that succeeds, run or test the result."""


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of a build or test command."""

    succeeded: bool
    output: str
    timed_out: bool = False


def truncate(text: str, limit: int) -> str:
    """Shorten ``text`` to ``limit`` characters, marking that it was cut."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [output truncated]"


def run_command(
    command: str | Sequence[str], cwd: Path, timeout: int, output_limit: int
) -> CommandResult:
    """Run a build or test command, killing its whole process tree on timeout.

    A string runs through the shell, a sequence runs directly. A plain ``subprocess.run``
    timeout would kill only the shell and then wait forever for its children to close the
    output pipe, which is why this manages the process itself.
    """
    process = subprocess.Popen(
        command,
        cwd=cwd,
        shell=isinstance(command, str),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        start_new_session=os.name != "nt",
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            output, _ = process.communicate(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            output = ""
        return CommandResult(False, truncate(output or "", output_limit), timed_out=True)
    return CommandResult(process.returncode == 0, truncate(output, output_limit))


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """Kill ``process`` and everything it started."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        os.killpg(process.pid, signal.SIGKILL)


class CppVerifier:
    """Compiles sources with a C++ compiler and runs the result under a time limit.

    With ``exit_code_is_verdict`` the program acts as the test: exit code 0 means passed.
    """

    def __init__(
        self,
        compiler: str = "g++",
        run_timeout: int = 10,
        output_limit: int = 3000,
        *,
        flags: Sequence[str] = (),
        exit_code_is_verdict: bool = False,
    ):
        self.name = compiler
        self.automated = exit_code_is_verdict
        self._run_timeout = run_timeout
        self._output_limit = output_limit
        self._flags = tuple(flags)
        self._exit_code_is_verdict = exit_code_is_verdict

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        if shutil.which(self.name) is None:
            return VerificationResult(
                compiled=True,
                output=f"({self.name} not found, skipped compiling. Check the code yourself.)",
                skipped=True,
            )
        if not sources:
            return VerificationResult(False, "No C++ source files (.cpp, .cc) found to compile.")

        # A private temp directory per run avoids collisions between concurrent runs and
        # guarantees the binary is cleaned up afterwards.
        with tempfile.TemporaryDirectory(prefix="patchpilot-") as scratch:
            executable = Path(scratch) / ("program.exe" if os.name == "nt" else "program")

            command = [self.name, *self._flags, *map(str, sources), "-o", str(executable)]
            logger.debug("Compiling: %s", " ".join(command))
            build = subprocess.run(
                command, capture_output=True, encoding="utf-8", errors="replace", check=False
            )
            if build.returncode != 0:
                return VerificationResult(False, truncate(build.stderr, self._output_limit))

            output, succeeded = self._run(executable)
            verdict = succeeded if self._exit_code_is_verdict else None
            return VerificationResult(True, output, tests_passed=verdict)

    def _run(self, executable: Path) -> tuple[str, bool]:
        """Run the compiled program, returning its combined output and whether it succeeded."""
        try:
            completed = subprocess.run(
                [str(executable)],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._run_timeout,
                # Closing stdin stops a program that reads from cin from hanging the terminal.
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"Program did not finish in {self._run_timeout} seconds (infinite loop?)", False
        except OSError as exc:
            return f"Could not start the compiled program: {exc}", False

        output = completed.stdout + completed.stderr
        if completed.returncode != 0:
            output += f"\n[process exited with code {completed.returncode}]"
        return truncate(output, self._output_limit), completed.returncode == 0


class MakeVerifier:
    """Builds with ``make``, then runs a ``test`` or ``check`` target if the Makefile has one."""

    def __init__(self, root: Path, timeout: int, output_limit: int, *, run_tests: bool = True):
        self.name = "make"
        self._root = root
        self._timeout = timeout
        self._output_limit = output_limit
        self._tool = shutil.which("make") or shutil.which("mingw32-make")
        self._test_target = self._find_test_target() if run_tests else None
        self.automated = self._test_target is not None

    def _find_test_target(self) -> str | None:
        for name in ("Makefile", "makefile", "GNUmakefile"):
            path = self._root / name
            if path.is_file():
                match = _MAKE_TEST_TARGET.search(path.read_text(encoding="utf-8", errors="replace"))
                return match.group(1) if match else None
        return None

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        if self._tool is None:
            return VerificationResult(True, "(make not found, skipped building.)", skipped=True)

        build = run_command([self._tool], self._root, self._timeout, self._output_limit)
        if not build.succeeded:
            return VerificationResult(False, _build_failure(build, self._timeout))
        if self._test_target is None:
            return VerificationResult(True, build.output)

        tests = run_command(
            [self._tool, self._test_target], self._root, self._timeout, self._output_limit
        )
        output = _test_output(tests, self._timeout)
        return VerificationResult(True, output, tests_passed=tests.succeeded)


class CMakeVerifier:
    """Configures and builds with CMake, then runs CTest if the project registers tests.

    The build tree sits in :data:`CMAKE_BUILD_DIRECTORY`, so later attempts rebuild only what
    changed and an existing ``build/`` directory is left alone.
    """

    def __init__(self, root: Path, timeout: int, output_limit: int, *, run_tests: bool = True):
        self.name = "cmake"
        self._root = root
        self._timeout = timeout
        self._output_limit = output_limit
        self._run_tests = run_tests
        self.automated = run_tests and _cmake_registers_tests(root)

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        if shutil.which("cmake") is None:
            return VerificationResult(True, "(cmake not found, skipped building.)", skipped=True)

        build_dir = self._root / CMAKE_BUILD_DIRECTORY
        build_dir.mkdir(exist_ok=True)
        (build_dir / ".gitignore").write_text("*\n", encoding="utf-8")

        for command in (
            ["cmake", "-S", str(self._root), "-B", str(build_dir)],
            ["cmake", "--build", str(build_dir), "--config", _CMAKE_CONFIGURATION],
        ):
            step = run_command(command, self._root, self._timeout, self._output_limit)
            if not step.succeeded:
                return VerificationResult(False, _build_failure(step, self._timeout))

        if not self._run_tests or shutil.which("ctest") is None:
            return VerificationResult(True, step.output)

        # CTest exits 0 when it finds no tests, which must not count as a pass.
        ctest = ["ctest", "-C", _CMAKE_CONFIGURATION]
        listing = run_command([*ctest, "-N"], build_dir, self._timeout, self._output_limit)
        total = _CTEST_TOTAL.search(listing.output)
        if total is None or int(total.group(1)) == 0:
            return VerificationResult(True, step.output)

        tests = run_command(
            [*ctest, "--output-on-failure"], build_dir, self._timeout, self._output_limit
        )
        output = _test_output(tests, self._timeout)
        return VerificationResult(True, output, tests_passed=tests.succeeded)


class TestCommandVerifier:
    """Runs the user's ``--test-cmd`` in the project, optionally after a build step."""

    __test__ = False  # Stops pytest from collecting this class because of its name.
    automated = True

    def __init__(
        self, build: Verifier | None, command: str, root: Path, timeout: int, output_limit: int
    ) -> None:
        self.name = f"{build.name} and tests" if build else "the test command"
        self._build = build
        self._command = command
        self._root = root
        self._timeout = timeout
        self._output_limit = output_limit

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        built = self._build.verify(sources) if self._build else None
        if built is not None and not built.compiled:
            return built

        tests = run_command(self._command, self._root, self._timeout, self._output_limit)
        return VerificationResult(
            compiled=True,
            output=_test_output(tests, self._timeout),
            skipped=built.skipped if built else False,
            tests_passed=tests.succeeded,
        )


def build_verifier(root: Path, settings: Settings) -> Verifier:
    """Choose the verifier for a project from its files and the user's settings.

    Raises:
        ConfigError: if ``--build none`` was given without a test command.
    """
    test_command = settings.test_command
    build = _build_step(root, settings, run_tests=test_command is None)
    if test_command:
        return TestCommandVerifier(
            build, test_command, root, settings.command_timeout, settings.output_limit
        )
    if build is None:
        raise ConfigError("--build none needs a --test-cmd, otherwise nothing checks the fix.")
    return build


def _build_step(root: Path, settings: Settings, *, run_tests: bool) -> Verifier | None:
    system = settings.build_system
    if system == "auto":
        system = detect_build_system(root)
        if system == "g++" and settings.test_command:
            # Compiling every .cpp together breaks on separate test mains, and a test command
            # for a project with no build system has to build the code itself anyway.
            return None

    limits = (root, settings.command_timeout, settings.output_limit)
    if system == "make":
        return MakeVerifier(*limits, run_tests=run_tests)
    if system == "cmake":
        return CMakeVerifier(*limits, run_tests=run_tests)
    if system == "none":
        return None
    return CppVerifier(run_timeout=settings.run_timeout, output_limit=settings.output_limit)


def detect_build_system(root: Path) -> str:
    """Return ``cmake`` or ``make`` if the project uses one of them, otherwise ``g++``."""
    if (root / "CMakeLists.txt").is_file():
        return "cmake"
    if any((root / name).is_file() for name in ("Makefile", "makefile", "GNUmakefile")):
        return "make"
    return "g++"


def _cmake_registers_tests(root: Path) -> bool:
    for path in root.rglob("CMakeLists.txt"):
        if CMAKE_BUILD_DIRECTORY in path.parts:
            continue
        if _CMAKE_REGISTERS_TESTS.search(path.read_text(encoding="utf-8", errors="replace")):
            return True
    return False


def _build_failure(result: CommandResult, timeout: int) -> str:
    if result.timed_out:
        return f"Build did not finish in {timeout} seconds.\n{result.output}"
    return result.output


def _test_output(result: CommandResult, timeout: int) -> str:
    if result.timed_out:
        return f"Tests did not finish in {timeout} seconds.\n{result.output}"
    return result.output
