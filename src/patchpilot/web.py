"""The logic behind the hosted web demo, kept free of Streamlit so it can be tested directly.

A run works on a throwaway copy of the visitor's code, so edits are applied without asking
and the visitor reviews the final diff instead. Model-written code is only ever executed
inside the Bubblewrap sandbox. Without a working sandbox the demo still proposes a fix, but
compiles and runs nothing.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .agent import CodingAgent
from .config import Settings, get_provider
from .errors import ConfigError
from .llm import LLMClient, create_client
from .sandbox import SandboxedVerifier, sandbox_available
from .ui import DEFAULT_PREVIEW_LINES
from .verifier import VerificationResult, Verifier
from .workspace import CODE_EXTENSIONS, Workspace

WEB_PROVIDERS = ("groq", "gemini", "openrouter")  # Ollama would mean the server's localhost.
MAX_FILES = 10
MAX_TOTAL_BYTES = 100_000
MAX_RUNS_PER_SESSION = 5
WEB_MAX_ATTEMPTS = 2
WEB_MAX_STEPS = 8

LineSink = Callable[[str], None]
VerifierFactory = Callable[[Path], Verifier]


@dataclass(frozen=True, slots=True)
class FixOutcome:
    """What a web run produced."""

    solved: bool
    diff: str
    executed: bool
    log: str
    llm_calls: int
    tokens: int


class WebUI:
    """Collects agent output as text. Nobody can answer a prompt during a web run."""

    def __init__(self, on_line: LineSink | None = None) -> None:
        self.lines: list[str] = []
        self._on_line = on_line

    def _emit(self, line: str) -> None:
        self.lines.append(line)
        if self._on_line:
            self._on_line(line)

    def header(self, title: str, char: str = "=") -> None:
        self._emit(f"\n{title}")

    def say(self, message: str = "") -> None:
        self._emit(message)

    def block(self, text: str, max_lines: int = DEFAULT_PREVIEW_LINES) -> None:
        lines = text.splitlines()
        for line in lines[:max_lines]:
            self._emit(f"  | {line}")
        if len(lines) > max_lines:
            self._emit(f"  | ... ({len(lines) - max_lines} more lines)")

    def ask(self, question: str) -> str:
        # "Is the bug fixed?" only ends the run: the visitor judges the diff afterwards.
        return "y" if "fixed" in question else ""


class NotExecutedVerifier:
    """Stands in for verification where no sandbox exists: nothing is compiled or run."""

    name = "nothing, because this server has no sandbox"
    automated = False

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        return VerificationResult(
            compiled=True, output="(Not compiled: no sandbox on this server.)", skipped=True
        )


class ExpectedOutputVerifier:
    """Turns the visitor's expected output into a test verdict."""

    automated = True

    def __init__(self, inner: Verifier, expected: str) -> None:
        self.name = inner.name
        self._inner = inner
        self._expected = _normalise(expected)

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        result = self._inner.verify(sources)
        if not result.compiled:
            return result
        return VerificationResult(
            compiled=True,
            output=f"{result.output}\n\nExpected output:\n{self._expected}",
            tests_passed=_normalise(result.output) == self._expected,
        )


def _normalise(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def validate_files(files: Mapping[str, str]) -> dict[str, str]:
    """Check submitted files, returning them keyed by bare file name.

    Directory parts are dropped, so a crafted upload name cannot escape the temp directory.

    Raises:
        ConfigError: if the files are missing, too large or not C/C++ sources.
    """
    if not files:
        raise ConfigError("Add at least one C or C++ file.")
    if len(files) > MAX_FILES:
        raise ConfigError(f"The demo accepts at most {MAX_FILES} files.")

    cleaned: dict[str, str] = {}
    for name, content in files.items():
        safe_name = PurePosixPath(name.replace("\\", "/")).name
        if PurePosixPath(safe_name).suffix.lower() not in CODE_EXTENSIONS:
            raise ConfigError(f"{name} is not a C or C++ source file.")
        cleaned[safe_name] = content

    if sum(len(content.encode("utf-8")) for content in cleaned.values()) > MAX_TOTAL_BYTES:
        raise ConfigError(f"The demo accepts at most {MAX_TOTAL_BYTES // 1000} KB of code.")
    return cleaned


def web_settings(provider: str, model: str, api_key: str) -> Settings:
    """Build the tightly limited settings used for a web run.

    Raises:
        ConfigError: if the provider is not offered on the web or the key is empty.
    """
    if provider not in WEB_PROVIDERS:
        raise ConfigError(f"Provider {provider!r} is not available in the web demo.")
    if not api_key.strip():
        raise ConfigError("Enter an API key.")

    chosen = get_provider(provider)
    return Settings(
        api_key=api_key.strip(),
        model=model.strip() or chosen.default_model,
        provider=provider,
        base_url=chosen.base_url,
        max_attempts=WEB_MAX_ATTEMPTS,
        max_steps=WEB_MAX_STEPS,
        api_max_retries=3,
        auto_approve=True,
    )


def default_verifier(root: Path) -> Verifier:
    """Return the sandbox if it really works here, otherwise something that runs nothing."""
    return SandboxedVerifier(root) if sandbox_available() else NotExecutedVerifier()


def run_fix(
    files: Mapping[str, str],
    bug: str,
    settings: Settings,
    *,
    expected_output: str = "",
    client: LLMClient | None = None,
    verifier_factory: VerifierFactory = default_verifier,
    on_line: LineSink | None = None,
) -> FixOutcome:
    """Run the agent on a throwaway copy of ``files`` and return the diff it produced."""
    cleaned = validate_files(files)
    ui = WebUI(on_line)

    with tempfile.TemporaryDirectory(prefix="patchpilot-web-") as scratch:
        root = Path(scratch)
        for name, content in cleaned.items():
            (root / name).write_text(content, encoding="utf-8", newline="")

        verifier = verifier_factory(root)
        executed = not isinstance(verifier, NotExecutedVerifier)
        if executed and expected_output.strip():
            verifier = ExpectedOutputVerifier(verifier, expected_output)

        workspace = Workspace(root)
        agent = CodingAgent(
            workspace=workspace,
            llm=client or create_client(settings),
            verifier=verifier,
            ui=ui,
            settings=settings,
        )
        solved = agent.solve(bug)

        return FixOutcome(
            solved=solved,
            diff=workspace.diff(agent.original_files) if solved else "",
            executed=executed,
            log="\n".join(ui.lines),
            llm_calls=agent.usage.calls,
            tokens=agent.usage.total_tokens,
        )
