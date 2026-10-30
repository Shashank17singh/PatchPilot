from __future__ import annotations

from pathlib import Path

import pytest

from conftest import line_number_of
from patchpilot.tools import TOOL_SCHEMAS, ToolKit
from patchpilot.workspace import Workspace


@pytest.fixture
def tools(workspace: Workspace) -> ToolKit:
    return ToolKit(workspace)


class TestSearchFiles:
    def test_empty_keyword_lists_every_file(self, tools: ToolKit) -> None:
        assert tools.search_files("") == "main.cpp"

    def test_reports_match_counts(self, tools: ToolKit) -> None:
        result = tools.search_files("factorial")

        assert result.startswith("main.cpp  (")
        assert "matches)" in result

    def test_is_case_insensitive(self, tools: ToolKit) -> None:
        assert tools.search_files("FACTORIAL").startswith("main.cpp")

    def test_matches_on_file_name_alone(self, tools: ToolKit) -> None:
        assert tools.search_files("main").startswith("main.cpp  (")

    def test_falls_back_to_the_full_listing_when_nothing_matches(self, tools: ToolKit) -> None:
        result = tools.search_files("zzz_not_in_any_file")

        assert "No file mentions 'zzz_not_in_any_file'" in result
        assert "main.cpp" in result

    def test_reports_an_empty_workspace(self, tmp_path: Path) -> None:
        assert ToolKit(Workspace(tmp_path)).search_files("x") == "The workspace has no code files."

    def test_skips_files_that_are_not_valid_utf8(self, workspace: Workspace) -> None:
        (workspace.root / "legacy.cpp").write_bytes(b"// caf\xe9 factorial\n")

        result = ToolKit(workspace).search_files("factorial")

        assert "main.cpp" in result
        assert "legacy.cpp" not in result


class TestSearchInFile:
    def test_returns_matching_lines_with_numbers(self, tools: ToolKit, project: Path) -> None:
        expected = line_number_of(project / "main.cpp", "int add(int a, int b)")

        result = tools.search_in_file("main.cpp", "int add")

        assert result == f"{expected:>4}: int add(int a, int b) {{"

    def test_empty_keyword_returns_the_whole_file(self, tools: ToolKit, project: Path) -> None:
        total = len((project / "main.cpp").read_text().splitlines())

        result = tools.search_in_file("main.cpp", "")

        assert len(result.splitlines()) == total

    def test_reports_when_the_keyword_is_absent(self, tools: ToolKit) -> None:
        assert "not found in main.cpp" in tools.search_in_file("main.cpp", "zzz_absent")

    def test_rejects_unknown_files(self, tools: ToolKit) -> None:
        assert "not found" in tools.search_in_file("missing.cpp", "x")

    def test_rejects_path_traversal(self, tools: ToolKit, project: Path) -> None:
        (project.parent / "outside.cpp").write_text("int secret;")

        assert "not found" in tools.search_in_file("../outside.cpp", "secret")

    def test_reports_non_utf8_files(self, workspace: Workspace) -> None:
        (workspace.root / "legacy.cpp").write_bytes(b"// caf\xe9\n")

        assert "not valid UTF-8" in ToolKit(workspace).search_in_file("legacy.cpp", "")


