from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from conftest import FakeVerifier, ScriptedLLM, ScriptedUI, final_reply, line_number_of, tool_reply
from patchpilot.agent import CodingAgent
from patchpilot.config import Settings
from patchpilot.errors import LLMError, RejectedRequestError
from patchpilot.llm import LLMReply, ToolCall
from patchpilot.verifier import CppVerifier, VerificationResult
from patchpilot.workspace import Workspace

OK = VerificationResult(compiled=True, output="add(2, 3) = 5")
BROKEN_BUILD = VerificationResult(compiled=False, output="main.cpp:5: error: expected ';'")


def make_agent(
    workspace: Workspace,
    settings: Settings,
    llm: ScriptedLLM,
    ui: ScriptedUI,
    verifier: object | None = None,
) -> CodingAgent:
    return CodingAgent(
        workspace=workspace,
        llm=llm,
        verifier=verifier or FakeVerifier([OK]),  # type: ignore[arg-type]
        ui=ui,
        settings=settings,
    )


def edit_script(
    project: Path, replacement: str = "return a * b;", target: str = "return a + b;"
) -> list[LLMReply]:
    """Model turns that locate the `add` function, edit `target`, and report back."""
    number = line_number_of(project / "main.cpp", target)
    return [
        tool_reply("search_files", keyword="add"),
        tool_reply("search_in_file", path="main.cpp", keyword="return a"),
        tool_reply("replace_line", path="main.cpp", line_number=number, new_line=replacement),
        final_reply("Changed the operator."),
    ]


