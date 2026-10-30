from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from patchpilot.sandbox import (
    PRLIMIT,
    SandboxedVerifier,
    bwrap_command,
    sandbox_available,
)

# CI sets this so that a sandbox which stops working fails the build instead of being skipped.
REQUIRED = os.environ.get("PATCHPILOT_REQUIRE_SANDBOX") == "1"
AVAILABLE = sandbox_available()

requires_sandbox = pytest.mark.skipif(
    not AVAILABLE and not REQUIRED, reason="Bubblewrap cannot run here"
)


def compile_and_run(tmp_path: Path, source: str, run_timeout: int = 5) -> tuple[bool, str]:
    path = tmp_path / "main.cpp"
    path.write_text(source)
    result = SandboxedVerifier(tmp_path, run_timeout=run_timeout).verify([path])
    return result.compiled, result.output


class TestCommand:
    def test_isolates_everything_that_matters(self) -> None:
        command = bwrap_command(["/work/out/program"], ())

        assert command[0] == "bwrap"
        for flag in ("--unshare-all", "--die-with-parent", "--clearenv", "--new-session"):
            assert flag in command
        assert command[command.index("--cap-drop") + 1] == "ALL"

    def test_does_not_expose_the_home_directory_or_etc(self) -> None:
        command = bwrap_command(["true"], ())

        mounts = [command[i + 1] for i, part in enumerate(command) if "bind" in part]
        assert "/" not in mounts
        assert "/etc" not in mounts
        assert "/home" not in mounts

    def test_applies_resource_limits_around_the_program(self) -> None:
        command = bwrap_command(["/work/out/program"], ())

        limits = command[command.index(PRLIMIT) :]
        assert any(part.startswith("--cpu=") for part in limits)
        assert any(part.startswith("--as=") for part in limits)
        assert any(part.startswith("--fsize=") for part in limits)
        assert any(part.startswith("--nproc=") for part in limits)
        assert limits[-1] == "/work/out/program"

    def test_includes_the_requested_mounts(self) -> None:
        command = bwrap_command(["true"], (("--ro-bind", "/tmp/source", "/work/src"),))

        index = command.index("/tmp/source")
        assert command[index - 1 : index + 2] == ["--ro-bind", "/tmp/source", "/work/src"]


def test_is_unavailable_without_bubblewrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("patchpilot.sandbox.shutil.which", lambda _: None)

    assert not sandbox_available()


def test_a_broken_sandbox_counts_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("patchpilot.sandbox.shutil.which", lambda _: "/usr/bin/bwrap")

    def denied(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 1, b"", b"setting up uid map: Permission denied")

    monkeypatch.setattr("patchpilot.sandbox.subprocess.run", denied)

    assert not sandbox_available()


def test_a_probe_that_cannot_start_counts_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("patchpilot.sandbox.shutil.which", lambda _: "/usr/bin/bwrap")

    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("bwrap")

    monkeypatch.setattr("patchpilot.sandbox.subprocess.run", missing)

    assert not sandbox_available()


@requires_sandbox
class TestInsideTheSandbox:
    """Real isolation checks. They run wherever Bubblewrap works, including CI."""

    def test_compiles_and_runs_a_program(self, tmp_path: Path) -> None:
        compiled, output = compile_and_run(
            tmp_path, '#include <iostream>\nint main() { std::cout << "hello"; }\n'
        )

        assert compiled
        assert output == "hello"

    def test_reports_compile_errors(self, tmp_path: Path) -> None:
        compiled, output = compile_and_run(tmp_path, "int main( {\n")

        assert not compiled
        assert "error" in output

    def test_kills_an_infinite_loop(self, tmp_path: Path) -> None:
        compiled, output = compile_and_run(tmp_path, "int main() { while (true) {} }\n", 1)

        assert compiled
        assert "did not finish" in output

    def test_the_program_has_no_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "server-secret")

        _, output = compile_and_run(
            tmp_path,
            "#include <cstdio>\n#include <cstdlib>\n"
            'int main() { const char *k = getenv("GROQ_API_KEY"); puts(k ? k : "unset"); }\n',
        )

        assert output.strip() == "unset"

    def test_the_compiler_cannot_read_server_secrets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "server-secret")

        _, output = compile_and_run(tmp_path, '#include "/proc/self/environ"\nint main() {}\n')

        assert "server-secret" not in output

    def test_the_program_cannot_read_the_project_or_home(self, tmp_path: Path) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("top secret")

        _, output = compile_and_run(
            tmp_path,
            "#include <cstdio>\n"
            f'int main() {{ puts(fopen("{secret.as_posix()}", "r") ? "read" : "blocked"); '
            f'puts(fopen("{Path.home().as_posix()}/.bashrc", "r") ? "read" : "blocked"); }}\n',
        )

        assert "read" not in output

    def test_the_program_has_no_network(self, tmp_path: Path) -> None:
        _, output = compile_and_run(
            tmp_path,
            "#include <arpa/inet.h>\n#include <cstdio>\n#include <sys/socket.h>\n"
            "int main() {\n"
            "  sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(53);\n"
            '  inet_pton(AF_INET, "1.1.1.1", &a.sin_addr);\n'
            "  int s = socket(AF_INET, SOCK_STREAM, 0);\n"
            '  puts(connect(s, (sockaddr *)&a, sizeof a) == 0 ? "connected" : "blocked");\n'
            "}\n",
        )

        assert output.strip() == "blocked"

    def test_runaway_output_does_not_exhaust_memory(self, tmp_path: Path) -> None:
        compiled, output = compile_and_run(
            tmp_path,
            '#include <cstdio>\nint main() { for (;;) puts("xxxxxxxxxxxxxxxxxxxxxxxxx"); }\n',
            5,
        )

        assert compiled
        assert len(output) < 4000


def test_nothing_is_left_behind_on_an_empty_project(tmp_path: Path) -> None:
    result = SandboxedVerifier(tmp_path).verify([])

    assert not result.compiled
    assert shutil.which("bwrap") is None or "No C++ source files" in result.output
