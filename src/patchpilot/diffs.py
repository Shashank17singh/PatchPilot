"""Unified diffs, shown to the user and exported as patch files."""

from __future__ import annotations

import difflib
import re

_LINE = re.compile(r"[^\n]*\n|[^\n]+\Z")
_NO_NEWLINE = "\\ No newline at end of file\n"


def unified_diff(relative_path: str, before: str, after: str) -> str:
    """Return a diff of one file that ``git apply`` accepts, or ``""`` if nothing changed.

    Lines are split on ``\\n`` only, so a ``\\r\\n`` file keeps its carriage returns in the
    patch and still applies byte for byte.
    """
    diff = difflib.unified_diff(
        _LINE.findall(before),
        _LINE.findall(after),
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
    )
    body = "".join(line if line.endswith("\n") else line + "\n" + _NO_NEWLINE for line in diff)
    if not body:
        return ""
    return f"diff --git a/{relative_path} b/{relative_path}\n{body}"
