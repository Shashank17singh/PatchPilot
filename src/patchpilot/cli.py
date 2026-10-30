"""Command-line interface."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from . import __version__, gitops
from .agent import CodingAgent
from .config import BUILD_SYSTEMS, PROVIDERS, Settings
from .errors import ConfigError, GitError, PatchPilotError
from .llm import create_client
from .ui import TerminalUI
from .verifier import build_verifier
from .workspace import Workspace

EXIT_INTERRUPTED = 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchpilot",
        description="Describe a bug in plain English and let an LLM agent fix your C++ code.",
    )
    parser.add_argument(
        "workspace",
        nargs="?",
        type=Path,
        default=Path("."),
        help="directory containing the C/C++ project (default: current directory)",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="apply edits without asking for each one"
    )
    parser.add_argument(
        "--provider", choices=sorted(PROVIDERS), help="LLM provider (default: groq)"
    )
    parser.add_argument("--model", help="model identifier (default: the provider's default)")
    parser.add_argument(
        "--build",
        choices=BUILD_SYSTEMS,
        default="auto",
        help="how to build the project (default: detect CMake or Make, otherwise g++)",
    )
    parser.add_argument(
        "--test-cmd",
        metavar="CMD",
        help="shell command run in the project after building; exit code 0 means fixed",
    )
    parser.add_argument(
        "--patch-file", type=Path, metavar="PATH", help="write the confirmed fix to a patch file"
    )
    parser.add_argument(
        "--commit", action="store_true", help="commit the confirmed fix to the local git repo"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run PatchPilot and return the process exit code.

    Exit codes: 0 bug fixed (or nothing to do), 1 bug not fixed or a runtime failure,
    2 invalid configuration or workspace, 130 interrupted.
    """
    args = build_parser().parse_args(argv)

    # Search for .env from the current directory upwards, not from this package's location.
    # This must run first so that settings in .env (such as the log level) take effect.
    load_dotenv(find_dotenv(usecwd=True))
    _configure_logging()
    _configure_stdout()

    ui = TerminalUI()
    try:
        return _run(args, ui)
    except KeyboardInterrupt:
        ui.say("\nInterrupted.")
        return EXIT_INTERRUPTED
    except PatchPilotError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code


def _run(args: argparse.Namespace, ui: TerminalUI) -> int:
    # Validate configuration up front so a missing API key fails before the user types anything.
    settings = dataclasses.replace(
        Settings.from_env(provider=args.provider, model=args.model),
        auto_approve=args.yes,
        build_system=args.build,
        test_command=args.test_cmd,
    )
    workspace = Workspace(args.workspace)
    verifier = build_verifier(workspace.root, settings)
    if args.commit and not gitops.is_repository(workspace.root):
        raise ConfigError(f"--commit needs a git repository, but {workspace.root} is not one.")
    files = workspace.code_files()

    ui.header("PATCHPILOT")
    ui.say(f"Workspace: {workspace.root}")
    ui.say(f"Files    : {', '.join(workspace.relative(f) for f in files) or '(none)'}")
    ui.say(f"Model    : {settings.model} ({settings.provider})")
    if not files:
        print("error: no C/C++ source files found in the workspace.", file=sys.stderr)
        return 1

    bug = ui.ask("\nDescribe the bug in simple English: ")
    if not bug:
        ui.say("No bug given. Bye!")
        return 0

    # Recorded now, because after the fix the user's edits and the model's look the same.
    dirty_before = gitops.uncommitted_files(workspace.root) if args.commit else set()

    agent = CodingAgent(
        workspace=workspace,
        llm=create_client(settings),
        verifier=verifier,
        ui=ui,
        settings=settings,
    )
    solved = agent.solve(bug)

    if solved:
        _publish_fix(args, agent, workspace, ui, bug, dirty_before)
    ui.say(f"\nLLM calls: {agent.usage.calls}   tokens used: {agent.usage.total_tokens}")
    return 0 if solved else 1


def _publish_fix(
    args: argparse.Namespace,
    agent: CodingAgent,
    workspace: Workspace,
    ui: TerminalUI,
    bug: str,
    dirty_before: set[Path],
) -> None:
    """Export or commit a confirmed fix.

    A failure here is reported but not fatal: the fix is already in the working tree.
    """
    if args.patch_file:
        try:
            with open(args.patch_file, "w", encoding="utf-8", newline="") as handle:
                handle.write(workspace.diff(agent.original_files))
            ui.say(f"Patch written to {args.patch_file}")
        except OSError as exc:
            ui.say(f"Warning: could not write {args.patch_file}: {exc}")

    if args.commit:
        changed = workspace.changed_files(agent.original_files)
        overlapping = dirty_before.intersection(path.resolve() for path in changed)
        if not changed:
            ui.say("Nothing to commit: the fix changed no files.")
        elif overlapping:
            names = ", ".join(sorted(workspace.relative(path) for path in overlapping))
            ui.say(f"Not committing: {names} had uncommitted changes that would be included.")
        else:
            try:
                message = gitops.commit_message(bug, agent.summary)
                revision = gitops.commit_files(workspace.root, changed, message)
                ui.say(f"Committed the fix as {revision}")
            except GitError as exc:
                ui.say(f"Warning: the fix is in place but was not committed. {exc}")


def _configure_logging() -> None:
    """Send diagnostics to stderr. Set PATCHPILOT_LOG_LEVEL=DEBUG for detail."""
    level = getattr(logging, os.environ.get("PATCHPILOT_LOG_LEVEL", "WARNING").upper(), None)
    logging.basicConfig(
        level=level if isinstance(level, int) else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _configure_stdout() -> None:
    """Keep model output containing unusual Unicode from crashing Windows consoles."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")
