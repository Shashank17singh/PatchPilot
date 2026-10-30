"""Isolated compile-and-run for code nobody has reviewed.

The hosted web demo builds and runs model-written code on a shared server, so it uses
Bubblewrap (Linux only) instead of :class:`~patchpilot.verifier.CppVerifier`:

- Both the compile and the run happen inside the sandbox. Compiling is not safe either:
  ``#include "/proc/self/environ"`` would put the server's secrets into compiler errors.
- The sandbox has no network, its own PID namespace, an empty environment, no capabilities
  and a read-only view of ``/usr``. The project files and the app's own files are not
  visible to the program.
- CPU time, memory, file size and process count are capped, and output goes to a size-limited
  file so a program printing forever cannot exhaust the server's memory.

Callers must check :func:`sandbox_available` and refuse to run anything when it is ``False``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .verifier import VerificationResult, truncate

logger = logging.getLogger(__name__)

COMPILER = "/usr/bin/g++"
PRLIMIT = "/usr/bin/prlimit"
PROBE_TIMEOUT = 10
COMPILE_TIMEOUT = 60

CPU_SECONDS = 30
MEMORY_BYTES = 1024**3
FILE_BYTES = 32 * 1024**2
MAX_PROCESSES = 256

_SOURCE_DIR = "/work/src"
_OUTPUT_DIR = "/work/out"


def bwrap_command(argv: Sequence[str], binds: Sequence[tuple[str, str, str]]) -> list[str]:
    """Wrap ``argv`` in a Bubblewrap and resource-limit invocation.

    Args:
        argv: the command to run inside the sandbox.
        binds: ``(flag, host_path, sandbox_path)`` for each mounted directory, where the
            flag is ``--ro-bind`` or ``--bind``.
    """
    command = [
        "bwrap",
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind-try",
        "/lib",
        "/lib",
        "--ro-bind-try",
        "/lib64",
        "/lib64",
        "--ro-bind-try",
        "/bin",
        "/bin",
        "--ro-bind-try",
        "/etc/alternatives",
        "/etc/alternatives",
        "--ro-bind-try",
        "/etc/ld.so.cache",
        "/etc/ld.so.cache",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    for flag, host_path, sandbox_path in binds:
        command += [flag, host_path, sandbox_path]
    command += [
        "--chdir",
        "/tmp",
        "--",
        PRLIMIT,
        f"--cpu={CPU_SECONDS}",
        f"--as={MEMORY_BYTES}",
        f"--fsize={FILE_BYTES}",
        f"--nproc={MAX_PROCESSES}",
        "--",
        *argv,
    ]
    return command


def sandbox_available() -> bool:
    """Return True only if a sandboxed compiler really starts on this machine.

    Bubblewrap being installed is not enough: many containers and hardened kernels forbid
    the user namespaces it depends on, so this runs it instead of looking for the binary.
    """
    if shutil.which("bwrap") is None:
        return False
    try:
        completed = subprocess.run(
            bwrap_command([COMPILER, "--version"], ()),
            capture_output=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("Sandbox probe could not start", exc_info=True)
        return False
    if completed.returncode != 0:
        logger.warning("Sandbox probe failed: %s", completed.stderr[:500])
    return completed.returncode == 0


class SandboxedVerifier:
    """Compiles and runs a project inside a Bubblewrap sandbox."""

    name = "g++ in a Bubblewrap sandbox"
    automated = False

    def __init__(self, root: Path, run_timeout: int = 10, output_limit: int = 3000) -> None:
        self._root = root
        self._run_timeout = run_timeout
        self._output_limit = output_limit

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        if not sources:
            return VerificationResult(False, "No C++ source files (.cpp, .cc) found to compile.")

        with tempfile.TemporaryDirectory(prefix="patchpilot-sandbox-") as scratch:
            output_dir = Path(scratch) / "out"
            output_dir.mkdir()
            log = Path(scratch) / "output.log"
            binds = (
                ("--ro-bind", str(self._root), _SOURCE_DIR),
                ("--bind", str(output_dir), _OUTPUT_DIR),
            )

            relative = [path.relative_to(self._root).as_posix() for path in sources]
            program = f"{_OUTPUT_DIR}/program"
            compile_argv = [
                COMPILER,
                *(f"{_SOURCE_DIR}/{path}" for path in relative),
                "-o",
                program,
            ]
            compile_code = self._execute(compile_argv, binds, log, COMPILE_TIMEOUT)
            if compile_code != 0:
                reason = "Compile timed out." if compile_code is None else self._read(log)
                return VerificationResult(False, reason)

            # The run sees only the binary, not the sources.
            run_binds = (("--ro-bind", str(output_dir), _OUTPUT_DIR),)
            run_code = self._execute([program], run_binds, log, self._run_timeout)
            if run_code is None:
                message = f"Program did not finish in {self._run_timeout} seconds (infinite loop?)"
                return VerificationResult(True, message)

            output = self._read(log)
            if run_code != 0:
                output += f"\n[process exited with code {run_code}]"
            return VerificationResult(True, output)

    @staticmethod
    def _execute(
        argv: Sequence[str],
        binds: Sequence[tuple[str, str, str]],
        log: Path,
        timeout: int,
    ) -> int | None:
        """Run ``argv`` in the sandbox, returning its exit code, or ``None`` on timeout."""
        with open(log, "wb") as output:
            try:
                completed = subprocess.run(
                    bwrap_command(argv, binds),
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env={"PATH": "/usr/bin:/bin"},  # The server's secrets stay out of bwrap too.
                    timeout=timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return None  # Killing bwrap takes the whole PID namespace down with it.
        return completed.returncode

    def _read(self, log: Path) -> str:
        with open(log, encoding="utf-8", errors="replace") as handle:
            return truncate(handle.read(self._output_limit + 1), self._output_limit)
