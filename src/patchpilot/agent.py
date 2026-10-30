"""The agent loop.

Flow:
    1. The user describes a bug in plain English.
    2. The model searches and edits code through its tools until it reports it is done.
       Each edit is shown as a diff and needs the user's approval, unless ``--yes`` was given.
    3. The project is built and checked. Automated tests decide the outcome when there are
       any; otherwise the result is shown to the user, who decides.
    4. A build failure, failing tests or a "not fixed" answer feeds into the next attempt.
    5. If no attempt succeeds, every file is restored to its original state.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from . import prompts
from .config import Settings
from .errors import LLMError, RejectedRequestError
from .llm import LLMClient, LLMReply, Message, ToolCall
from .tools import TOOL_SCHEMAS, ToolKit
from .ui import CONFIRM_FIX_PROMPT, UserInterface
from .verifier import VerificationResult, Verifier
from .workspace import Snapshot, Workspace

logger = logging.getLogger(__name__)

_RESULT_INDENT = " " * 11
# A permanent 400, such as a model without tool support, must not burn every step and attempt.
_MAX_CONSECUTIVE_REJECTIONS = 3


@dataclass(slots=True)
class UsageTracker:
    """Running totals of model usage for one agent run."""

    calls: int = 0
    total_tokens: int = 0

    def record(self, tokens: int) -> None:
        self.calls += 1
        self.total_tokens += tokens


class CodingAgent:
    """Fixes a described bug by letting a language model drive a small set of tools."""

    def __init__(
        self,
        *,
        workspace: Workspace,
        llm: LLMClient,
        verifier: Verifier,
        ui: UserInterface,
        settings: Settings,
    ) -> None:
        self._workspace = workspace
        self._llm = llm
        self._verifier = verifier
        self._ui = ui
        self._settings = settings
        self._tools = ToolKit(workspace, self._review_edit)
        self._trust_tests = True
        self.usage = UsageTracker()
        self.attempts_used = 0
        self.summary = ""
        self.original_files: Snapshot = {}

    def solve(self, bug_description: str) -> bool:
        """Try to fix ``bug_description``.

        Returns:
            ``True`` if the user confirmed the fix. ``False`` if every attempt failed,
            in which case all files have been restored to their original contents.

        If the run is interrupted or fails unexpectedly, the files are restored before
        the exception propagates, so the project is never left half-edited.
        """
        snapshot = self.original_files = self._workspace.snapshot()
        messages: list[Message] = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "user", "content": prompts.bug_report(bug_description)},
        ]

        try:
            self._check_tests_before_editing(messages)
            for attempt in range(1, self._settings.max_attempts + 1):
                self.attempts_used = attempt
                self._ui.header(f"ATTEMPT {attempt} of {self._settings.max_attempts}")
                if self._run_attempt_cycle(messages):
                    self._ui.header(f"BUG FIXED in attempt {attempt}")
                    return True
        except BaseException:
            self._rollback(snapshot)
            raise

        self._ui.header(
            f"Sorry, I could not solve this bug in {self._settings.max_attempts} attempts."
        )
        self._rollback(snapshot)
        return False

    def _check_tests_before_editing(self, messages: list[Message]) -> None:
        """Run the tests once on the untouched code.

        Tests that already pass cannot show that a change fixed anything, so the agent stops
        trusting them and asks the user. Tests that fail give the model something concrete.
        """
        if not self._verifier.automated:
            return

        self._ui.header(f"BASELINE: checking the code with {self._verifier.name}", char="-")
        baseline = self._verifier.verify(self._workspace.source_files())
        if baseline.tests_passed:
            self._trust_tests = False
            self._ui.say("Warning: the tests already pass, so they cannot prove a fix.")
            self._ui.say("You will be asked to confirm the result yourself.")
        elif baseline.tests_passed is False:
            self._ui.say("Tests: FAILED, as expected before the fix.")
            messages.append({"role": "user", "content": prompts.failing_tests(baseline.output)})

    # ------------------------------------------------------------------
    # One attempt: edit, verify, ask the user
    # ------------------------------------------------------------------

    def _run_attempt_cycle(self, messages: list[Message]) -> bool:
        """Run one edit-verify-confirm cycle. Returns True once the user accepts the fix."""
        summary = self._run_tool_loop(messages)
        if summary is None:
            self._ui.say(f"\nLLM used all {self._settings.max_steps} steps without finishing.")
            messages.append(
                {"role": "user", "content": prompts.step_limit_reached(self._settings.max_steps)}
            )
            return False

        self.summary = summary
        self._ui.say("\nLLM summary:")
        self._ui.block(summary)

        self._ui.header(
            f"VERIFY: compiling and running the code with {self._verifier.name}", char="-"
        )
        result = self._verifier.verify(self._workspace.source_files())

        if not result.compiled:
            self._ui.say("Compile: FAILED")
            self._ui.block(result.output, max_lines=20)
            messages.append({"role": "user", "content": prompts.compile_failed(result.output)})
            return False

        self._ui.say("Compile: SKIPPED" if result.skipped else "Compile: OK")
        self._ui.say("Program output:")
        self._ui.block(result.output, max_lines=30)

        verdict = self._test_verdict(result, messages)
        if verdict is not None:
            return verdict

        if self._ui.ask(CONFIRM_FIX_PROMPT).lower().startswith("y"):
            return True

        comment = self._ui.ask("What is still wrong? (Enter to skip): ")
        messages.append({"role": "user", "content": prompts.bug_not_fixed(result.output, comment)})
        return False

    def _test_verdict(self, result: VerificationResult, messages: list[Message]) -> bool | None:
        """Decide the attempt from its tests. ``None`` means the user has to decide."""
        if result.tests_passed is None:
            return None
        if not result.tests_passed:
            self._ui.say("Tests: FAILED")
            messages.append({"role": "user", "content": prompts.tests_failed(result.output)})
            return False

        self._ui.say("Tests: PASSED")
        return True if self._trust_tests else None

    # ------------------------------------------------------------------
    # The model <-> tools loop
    # ------------------------------------------------------------------

    def _run_tool_loop(self, messages: list[Message]) -> str | None:
        """Let the model call tools until it stops asking for them.

        Returns the model's closing summary, or ``None`` if it used every allowed step
        without finishing.
        """
        rejections = 0
        for step in range(1, self._settings.max_steps + 1):
            self._ui.say(f"\n[step {step}] asking the LLM what to do next ...")

            try:
                reply = self._llm.complete(messages, TOOL_SCHEMAS)
            except RejectedRequestError as exc:
                logger.warning("Request rejected by the API: %s", exc)
                rejections += 1
                if rejections >= _MAX_CONSECUTIVE_REJECTIONS:
                    raise LLMError(f"The API rejected every request: {exc}") from exc
                self._ui.say(
                    f"[step {step}] The API rejected the request "
                    "(likely a malformed tool call), asking again"
                )
                continue

            rejections = 0
            self.usage.record(reply.total_tokens)

            if not reply.tool_calls:
                messages.append(
                    {"role": "assistant", "content": reply.content, **reply.provider_fields}
                )
                self._ui.say(f"[step {step}] LLM finished (no tool call)")
                return reply.content or "(no summary)"

            if reply.content:
                self._ui.say(f"[step {step}] LLM says: {reply.content.strip()}")

            messages.append(self._assistant_tool_message(reply))

            for call in reply.tool_calls:
                self._ui.say(f"[step {step}] TOOL CALLED -> {call.name}")
                result = self._execute(call)
                self._ui.say(f"{_RESULT_INDENT}result:")
                self._ui.block(result)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        return None

    @staticmethod
    def _assistant_tool_message(reply: LLMReply) -> Message:
        """Rebuild the model's turn for the history, keeping any provider-specific data."""
        tool_calls: list[dict[str, object]] = []
        for call in reply.tool_calls:
            entry: dict[str, object] = {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            if call.extra_content:
                entry["extra_content"] = call.extra_content
            tool_calls.append(entry)
        return {
            "role": "assistant",
            "content": reply.content,
            "tool_calls": tool_calls,
            **reply.provider_fields,
        }

    def _review_edit(self, path: str, diff: str) -> bool:
        """Show a proposed edit in full and return whether it may be written."""
        self._ui.say(f"{_RESULT_INDENT}proposed edit to {path}:")
        self._ui.block(diff, max_lines=len(diff.splitlines()))
        if self._settings.auto_approve:
            return True
        # An empty answer, including closed input, must never write to the project.
        return self._ui.ask("Apply this edit? (y/n): ").lower().startswith("y")

    def _execute(self, call: ToolCall) -> str:
        """Parse a tool call's arguments and run it, returning text for the model."""
        try:
            arguments = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return f"Tool call failed: arguments are not valid JSON ({exc})."
        self._ui.say(f"{_RESULT_INDENT}args: {arguments}")
        return self._tools.execute(call.name, arguments)

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def _rollback(self, snapshot: Snapshot) -> None:
        self._workspace.restore(snapshot)
        self._ui.say("Your files are restored to how they were before the agent started.")
