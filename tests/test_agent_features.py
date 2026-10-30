from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FakeVerifier, ScriptedLLM, ScriptedUI, final_reply, line_number_of, tool_reply
from patchpilot.agent import CodingAgent
from patchpilot.config import Settings
from patchpilot.errors import LLMError, RejectedRequestError
from patchpilot.llm import LLMReply, ToolCall
from patchpilot.verifier import VerificationResult
from patchpilot.workspace import Workspace

FAILING = VerificationResult(True, "1 test failed", tests_passed=False)
PASSING = VerificationResult(True, "all tests passed", tests_passed=True)
NO_VERDICT = VerificationResult(True, "program output")


def agent_for(
    workspace: Workspace,
    llm: ScriptedLLM,
    ui: ScriptedUI,
    verifier: FakeVerifier,
    *,
    auto_approve: bool = True,
) -> CodingAgent:
    return CodingAgent(
        workspace=workspace,
        llm=llm,
        verifier=verifier,
        ui=ui,
        settings=Settings(api_key="k", max_attempts=2, max_steps=4, auto_approve=auto_approve),
    )


def edit(project: Path, replacement: str = "return a * b;") -> LLMReply:
    number = line_number_of(project / "main.cpp", "return a + b;")
    return tool_reply("replace_line", path="main.cpp", line_number=number, new_line=replacement)


class TestConfirmation:
    def test_edits_are_written_only_after_the_user_approves(
        self, workspace: Workspace, project: Path
    ) -> None:
        llm = ScriptedLLM([edit(project), final_reply()])
        ui = ScriptedUI(["y", "y"])
        agent = agent_for(workspace, llm, ui, FakeVerifier([NO_VERDICT]), auto_approve=False)

        assert agent.solve("bug")

        assert ui.questions[0] == "Apply this edit? (y/n): "
        assert "return a * b;" in (project / "main.cpp").read_text()
        assert "-    return a + b;" in ui.transcript
        assert "+    return a * b;" in ui.transcript

    def test_a_rejected_edit_is_not_written_and_the_model_is_told(
        self, workspace: Workspace, project: Path
    ) -> None:
        llm = ScriptedLLM([edit(project), final_reply("Stopped.")])
        ui = ScriptedUI(["n", "y"])

        agent_for(workspace, llm, ui, FakeVerifier([NO_VERDICT]), auto_approve=False).solve("bug")

        assert "return a + b;" in (project / "main.cpp").read_text()
        assert "rejected this edit" in llm.conversations[-1][-1]["content"]

    @pytest.mark.parametrize("answer", ["", "maybe", "no"])
    def test_anything_but_yes_refuses_the_edit(
        self, workspace: Workspace, project: Path, answer: str
    ) -> None:
        llm = ScriptedLLM([edit(project), final_reply()])
        ui = ScriptedUI([answer, "y"])

        agent_for(workspace, llm, ui, FakeVerifier([NO_VERDICT]), auto_approve=False).solve("bug")

        assert "return a + b;" in (project / "main.cpp").read_text()

    def test_yes_flag_skips_the_question_but_still_shows_the_diff(
        self, workspace: Workspace, project: Path
    ) -> None:
        llm = ScriptedLLM([edit(project), final_reply()])
        ui = ScriptedUI(["y"])

        agent_for(workspace, llm, ui, FakeVerifier([NO_VERDICT])).solve("bug")

        assert ui.questions == ["\nIs the bug fixed? (y/n): "]
        assert "+    return a * b;" in ui.transcript

    def test_ctrl_c_at_the_prompt_restores_earlier_edits(
        self, workspace: Workspace, project: Path
    ) -> None:
        original = (project / "main.cpp").read_bytes()
        number = line_number_of(project / "main.cpp", "int result = 1;")
        llm = ScriptedLLM(
            [
                edit(project),
                tool_reply("replace_line", path="main.cpp", line_number=number, new_line="int r;"),
            ]
        )
        ui = ScriptedUI(["y", KeyboardInterrupt()])

        with pytest.raises(KeyboardInterrupt):
            agent_for(workspace, llm, ui, FakeVerifier([NO_VERDICT]), auto_approve=False).solve(
                "bug"
            )

        assert (project / "main.cpp").read_bytes() == original


