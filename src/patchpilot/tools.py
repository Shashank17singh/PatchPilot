"""Tools exposed to the language model.

Each tool is a plain method returning a string, because the model only understands text.
Failures are reported as readable messages rather than raised, so the model can correct
itself on the next step.

Tools:
    search_files    List code files related to a keyword.
    search_in_file  Show the lines of one file that contain a keyword, with line numbers.
    replace_line    Replace a single line of a file.
    replace_range   Replace a block of lines with any number of new lines.
    insert_lines    Add lines to a file.
    delete_lines    Remove a block of lines.

Every edit goes through one approval callback before anything is written.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .diffs import unified_diff
from .errors import WorkspaceError
from .workspace import Workspace

logger = logging.getLogger(__name__)

ToolFunction = Callable[..., str]
EditApprover = Callable[[str, str], bool]  # (relative path, unified diff) -> write it?

_NOT_FOUND = "File '{path}' not found. Use a path exactly as search_files returned it."
_REJECTED = (
    "The user rejected this edit, so {path} is unchanged. Do not repeat it. "
    "Try a different fix, or explain the problem in your final reply."
)
_SHIFT_WARNING = " Line numbers may have changed, so search again before the next edit."


class ToolKit:
    """Implements the model-facing tools on top of a :class:`Workspace`.

    ``approve`` is called with every proposed edit. Without one, edits are always written.
    """

    def __init__(self, workspace: Workspace, approve: EditApprover | None = None) -> None:
        self._workspace = workspace
        self._approve: EditApprover = approve or (lambda _path, _diff: True)
        self._registry: dict[str, ToolFunction] = {
            "search_files": self.search_files,
            "search_in_file": self.search_in_file,
            "replace_line": self.replace_line,
            "replace_range": self.replace_range,
            "insert_lines": self.insert_lines,
            "delete_lines": self.delete_lines,
        }

    def execute(self, name: str, arguments: Any) -> str:
        """Run the tool called ``name`` and return its textual result.

        Never raises: any failure is returned as a message for the model to act on.
        """
        function = self._registry.get(name)
        if function is None:
            return f"Unknown tool '{name}'. Available tools: {', '.join(self._registry)}"
        if not isinstance(arguments, dict):
            return "Tool call failed: arguments must be a JSON object."
        try:
            return function(**arguments)
        except Exception as exc:  # Tool boundary: report every failure back to the model.
            logger.warning("Tool %s failed", name, exc_info=True)
            return f"Tool call failed: {exc}"

    # ------------------------------------------------------------------
    # search_files
    # ------------------------------------------------------------------

    def search_files(self, keyword: str) -> str:
        """List code files whose name or content contains ``keyword`` (all files if empty)."""
        files = self._workspace.code_files()
        if not files:
            return "The workspace has no code files."

        needle = keyword.lower()
        matches: list[str] = []
        for path in files:
            relative = self._workspace.relative(path)
            if not needle:
                matches.append(relative)
                continue
            hits = self._count_occurrences(path, needle)
            if hits or needle in path.name.lower():
                matches.append(f"{relative}  ({hits} matches)")

        if not matches:
            # Fall back to the full listing so the model can continue from there.
            listing = "\n".join(self._workspace.relative(p) for p in files)
            return f"No file mentions '{keyword}'. All code files:\n{listing}"
        return "\n".join(matches)

    @staticmethod
    def _count_occurrences(path: Path, needle: str) -> int:
        try:
            return path.read_text(encoding="utf-8").lower().count(needle)
        except (OSError, UnicodeDecodeError):
            return 0

    # ------------------------------------------------------------------
    # search_in_file
    # ------------------------------------------------------------------

    def search_in_file(self, path: str, keyword: str) -> str:
        """Show lines of ``path`` containing ``keyword`` with line numbers (all if empty)."""
        full = self._workspace.resolve(path)
        if full is None:
            return _NOT_FOUND.format(path=path)

        try:
            lines = [line.rstrip("\r\n") for line in self._workspace.read_lines(full)]
        except WorkspaceError as exc:
            return str(exc)

        needle = keyword.lower()
        matches = [(n, line) for n, line in enumerate(lines, start=1) if needle in line.lower()]
        if not matches:
            return (
                f"'{keyword}' not found in {path}. "
                "Try another keyword, or an empty keyword to see the whole file."
            )
        return "\n".join(f"{n:>4}: {line}" for n, line in matches)

    # ------------------------------------------------------------------
    # replace_line
    # ------------------------------------------------------------------

    def replace_line(self, path: str, line_number: int, new_line: str) -> str:
        """Replace line ``line_number`` (1-based) of ``path`` with ``new_line``."""
        full = self._workspace.resolve(path)
        if full is None:
            return _NOT_FOUND.format(path=path)
        if "\n" in new_line or "\r" in new_line:
            return "new_line must be ONE line. Use replace_range to write several lines."

        number = _as_int(line_number)
        if number is None:
            return f"line_number must be an integer, got {line_number!r}."

        try:
            lines = self._workspace.read_lines(full)
        except WorkspaceError as exc:
            return str(exc)

        if not 1 <= number <= len(lines):
            return f"{path} has {len(lines)} lines, there is no line {number}."

        old = lines[number - 1]
        old_text = old.rstrip("\r\n")
        line_ending = old[len(old_text) :]

        # If the model forgot the indentation, inherit it from the line being replaced.
        if new_line.strip() and not new_line[0].isspace():
            indentation = old_text[: len(old_text) - len(old_text.lstrip())]
            new_line = indentation + new_line

        updated = [*lines[: number - 1], new_line + line_ending, *lines[number:]]
        return self._apply(
            full,
            lines,
            updated,
            f"Line {number} of {path} replaced.\n"
            f"before: {old_text.strip()}\n"
            f"after : {new_line.strip()}",
        )

    # ------------------------------------------------------------------
    # Multi-line edits
    # ------------------------------------------------------------------

    def replace_range(self, path: str, start_line: int, end_line: int, new_text: str) -> str:
        """Replace lines ``start_line`` to ``end_line`` (inclusive) with ``new_text``."""
        full = self._workspace.resolve(path)
        if full is None:
            return _NOT_FOUND.format(path=path)
        if not new_text.strip():
            return "new_text is empty. Use delete_lines to remove lines."

        try:
            lines = self._workspace.read_lines(full)
        except WorkspaceError as exc:
            return str(exc)

        span = _parse_range(path, start_line, end_line, len(lines))
        if isinstance(span, str):
            return span
        start, end = span

        updated = _splice(lines, start - 1, end, new_text)
        return self._apply(
            full, lines, updated, f"Lines {start}-{end} of {path} replaced.{_SHIFT_WARNING}"
        )

    def insert_lines(self, path: str, after_line: int, new_text: str) -> str:
        """Insert ``new_text`` after line ``after_line`` of ``path`` (0 inserts at the top)."""
        full = self._workspace.resolve(path)
        if full is None:
            return _NOT_FOUND.format(path=path)
        if not new_text:
            return "new_text is empty, so there is nothing to insert."

        number = _as_int(after_line)
        if number is None:
            return f"after_line must be an integer, got {after_line!r}."

        try:
            lines = self._workspace.read_lines(full)
        except WorkspaceError as exc:
            return str(exc)

        if not 0 <= number <= len(lines):
            return f"{path} has {len(lines)} lines, cannot insert after line {number}."

        updated = _splice(lines, number, number, new_text)
        return self._apply(
            full, lines, updated, f"Inserted after line {number} of {path}.{_SHIFT_WARNING}"
        )

    def delete_lines(self, path: str, start_line: int, end_line: int) -> str:
        """Delete lines ``start_line`` to ``end_line`` (inclusive) of ``path``."""
        full = self._workspace.resolve(path)
        if full is None:
            return _NOT_FOUND.format(path=path)

        try:
            lines = self._workspace.read_lines(full)
        except WorkspaceError as exc:
            return str(exc)

        span = _parse_range(path, start_line, end_line, len(lines))
        if isinstance(span, str):
            return span
        start, end = span

        updated = _splice(lines, start - 1, end, "")
        return self._apply(
            full, lines, updated, f"Deleted lines {start}-{end} of {path}.{_SHIFT_WARNING}"
        )

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def _apply(self, full: Path, before: list[str], after: list[str], success: str) -> str:
        """Write ``after`` to ``full`` if the change is approved, and report the outcome."""
        relative = self._workspace.relative(full)
        diff = unified_diff(relative, "".join(before), "".join(after))
        if not diff:
            return f"That edit does not change {relative}."
        if not self._approve(relative, diff):
            return _REJECTED.format(path=relative)
        self._workspace.write_lines(full, after)
        return success


def _as_int(value: object) -> int | None:
    """Convert a model-supplied line number. Models sometimes send "17" instead of 17."""
    try:
        return int(value)  # type: ignore[call-overload,no-any-return]
    except (TypeError, ValueError):
        return None


def _parse_range(
    path: str, start_line: object, end_line: object, total: int
) -> tuple[int, int] | str:
    """Validate a 1-based inclusive line range, returning it or a message for the model."""
    start, end = _as_int(start_line), _as_int(end_line)
    if start is None or end is None:
        return f"start_line and end_line must be integers, got {start_line!r} and {end_line!r}."
    if not 1 <= start <= end <= total:
        return f"{path} has {total} lines, lines {start}-{end} are not a valid range."
    return start, end


def _splice(lines: list[str], start: int, end: int, new_text: str) -> list[str]:
    """Replace ``lines[start:end]`` with ``new_text``, matching the file's line endings."""
    ending = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    ends_with_newline = not lines or lines[-1].endswith(("\n", "\r"))

    new_lines = new_text.replace("\r\n", "\n").removesuffix("\n").split("\n") if new_text else []
    updated = [*lines[:start], *(line + ending for line in new_lines), *lines[end:]]

    # The old last line may lack an ending but no longer be last, and vice versa.
    updated[:-1] = [line if line.endswith(("\n", "\r")) else line + ending for line in updated[:-1]]
    if updated and not ends_with_newline:
        updated[-1] = updated[-1].rstrip("\r\n")
    elif updated and not updated[-1].endswith(("\n", "\r")):
        updated[-1] += ending
    return updated


