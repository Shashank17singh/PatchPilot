"""Safe access to the project being repaired.

:class:`Workspace` is the only component that touches the filesystem on behalf of the
model. It confines every operation to one directory tree and to recognised code files,
so a misbehaving model cannot read or modify anything else (for example ``../../.env``).
"""

from __future__ import annotations

import os
from pathlib import Path

from .diffs import unified_diff
from .errors import WorkspaceError

CODE_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".h", ".hpp"})
COMPILABLE_EXTENSIONS = frozenset({".cc", ".cpp"})
IGNORED_DIRECTORIES = frozenset(
    {".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__", ".patchpilot-build"}
)

Snapshot = dict[Path, bytes]


class Workspace:
    """A directory of C/C++ sources that the agent is allowed to read and edit."""

    def __init__(self, root: Path) -> None:
        resolved = root.resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace '{root}' is not a directory.")
        self.root = resolved

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def code_files(self) -> list[Path]:
        """Return every code file in the workspace, sorted by path."""
        found: list[Path] = []
        for directory, subdirectories, filenames in os.walk(self.root):
            # Pruning in place stops os.walk from descending into ignored directories.
            subdirectories[:] = [d for d in subdirectories if d not in IGNORED_DIRECTORIES]
            for filename in filenames:
                path = Path(directory) / filename
                if path.suffix.lower() in CODE_EXTENSIONS and self._is_inside_root(path):
                    found.append(path)
        return sorted(found)

    def source_files(self) -> list[Path]:
        """Return the translation units that should be passed to the compiler."""
        return [p for p in self.code_files() if p.suffix.lower() in COMPILABLE_EXTENSIONS]

    def relative(self, path: Path) -> str:
        """Return ``path`` relative to the workspace root, using forward slashes."""
        return path.relative_to(self.root).as_posix()

    def resolve(self, relative_path: str) -> Path | None:
        """Map a model-supplied path to a real file, or ``None`` if it is not allowed.

        A path is allowed only if it stays inside the workspace, points at an existing
        code file, and does not sit within an ignored directory.
        """
        try:
            full = (self.root / relative_path).resolve()
        except (OSError, ValueError):  # e.g. an embedded NUL byte
            return None
        if not self._is_inside_root(full) or not full.is_file():
            return None
        if full.suffix.lower() not in CODE_EXTENSIONS:
            return None
        if any(part in IGNORED_DIRECTORIES for part in full.relative_to(self.root).parts[:-1]):
            return None
        return full

    # ------------------------------------------------------------------
    # Reading and writing
    # ------------------------------------------------------------------

    @staticmethod
    def read_lines(path: Path) -> list[str]:
        """Read a file as UTF-8, keeping each line's original ending (``\\n`` or ``\\r\\n``).

        Raises:
            WorkspaceError: if the file is not valid UTF-8 and so cannot be edited safely.
        """
        try:
            with open(path, encoding="utf-8", newline="") as handle:
                return handle.read().splitlines(keepends=True)
        except UnicodeDecodeError:
            raise WorkspaceError(f"{path.name} is not valid UTF-8 text.") from None

    @staticmethod
    def write_lines(path: Path, lines: list[str]) -> None:
        """Write ``lines`` back to ``path`` without altering line endings."""
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.writelines(lines)

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        """Capture the exact bytes of every code file, for use with :meth:`restore`."""
        return {path: path.read_bytes() for path in self.code_files()}

    @staticmethod
    def restore(snapshot: Snapshot) -> None:
        """Write a snapshot back to disk, rewriting only the files that changed."""
        for path, original in snapshot.items():
            if not path.exists() or path.read_bytes() != original:
                path.write_bytes(original)

    @staticmethod
    def changed_files(snapshot: Snapshot) -> list[Path]:
        """Return the files whose current contents differ from ``snapshot``."""
        return [
            path
            for path, original in snapshot.items()
            if not path.exists() or path.read_bytes() != original
        ]

    def diff(self, snapshot: Snapshot) -> str:
        """Return a patch that turns ``snapshot`` into the files as they are now."""
        patches = [
            unified_diff(
                self.relative(path),
                snapshot[path].decode("utf-8", errors="replace"),
                path.read_text(encoding="utf-8", errors="replace") if path.exists() else "",
            )
            for path in self.changed_files(snapshot)
        ]
        return "".join(patches)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_inside_root(self, path: Path) -> bool:
        """Return True if ``path`` (after resolving symlinks) is within the workspace."""
        try:
            return path.resolve().is_relative_to(self.root)
        except (OSError, ValueError):
            return False