class TestReplaceLine:
    def test_replaces_exactly_one_line(self, tools: ToolKit, project: Path) -> None:
        path = project / "main.cpp"
        before = path.read_text().splitlines()
        number = line_number_of(path, "return a + b;")

        result = tools.replace_line("main.cpp", number, "return a - b;")

        after = path.read_text().splitlines()
        assert after[number - 1] == "    return a - b;"
        assert [i for i, (x, y) in enumerate(zip(before, after, strict=True)) if x != y] == [
            number - 1
        ]
        assert "before: return a + b;" in result
        assert "after : return a - b;" in result

    def test_inherits_indentation_when_the_model_omits_it(
        self, tools: ToolKit, project: Path
    ) -> None:
        path = project / "main.cpp"
        number = line_number_of(path, "return a + b;")

        tools.replace_line("main.cpp", number, "return 0;")

        assert path.read_text().splitlines()[number - 1] == "    return 0;"

    def test_keeps_explicit_indentation(self, tools: ToolKit, project: Path) -> None:
        path = project / "main.cpp"
        number = line_number_of(path, "return a + b;")

        tools.replace_line("main.cpp", number, "        return 0;")

        assert path.read_text().splitlines()[number - 1] == "        return 0;"

    def test_preserves_crlf_line_endings(self, tmp_path: Path) -> None:
        path = tmp_path / "win.cpp"
        path.write_bytes(b"int a;\r\nint b;\r\nint c;\r\n")

        ToolKit(Workspace(tmp_path)).replace_line("win.cpp", 2, "int x;")

        assert path.read_bytes() == b"int a;\r\nint x;\r\nint c;\r\n"

    def test_accepts_a_line_number_sent_as_a_string(self, tools: ToolKit, project: Path) -> None:
        path = project / "main.cpp"
        number = line_number_of(path, "return a + b;")

        tools.replace_line("main.cpp", str(number), "return 1;")  # type: ignore[arg-type]

        assert path.read_text().splitlines()[number - 1] == "    return 1;"

    @pytest.mark.parametrize("bad", ["abc", None, ""])
    def test_rejects_a_non_integer_line_number(self, tools: ToolKit, bad: object) -> None:
        result = tools.replace_line("main.cpp", bad, "int x;")  # type: ignore[arg-type]

        assert "line_number must be an integer" in result

    @pytest.mark.parametrize("number", [0, -1, 10_000])
    def test_rejects_out_of_range_line_numbers(
        self, tools: ToolKit, project: Path, number: int
    ) -> None:
        before = (project / "main.cpp").read_bytes()

        result = tools.replace_line("main.cpp", number, "int x;")

        assert "there is no line" in result
        assert (project / "main.cpp").read_bytes() == before

    @pytest.mark.parametrize("new_line", ["a\nb", "a\rb"])
    def test_rejects_multi_line_replacements(
        self, tools: ToolKit, project: Path, new_line: str
    ) -> None:
        before = (project / "main.cpp").read_bytes()

        result = tools.replace_line("main.cpp", 1, new_line)

        assert "ONE line" in result
        assert (project / "main.cpp").read_bytes() == before

    def test_cannot_edit_files_outside_the_workspace(self, tools: ToolKit, project: Path) -> None:
        outside = project.parent / "outside.cpp"
        outside.write_text("int safe;\n")

        result = tools.replace_line("../outside.cpp", 1, "int pwned;")

        assert "not found" in result
        assert outside.read_text() == "int safe;\n"

    def test_cannot_edit_non_code_files(self, tools: ToolKit, project: Path) -> None:
        env_file = project / ".env"
        env_file.write_text("KEY=secret\n")

        result = tools.replace_line(".env", 1, "KEY=leaked")

        assert "not found" in result
        assert env_file.read_text() == "KEY=secret\n"


class TestExecute:
    def test_dispatches_to_the_named_tool(self, tools: ToolKit) -> None:
        assert tools.execute("search_files", {"keyword": ""}) == "main.cpp"

    def test_reports_unknown_tools(self, tools: ToolKit) -> None:
        result = tools.execute("delete_everything", {})

        assert "Unknown tool 'delete_everything'" in result
        assert "search_files" in result

    def test_reports_wrong_argument_names(self, tools: ToolKit) -> None:
        assert tools.execute("search_files", {"word": "x"}).startswith("Tool call failed:")

    def test_reports_missing_arguments(self, tools: ToolKit) -> None:
        assert tools.execute("search_in_file", {"path": "main.cpp"}).startswith("Tool call failed:")

    @pytest.mark.parametrize("arguments", [[], "text", 5, None])
    def test_rejects_non_object_arguments(self, tools: ToolKit, arguments: object) -> None:
        assert "must be a JSON object" in tools.execute("search_files", arguments)


def test_schemas_match_the_implemented_tools(tools: ToolKit) -> None:
    declared = {schema["function"]["name"] for schema in TOOL_SCHEMAS}

    assert declared == {
        "search_files",
        "search_in_file",
        "replace_line",
        "replace_range",
        "insert_lines",
        "delete_lines",
    }
    for schema in TOOL_SCHEMAS:
        name = schema["function"]["name"]
        required = schema["function"]["parameters"]["required"]
        properties = schema["function"]["parameters"]["properties"]
        assert set(required) <= set(properties)
        assert name in tools._registry
