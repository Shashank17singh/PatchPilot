"""User-facing input and output.

The agent talks to the person running it exclusively through :class:`UserInterface`,
which keeps the agent logic free of ``print``/``input`` calls and makes it testable.
"""

from __future__ import annotations

from typing import Protocol

DEFAULT_PREVIEW_LINES = 15
CONFIRM_FIX_PROMPT = "\nIs the bug fixed? (y/n): "
_BLOCK_INDENT = " " * 11


class UserInterface(Protocol):
    """Everything the agent needs in order to communicate with the user."""

    def header(self, title: str, char: str = "=") -> None:
        """Print a section banner."""

    def say(self, message: str = "") -> None:
        """Print a single message."""

    def block(self, text: str, max_lines: int = DEFAULT_PREVIEW_LINES) -> None:
        """Print a multi-line block, truncated to ``max_lines`` lines."""

    def ask(self, question: str) -> str:
        """Prompt the user and return the stripped answer ('' if input is closed)."""


class TerminalUI:
    """:class:`UserInterface` backed by standard input and output."""

    def header(self, title: str, char: str = "=") -> None:
        print("\n" + char * 60)
        print(title)
        print(char * 60)

    def say(self, message: str = "") -> None:
        print(message)

    def block(self, text: str, max_lines: int = DEFAULT_PREVIEW_LINES) -> None:
        lines = text.splitlines() or [""]
        for line in lines[:max_lines]:
            print(f"{_BLOCK_INDENT}| {line}")
        hidden = len(lines) - max_lines
        if hidden > 0:
            print(f"{_BLOCK_INDENT}| ... ({hidden} more lines not shown)")

    def ask(self, question: str) -> str:
        try:
            return input(question).strip()
        except EOFError:
            return ""