class TestAutomatedVerification:
    def test_passing_tests_accept_the_fix_without_asking(
        self, workspace: Workspace, project: Path
    ) -> None:
        llm = ScriptedLLM([edit(project), final_reply()])
        ui = ScriptedUI()  # Any prompt fails the test.
        verifier = FakeVerifier([FAILING, PASSING], automated=True)

        assert agent_for(workspace, llm, ui, verifier).solve("bug")

        assert ui.questions == []
        assert "Tests: PASSED" in ui.transcript

    def test_the_baseline_failure_is_shown_to_the_model(
        self, workspace: Workspace, project: Path
    ) -> None:
        llm = ScriptedLLM([edit(project), final_reply()])
        verifier = FakeVerifier([FAILING, PASSING], automated=True)

        agent_for(workspace, llm, ScriptedUI(), verifier).solve("bug")

        assert "1 test failed" in llm.conversations[0][2]["content"]

    def test_failing_tests_feed_back_without_asking(
        self, workspace: Workspace, project: Path
    ) -> None:
        llm = ScriptedLLM([edit(project), final_reply(), final_reply("Second go.")])
        ui = ScriptedUI()
        verifier = FakeVerifier([FAILING, FAILING, PASSING], automated=True)

        assert agent_for(workspace, llm, ui, verifier).solve("bug")

        assert "BUG FIXED in attempt 2" in ui.transcript
        assert "The tests still fail" in llm.conversations[-1][-1]["content"]

    def test_a_run_that_never_passes_is_rolled_back(
        self, workspace: Workspace, project: Path
    ) -> None:
        original = (project / "main.cpp").read_bytes()
        llm = ScriptedLLM([edit(project), final_reply(), final_reply()])
        verifier = FakeVerifier([FAILING], automated=True)

        solved = agent_for(workspace, llm, ScriptedUI(), verifier).solve("bug")

        assert not solved
        assert (project / "main.cpp").read_bytes() == original

    def test_tests_that_already_pass_cannot_prove_a_fix(
        self, workspace: Workspace, project: Path
    ) -> None:
        llm = ScriptedLLM([edit(project), final_reply()])
        ui = ScriptedUI(["y"])
        verifier = FakeVerifier([PASSING], automated=True)

        assert agent_for(workspace, llm, ui, verifier).solve("bug")

        assert "the tests already pass" in ui.transcript
        assert ui.questions == ["\nIs the bug fixed? (y/n): "]

    def test_a_broken_build_at_baseline_is_not_treated_as_a_pass(
        self, workspace: Workspace, project: Path
    ) -> None:
        broken = VerificationResult(False, "syntax error")
        llm = ScriptedLLM([edit(project), final_reply()])
        ui = ScriptedUI()
        verifier = FakeVerifier([broken, PASSING], automated=True)

        assert agent_for(workspace, llm, ui, verifier).solve("bug")
        assert ui.questions == []

    def test_no_baseline_runs_without_automated_tests(
        self, workspace: Workspace, project: Path
    ) -> None:
        llm = ScriptedLLM([edit(project), final_reply()])
        verifier = FakeVerifier([VerificationResult(True, "output")])

        agent_for(workspace, llm, ScriptedUI(["y"]), verifier).solve("bug")

        assert verifier.calls == 1


class TestProviderDetails:
    def test_gemini_thought_signatures_are_sent_back_unchanged(self, workspace: Workspace) -> None:
        signature = {"google": {"thought_signature": "opaque=="}}
        call = ToolCall("call_1", "search_files", '{"keyword": "x"}', extra_content=signature)
        llm = ScriptedLLM([LLMReply("", (call,), 5), final_reply()])
        verifier = FakeVerifier([VerificationResult(True, "output")])

        agent_for(workspace, llm, ScriptedUI(["y"]), verifier).solve("bug")

        assistant = next(m for m in llm.conversations[-1] if m["role"] == "assistant")
        assert assistant["tool_calls"][0]["extra_content"] == signature

    def test_tool_calls_without_extra_data_stay_plain(self, workspace: Workspace) -> None:
        llm = ScriptedLLM([tool_reply("search_files", keyword="x"), final_reply()])
        verifier = FakeVerifier([VerificationResult(True, "output")])

        agent_for(workspace, llm, ScriptedUI(["y"]), verifier).solve("bug")

        assistant = next(m for m in llm.conversations[-1] if m["role"] == "assistant")
        assert "extra_content" not in assistant["tool_calls"][0]

    def test_openrouter_reasoning_details_are_replayed(self, workspace: Workspace) -> None:
        details = [{"type": "reasoning.encrypted", "data": "abc"}]
        call = ToolCall("call_1", "search_files", '{"keyword": "x"}')
        reply = LLMReply("", (call,), 5, {"reasoning_details": details})
        llm = ScriptedLLM([reply, final_reply()])
        verifier = FakeVerifier([VerificationResult(True, "output")])

        agent_for(workspace, llm, ScriptedUI(["y"]), verifier).solve("bug")

        assistant = next(m for m in llm.conversations[-1] if m["role"] == "assistant")
        assert assistant["reasoning_details"] == details

    def test_a_permanently_rejected_request_stops_quickly(
        self, workspace: Workspace, project: Path
    ) -> None:
        original = (project / "main.cpp").read_bytes()
        rejected = RejectedRequestError("model does not support tools")
        llm = ScriptedLLM([edit(project), rejected, rejected, rejected])
        verifier = FakeVerifier([VerificationResult(True, "output")])

        with pytest.raises(LLMError, match="does not support tools"):
            agent_for(workspace, llm, ScriptedUI(), verifier).solve("bug")

        assert (project / "main.cpp").read_bytes() == original
