"""Git integration: commit a confirmed fix without touching unrelated work.

PatchPilot only ever commits locally. It never pushes.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from .errors import GitError


def is_repository(root: Path) -> bool:
    """Return True if ``root`` is inside a git work tree."""
    try:
        return _git(root, "rev-parse", "--is-inside-work-tree").strip() == "true"
    except GitError:
        return False


def uncommitted_files(root: Path) -> set[Path]:
    """Return every file with uncommitted changes, including untracked files."""
    top_level = Path(_git(root, "rev-parse", "--show-toplevel").strip())
    entries = iter(_git(root, "status", "--porcelain", "-z").split("\0"))

    dirty: set[Path] = set()
    for entry in entries:
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        dirty.add((top_level / path).resolve())
        if "R" in status or "C" in status:
            next(entries, None)  # A rename is followed by its old path.
    return dirty


def commit_files(root: Path, files: Sequence[Path], message: str) -> str:
    """Commit only ``files`` and return the new commit's short hash.

    Naming the paths means anything else the user has staged stays staged and uncommitted.

    Raises:
        GitError: if git refuses the commit, for example because a hook failed.
    """
    _git(root, "commit", "--quiet", "-m", message, "--", *map(str, files))
    return _git(root, "rev-parse", "--short", "HEAD").strip()


def commit_message(bug: str, summary: str) -> str:
    """Build a commit message from the bug report and the model's summary."""
    subject = " ".join(bug.split())
    if len(subject) > 60:
        subject = subject[:57].rstrip() + "..."
    body = summary.strip()
    return f"fix: {subject}\n\n{body}" if body else f"fix: {subject}"


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"Could not run git: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise GitError(f"git {arguments[0]} failed: {detail}")
    return completed.stdout
