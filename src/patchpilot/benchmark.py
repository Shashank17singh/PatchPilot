"""Benchmark runner: measures how often a model fixes a set of seeded bugs.

Each case in ``benchmarks/cases/<id>/`` has a ``project/`` directory containing the bug, a
``case.json`` with the bug report, and a ``hidden_test.cpp`` that decides success. The hidden
test stays outside the copy the agent works on, so a model cannot pass by editing it.

Usage::

    patchpilot-bench --models groq,ollama:gpt-oss:20b --repeat 3
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .agent import CodingAgent
from .config import PROVIDERS, Settings, get_provider
from .errors import ConfigError, LLMError, PatchPilotError
from .llm import LLMClient, create_client
from .ui import DEFAULT_PREVIEW_LINES
from .verifier import CppVerifier, VerificationResult
from .workspace import Workspace

DEFAULT_CASES_DIRECTORY = Path("benchmarks/cases")
DEFAULT_RESULTS_DIRECTORY = Path("benchmarks/results")
HIDDEN_TEST_TIMEOUT = 5

ClientFactory = Callable[[Settings], LLMClient]


@dataclass(frozen=True, slots=True)
class ReferenceFix:
    """One text substitution of a case's known-good solution."""

    file: str
    find: str
    replace: str


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One seeded bug."""

    id: str
    title: str
    kind: str
    report: str
    directory: Path
    fixes: tuple[ReferenceFix, ...]

    @property
    def project(self) -> Path:
        return self.directory / "project"

    @property
    def hidden_test(self) -> Path:
        return self.directory / "hidden_test.cpp"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A provider and model to evaluate."""

    provider: str
    model: str

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Outcome of one run of one case."""

    model: str
    case: str
    run: int
    solved: bool
    attempts: int
    llm_calls: int
    tokens: int
    seconds: float
    error: str | None = None


class HiddenTestVerifier:
    """Judges a case by compiling its hidden test against the agent's copy of the project."""

    name = "the hidden test"
    automated = True

    def __init__(self, case: BenchmarkCase, project_copy: Path) -> None:
        self._hidden_test = case.hidden_test
        self._compiler = CppVerifier(
            run_timeout=HIDDEN_TEST_TIMEOUT,
            flags=(f"-I{project_copy}", f"-I{case.directory.parent}"),
            exit_code_is_verdict=True,
        )

    def verify(self, sources: Sequence[Path]) -> VerificationResult:
        return self._compiler.verify([self._hidden_test])


class SilentUI:
    """Discards agent output. A benchmark run has nobody to answer questions."""

    def header(self, title: str, char: str = "=") -> None:
        pass

    def say(self, message: str = "") -> None:
        pass

    def block(self, text: str, max_lines: int = DEFAULT_PREVIEW_LINES) -> None:
        pass

    def ask(self, question: str) -> str:
        return ""