# ----------------------------------------------------------------------
# Tool definitions in the function-calling format expected by the API
# ----------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Find which code files are related to the bug. Returns file paths whose name "
                "or content contains the keyword. An empty keyword lists every code file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": (
                            "A function name, variable or word from the bug, e.g. 'factorial'."
                        ),
                    }
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_file",
            "description": (
                "Show the lines of one file that contain the keyword, with their line numbers. "
                "An empty keyword shows the whole file with line numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path exactly as search_files returned it.",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "Text to look for. Empty string = whole file.",
                    },
                },
                "required": ["path", "keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_line",
            "description": (
                "Replace one whole line of a file with a new line. "
                "Line numbers come from search_in_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path exactly as search_files returned it.",
                    },
                    "line_number": {
                        "type": "integer",
                        "description": "1-based number of the line to replace.",
                    },
                    "new_line": {
                        "type": "string",
                        "description": "The complete new line of code (one line only).",
                    },
                },
                "required": ["path", "line_number", "new_line"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_range",
            "description": (
                "Replace lines start_line to end_line (inclusive) with any number of new lines. "
                "new_text is written exactly as given, so include the indentation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path exactly as search_files returned it.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "1-based number of the first line to replace.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "1-based number of the last line to replace.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The replacement code. May contain several lines.",
                    },
                },
                "required": ["path", "start_line", "end_line", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "insert_lines",
            "description": (
                "Add new lines after line after_line, without changing existing lines. "
                "after_line 0 inserts at the top of the file. Use this to add a missing brace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path exactly as search_files returned it.",
                    },
                    "after_line": {
                        "type": "integer",
                        "description": "1-based number of the line to insert after.",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The code to add, including indentation.",
                    },
                },
                "required": ["path", "after_line", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_lines",
            "description": "Delete lines start_line to end_line (inclusive).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path exactly as search_files returned it.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "1-based number of the first line to delete.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "1-based number of the last line to delete.",
                    },
                },
                "required": ["path", "start_line", "end_line"],
            },
        },
    },
]
