"""Exception hierarchy for PatchPilot.

Every error that is expected to reach the user derives from :class:`PatchPilotError`
and carries the process exit code the CLI should use when it is raised.
"""

from __future__ import annotations


class PatchPilotError(Exception):
    """Base class for all errors raised deliberately by PatchPilot."""

    exit_code = 1


class ConfigError(PatchPilotError):
    """The runtime configuration is missing or invalid."""

    exit_code = 2


class WorkspaceError(PatchPilotError):
    """The target project directory cannot be used."""

    exit_code = 2


class GitError(PatchPilotError):
    """A git operation requested by the user failed."""


class LLMError(PatchPilotError):
    """The language model API failed in a way the agent cannot recover from."""


class RejectedRequestError(LLMError):
    """The API rejected a request, typically because the model emitted a malformed tool call.

    Unlike other :class:`LLMError` subclasses this is recoverable: the agent simply asks again.
    """