def load_cases(directory: Path) -> list[BenchmarkCase]:
    """Load every case under ``directory``, sorted by id.

    Raises:
        ConfigError: if the directory contains no cases.
    """
    cases: list[BenchmarkCase] = []
    for path in sorted(directory.glob("*/case.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.append(
            BenchmarkCase(
                id=path.parent.name,
                title=data["title"],
                kind=data["kind"],
                report=data["report"],
                directory=path.parent,
                fixes=tuple(ReferenceFix(**fix) for fix in data["fix"]),
            )
        )
    if not cases:
        raise ConfigError(f"No benchmark cases found in {directory}.")
    return cases


def parse_model_spec(text: str) -> ModelSpec:
    """Parse ``provider`` or ``provider:model``. Only the first colon separates them."""
    provider_name, _, model = text.strip().partition(":")
    provider = get_provider(provider_name)
    return ModelSpec(provider.name, model or provider.default_model)


def run_case(
    case: BenchmarkCase,
    spec: ModelSpec,
    client_factory: ClientFactory = create_client,
    run: int = 1,
    env: dict[str, str] | None = None,
) -> CaseResult:
    """Run the agent once on a private copy of ``case`` and return the outcome."""
    settings = dataclasses.replace(
        Settings.from_env(env, provider=spec.provider, model=spec.model), auto_approve=True
    )

    with tempfile.TemporaryDirectory(prefix="patchpilot-bench-") as scratch:
        project_copy = Path(scratch) / "project"
        shutil.copytree(case.project, project_copy)

        agent = CodingAgent(
            workspace=Workspace(project_copy),
            llm=client_factory(settings),
            verifier=HiddenTestVerifier(case, project_copy),
            ui=SilentUI(),
            settings=settings,
        )
        started = time.monotonic()
        solved = False
        error = None
        try:
            solved = agent.solve(case.report)
        except LLMError as exc:
            error = str(exc)

        return CaseResult(
            model=spec.label,
            case=case.id,
            run=run,
            solved=solved,
            attempts=agent.attempts_used,
            llm_calls=agent.usage.calls,
            tokens=agent.usage.total_tokens,
            seconds=round(time.monotonic() - started, 2),
            error=error,
        )


def run_benchmark(
    cases: Sequence[BenchmarkCase],
    models: Sequence[ModelSpec],
    *,
    repeat: int = 1,
    client_factory: ClientFactory = create_client,
    env: dict[str, str] | None = None,
    on_result: Callable[[CaseResult], None] = lambda _: None,
) -> list[CaseResult]:
    """Run every case against every model ``repeat`` times."""
    results: list[CaseResult] = []
    for spec in models:
        for case in cases:
            for run in range(1, repeat + 1):
                result = run_case(case, spec, client_factory, run, env)
                results.append(result)
                on_result(result)
    return results


def format_report(results: Sequence[CaseResult]) -> str:
    """Return a Markdown summary: one row per model, then one row per case."""
    models = list(dict.fromkeys(result.model for result in results))
    cases = list(dict.fromkeys(result.case for result in results))

    lines = [
        "| Model | Success rate | Avg tokens | Avg LLM calls | Avg seconds | API errors |",
        "| ----- | ------------ | ---------- | ------------- | ----------- | ---------- |",
    ]
    for model in models:
        rows = [result for result in results if result.model == model]
        solved = sum(result.solved for result in rows)
        lines.append(
            f"| `{model}` | {solved}/{len(rows)} ({solved / len(rows):.0%}) "
            f"| {_mean(result.tokens for result in rows):,.0f} "
            f"| {_mean(result.llm_calls for result in rows):.1f} "
            f"| {_mean(result.seconds for result in rows):.1f} "
            f"| {sum(result.error is not None for result in rows)} |"
        )

    lines += [
        "",
        "| Case | " + " | ".join(f"`{model}`" for model in models) + " |",
        "| ---- | " + " | ".join("---" for _ in models) + " |",
    ]
    for case in cases:
        cells = []
        for model in models:
            rows = [r for r in results if r.model == model and r.case == case]
            cells.append(f"{sum(r.solved for r in rows)}/{len(rows)}")
        lines.append(f"| `{case}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _mean(values: object) -> float:
    numbers = list(values)  # type: ignore[call-overload]
    return sum(numbers) / len(numbers) if numbers else 0.0


def write_results(results: Sequence[CaseResult], directory: Path) -> Path:
    """Save raw results as JSON and the report as Markdown, returning the report's path."""
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (directory / f"{stamp}.json").write_text(
        json.dumps([dataclasses.asdict(result) for result in results], indent=2) + "\n",
        encoding="utf-8",
    )
    report = directory / f"{stamp}.md"
    report.write_text(format_report(results), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchpilot-bench", description="Measure how well a model fixes seeded C++ bugs."
    )
    parser.add_argument(
        "--models",
        default="groq",
        help=f"comma-separated provider[:model] list; providers: {', '.join(PROVIDERS)}",
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_DIRECTORY)
    parser.add_argument("--only", help="comma-separated case ids to run")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case (default: 1)")
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS_DIRECTORY)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the benchmark. Exit codes: 0 finished, 2 invalid configuration."""
    args = build_parser().parse_args(argv)
    load_dotenv(find_dotenv(usecwd=True))

    try:
        if shutil.which("g++") is None:
            raise ConfigError("The benchmark needs g++ on your PATH.")
        if args.repeat < 1:
            raise ConfigError("--repeat must be at least 1.")

        cases = load_cases(args.cases)
        if args.only:
            wanted = {name.strip() for name in args.only.split(",")}
            cases = [case for case in cases if case.id in wanted]
            if not cases:
                raise ConfigError(f"None of the requested cases exist: {args.only}")
        models = [parse_model_spec(text) for text in args.models.split(",")]
        for spec in models:
            Settings.from_env(provider=spec.provider, model=spec.model)  # Fail before spending.

        def show(result: CaseResult) -> None:
            status = "ok  " if result.solved else "FAIL"
            detail = f"  ({result.error})" if result.error else ""
            print(
                f"{status} {result.model}  {result.case}  run {result.run}  "
                f"{result.tokens} tokens  {result.seconds}s{detail}",
                flush=True,
            )

        results = run_benchmark(cases, models, repeat=args.repeat, on_result=show)
    except PatchPilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    report = write_results(results, args.output)
    print("\n" + format_report(results))
    print(f"Saved to {report}")
    return 0