class TestSuccess:
    def test_fixes_the_bug_when_the_user_confirms(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        llm = ScriptedLLM(edit_script(project, "return a - b;"))
        ui = ScriptedUI(["y"])

        solved = make_agent(workspace, settings, llm, ui).solve("add is wrong")

        assert solved
        assert "return a - b;" in (project / "main.cpp").read_text()
        assert "BUG FIXED in attempt 1" in ui.transcript
        assert "Changed the operator." in ui.transcript

    def test_shows_each_tool_call_to_the_user(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        ui = ScriptedUI(["y"])

        make_agent(workspace, settings, ScriptedLLM(edit_script(project)), ui).solve("bug")

        for name in ("search_files", "search_in_file", "replace_line"):
            assert f"TOOL CALLED -> {name}" in ui.transcript
        assert "[step 4] LLM finished (no tool call)" in ui.transcript

    def test_tracks_model_usage(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        agent = make_agent(
            workspace, settings, ScriptedLLM(edit_script(project)), ScriptedUI(["y"])
        )

        agent.solve("bug")

        assert agent.usage.calls == 4
        assert agent.usage.total_tokens == 40

    def test_sends_tool_results_back_to_the_model(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        llm = ScriptedLLM(edit_script(project))

        make_agent(workspace, settings, llm, ScriptedUI(["y"])).solve("add is wrong")

        last_conversation = llm.conversations[-1]
        roles = [m["role"] for m in last_conversation]
        assert roles[:2] == ["system", "user"]
        assert last_conversation[1]["content"] == "Bug report:\nadd is wrong"
        assert roles.count("tool") == 3
        assert roles.count("assistant") == 3
        tool_ids = {m["tool_call_id"] for m in last_conversation if m["role"] == "tool"}
        assert tool_ids == {"call_1"}

    def test_a_reply_without_text_is_given_a_placeholder_summary(
        self, workspace: Workspace, settings: Settings
    ) -> None:
        ui = ScriptedUI(["y"])

        make_agent(workspace, settings, ScriptedLLM([final_reply("")]), ui).solve("bug")

        assert "(no summary)" in ui.transcript

    def test_reports_when_verification_was_skipped(
        self, workspace: Workspace, settings: Settings
    ) -> None:
        skipped = VerificationResult(True, "(g++ not found, skipped compiling.)", skipped=True)
        ui = ScriptedUI(["y"])

        make_agent(
            workspace, settings, ScriptedLLM([final_reply()]), ui, FakeVerifier([skipped])
        ).solve("bug")

        assert "Compile: SKIPPED" in ui.transcript


class TestFeedbackLoop:
    def test_compile_errors_are_fed_back_into_the_next_attempt(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        llm = ScriptedLLM([*edit_script(project), final_reply("Repaired.")])
        ui = ScriptedUI(["y"])
        verifier = FakeVerifier([BROKEN_BUILD, OK])

        solved = make_agent(workspace, settings, llm, ui, verifier).solve("bug")

        assert solved
        assert "BUG FIXED in attempt 2" in ui.transcript
        assert "Compile: FAILED" in ui.transcript
        retry_messages = llm.conversations[-1]
        feedback = retry_messages[-1]
        assert feedback["role"] == "user"
        assert "does not compile" in feedback["content"]
        assert "expected ';'" in feedback["content"]

    def test_user_comments_are_fed_back_when_the_bug_is_not_fixed(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        llm = ScriptedLLM([*edit_script(project), final_reply("Second try.")])
        ui = ScriptedUI(["n", "still returns 6", "y"])

        solved = make_agent(workspace, settings, llm, ui).solve("bug")

        assert solved
        feedback = llm.conversations[-1][-1]["content"]
        assert "The bug is NOT fixed yet." in feedback
        assert "User says: still returns 6" in feedback
        assert "add(2, 3) = 5" in feedback

    def test_a_blank_comment_is_described_as_no_details(
        self, workspace: Workspace, settings: Settings
    ) -> None:
        llm = ScriptedLLM([final_reply(), final_reply()])

        make_agent(workspace, settings, llm, ScriptedUI(["n", "", "y"])).solve("bug")

        assert "User says: (no details)" in llm.conversations[-1][-1]["content"]

    def test_running_out_of_steps_nudges_the_model_and_retries(
        self, workspace: Workspace, settings: Settings
    ) -> None:
        # settings.max_steps is 4: the first attempt never finishes, the second does.
        stalling = [tool_reply("search_files", keyword="x") for _ in range(4)]
        llm = ScriptedLLM([*stalling, final_reply("Done.")])
        ui = ScriptedUI(["y"])

        solved = make_agent(workspace, settings, llm, ui).solve("bug")

        assert solved
        assert "LLM used all 4 steps without finishing." in ui.transcript
        assert "You used all 4 steps without finishing" in llm.conversations[-1][-1]["content"]


class TestFailureAndRollback:
    def test_restores_every_file_when_all_attempts_fail(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        original = (project / "main.cpp").read_bytes()
        script = [reply for _ in range(3) for reply in edit_script(project)]
        ui = ScriptedUI(["n", "no", "n", "no", "n", "no"])

        solved = make_agent(workspace, settings, ScriptedLLM(script), ui).solve("bug")

        assert not solved
        assert (project / "main.cpp").read_bytes() == original
        assert "could not solve this bug in 3 attempts" in ui.transcript
        assert "files are restored" in ui.transcript

    def test_gives_up_after_the_configured_number_of_attempts(self, workspace: Workspace) -> None:
        settings = Settings(api_key="k", max_attempts=2, max_steps=3)
        llm = ScriptedLLM([final_reply(), final_reply()])
        ui = ScriptedUI(["n", "", "n", ""])

        solved = make_agent(workspace, settings, llm, ui).solve("bug")

        assert not solved
        assert "ATTEMPT 2 of 2" in ui.transcript
        assert "ATTEMPT 3" not in ui.transcript

    def test_interrupting_restores_the_files_and_propagates(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        original = (project / "main.cpp").read_bytes()
        llm = ScriptedLLM(edit_script(project))
        ui = ScriptedUI([KeyboardInterrupt()])

        with pytest.raises(KeyboardInterrupt):
            make_agent(workspace, settings, llm, ui).solve("bug")

        assert (project / "main.cpp").read_bytes() == original

    def test_an_api_failure_restores_the_files_and_propagates(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        original = (project / "main.cpp").read_bytes()
        number = line_number_of(project / "main.cpp", "return a + b;")
        llm = ScriptedLLM(
            [
                tool_reply("replace_line", path="main.cpp", line_number=number, new_line="x;"),
                LLMError("service unavailable"),
            ]
        )

        with pytest.raises(LLMError, match="service unavailable"):
            make_agent(workspace, settings, llm, ScriptedUI()).solve("bug")

        assert (project / "main.cpp").read_bytes() == original

    def test_a_confirmed_fix_is_not_rolled_back(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        llm = ScriptedLLM(edit_script(project, "return a * b;"))

        make_agent(workspace, settings, llm, ScriptedUI(["y"])).solve("bug")

        assert "return a * b;" in (project / "main.cpp").read_text()


class TestModelMisbehaviour:
    def test_a_rejected_request_is_retried_without_using_an_attempt(
        self, workspace: Workspace, settings: Settings
    ) -> None:
        llm = ScriptedLLM([RejectedRequestError("tool call validation failed"), final_reply()])
        ui = ScriptedUI(["y"])
        agent = make_agent(workspace, settings, llm, ui)

        solved = agent.solve("bug")

        assert solved
        assert "rejected the request" in ui.transcript
        assert "ATTEMPT 2" not in ui.transcript
        assert agent.usage.calls == 1  # The rejected request produced no usage.

    def test_invalid_json_arguments_are_reported_to_the_model(
        self, workspace: Workspace, settings: Settings
    ) -> None:
        broken = LLMReply("", (ToolCall("call_x", "search_files", "{not json"),), 5)
        llm = ScriptedLLM([broken, final_reply()])

        make_agent(workspace, settings, llm, ScriptedUI(["y"])).solve("bug")

        tool_message = llm.conversations[-1][-1]
        assert tool_message["role"] == "tool"
        assert tool_message["tool_call_id"] == "call_x"
        assert tool_message["content"].startswith("Tool call failed: arguments are not valid JSON")

    def test_empty_arguments_are_treated_as_an_empty_object(
        self, workspace: Workspace, settings: Settings
    ) -> None:
        empty = LLMReply("", (ToolCall("call_x", "search_files", ""),), 5)
        llm = ScriptedLLM([empty, final_reply()])

        make_agent(workspace, settings, llm, ScriptedUI(["y"])).solve("bug")

        # search_files requires `keyword`, so the model is told about the missing argument.
        assert llm.conversations[-1][-1]["content"].startswith("Tool call failed:")

    def test_unknown_tools_are_reported_to_the_model(
        self, workspace: Workspace, settings: Settings
    ) -> None:
        llm = ScriptedLLM([tool_reply("run_shell", cmd="rm -rf /"), final_reply()])

        make_agent(workspace, settings, llm, ScriptedUI(["y"])).solve("bug")

        assert "Unknown tool 'run_shell'" in llm.conversations[-1][-1]["content"]

    def test_the_model_cannot_escape_the_workspace(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        outside = project.parent / "outside.cpp"
        outside.write_text("int safe;\n")
        llm = ScriptedLLM(
            [
                tool_reply("replace_line", path="../outside.cpp", line_number=1, new_line="pwned"),
                final_reply(),
            ]
        )

        make_agent(workspace, settings, llm, ScriptedUI(["y"])).solve("bug")

        assert outside.read_text() == "int safe;\n"


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ is not installed")
class TestEndToEndWithRealCompiler:
    def test_fixes_a_seeded_bug_and_verifies_the_real_program_output(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        source = project / "main.cpp"
        source.write_text(source.read_text().replace("return a + b;", "return a - b;"))
        llm = ScriptedLLM(edit_script(project, "return a + b;", target="return a - b;"))
        ui = ScriptedUI(["y"])
        verifier = CppVerifier()

        solved = make_agent(workspace, settings, llm, ui, verifier).solve("add(2, 3) gives -1")

        assert solved
        assert "Compile: OK" in ui.transcript
        assert "add(2, 3)              = 5     (expected 5)" in ui.transcript

    def test_a_change_that_does_not_compile_is_caught_and_reported(
        self, workspace: Workspace, settings: Settings, project: Path
    ) -> None:
        number = line_number_of(project / "main.cpp", "return a + b;")
        llm = ScriptedLLM(
            [
                *edit_script(project, "return a +* b;"),
                tool_reply(
                    "replace_line", path="main.cpp", line_number=number, new_line="return a + b;"
                ),
                final_reply("Fixed the typo."),
            ]
        )
        ui = ScriptedUI(["y"])

        solved = make_agent(workspace, settings, llm, ui, CppVerifier()).solve("bug")

        assert solved
        assert "Compile: FAILED" in ui.transcript
        assert "BUG FIXED in attempt 2" in ui.transcript
        feedback = next(m for m in llm.conversations[-1] if "does not compile" in m["content"])
        assert "error" in feedback["content"]
