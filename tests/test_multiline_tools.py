from __future__ import annotations

from pathlib import Path

import pytest

from patchpilot.tools import ToolKit
from patchpilot.workspace import Workspace


def write(tmp_path: Path, content: bytes, name: str = "code.cpp") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


class TestReplaceRange:
    def test_replaces_a_block_with_a_different_number_of_lines(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\nb\nc\nd\n")

        result = ToolKit(Workspace(tmp_path)).replace_range("code.cpp", 2, 3, "x\ny\nz")

        assert path.read_bytes() == b"a\nx\ny\nz\nd\n"
        assert "Lines 2-3 of code.cpp replaced." in result
        assert "search again" in result

    def test_matches_crlf_files(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\r\nb\r\nc\r\n")

        ToolKit(Workspace(tmp_path)).replace_range("code.cpp", 2, 2, "x\ny")

        assert path.read_bytes() == b"a\r\nx\r\ny\r\nc\r\n"

    def test_a_crlf_reply_on_an_lf_file_stays_lf(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\nb\n")

        ToolKit(Workspace(tmp_path)).replace_range("code.cpp", 1, 1, "x\r\ny\r\n")

        assert path.read_bytes() == b"x\ny\nb\n"

    def test_keeps_a_missing_final_newline(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\nb")

        ToolKit(Workspace(tmp_path)).replace_range("code.cpp", 2, 2, "x\ny")

        assert path.read_bytes() == b"a\nx\ny"

    def test_writes_text_verbatim_including_indentation(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\nb\n")

        ToolKit(Workspace(tmp_path)).replace_range("code.cpp", 1, 1, "        deep")

        assert path.read_bytes() == b"        deep\nb\n"

    @pytest.mark.parametrize(("start", "end"), [(0, 1), (2, 1), (1, 99), ("x", 1), (1, None)])
    def test_rejects_invalid_ranges(self, tmp_path: Path, start: object, end: object) -> None:
        path = write(tmp_path, b"a\nb\n")

        result = ToolKit(Workspace(tmp_path)).replace_range(
            "code.cpp",
            start,  # type: ignore[arg-type]
            end,  # type: ignore[arg-type]
            "x",
        )

        assert "range" in result or "integers" in result
        assert path.read_bytes() == b"a\nb\n"

    def test_accepts_numbers_sent_as_strings(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\nb\n")

        ToolKit(Workspace(tmp_path)).replace_range("code.cpp", "1", "1", "x")  # type: ignore[arg-type]

        assert path.read_bytes() == b"x\nb\n"

    def test_empty_text_is_refused(self, tmp_path: Path) -> None:
        write(tmp_path, b"a\n")

        result = ToolKit(Workspace(tmp_path)).replace_range("code.cpp", 1, 1, "  ")

        assert "delete_lines" in result

    def test_cannot_leave_the_workspace(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        project.mkdir()
        outside = write(tmp_path, b"safe\n", "outside.cpp")

        result = ToolKit(Workspace(project)).replace_range("../outside.cpp", 1, 1, "x")

        assert "not found" in result
        assert outside.read_bytes() == b"safe\n"


class TestInsertLines:
    def test_inserts_after_the_given_line(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\nb\n")

        result = ToolKit(Workspace(tmp_path)).insert_lines("code.cpp", 1, "x\ny")

        assert path.read_bytes() == b"a\nx\ny\nb\n"
        assert "Inserted after line 1" in result

    def test_line_zero_inserts_at_the_top(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\n")

        ToolKit(Workspace(tmp_path)).insert_lines("code.cpp", 0, "#include <vector>")

        assert path.read_bytes() == b"#include <vector>\na\n"

    def test_inserting_after_a_last_line_without_newline(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\nb")

        ToolKit(Workspace(tmp_path)).insert_lines("code.cpp", 2, "c")

        assert path.read_bytes() == b"a\nb\nc"

    def test_works_on_an_empty_file(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"")

        ToolKit(Workspace(tmp_path)).insert_lines("code.cpp", 0, "int x;")

        assert path.read_bytes() == b"int x;\n"

    def test_can_add_a_missing_closing_brace(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"void f() {\n    int x;\n")

        ToolKit(Workspace(tmp_path)).insert_lines("code.cpp", 2, "}")

        assert path.read_bytes() == b"void f() {\n    int x;\n}\n"

    @pytest.mark.parametrize("line", [-1, 3, "x", None])
    def test_rejects_invalid_positions(self, tmp_path: Path, line: object) -> None:
        path = write(tmp_path, b"a\nb\n")

        ToolKit(Workspace(tmp_path)).insert_lines("code.cpp", line, "x")  # type: ignore[arg-type]

        assert path.read_bytes() == b"a\nb\n"


class TestDeleteLines:
    def test_deletes_a_block(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\nb\nc\nd\n")

        result = ToolKit(Workspace(tmp_path)).delete_lines("code.cpp", 2, 3)

        assert path.read_bytes() == b"a\nd\n"
        assert "Deleted lines 2-3" in result

    def test_deleting_the_last_line_keeps_a_missing_newline_missing(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\nb")

        ToolKit(Workspace(tmp_path)).delete_lines("code.cpp", 2, 2)

        assert path.read_bytes() == b"a"

    def test_can_empty_a_file(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\n")

        ToolKit(Workspace(tmp_path)).delete_lines("code.cpp", 1, 1)

        assert path.read_bytes() == b""

    def test_rejects_invalid_ranges(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\n")

        result = ToolKit(Workspace(tmp_path)).delete_lines("code.cpp", 1, 5)

        assert "not a valid range" in result
        assert path.read_bytes() == b"a\n"


class TestApproval:
    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("replace_line", {"path": "code.cpp", "line_number": 1, "new_line": "x"}),
            (
                "replace_range",
                {"path": "code.cpp", "start_line": 1, "end_line": 1, "new_text": "x"},
            ),
            ("insert_lines", {"path": "code.cpp", "after_line": 0, "new_text": "x"}),
            ("delete_lines", {"path": "code.cpp", "start_line": 1, "end_line": 1}),
        ],
    )
    def test_every_edit_tool_asks_first_and_a_refusal_writes_nothing(
        self, tmp_path: Path, tool: str, arguments: dict[str, object]
    ) -> None:
        path = write(tmp_path, b"a\nb\n")
        seen: list[tuple[str, str]] = []

        def refuse(relative: str, diff: str) -> bool:
            seen.append((relative, diff))
            return False

        result = ToolKit(Workspace(tmp_path), refuse).execute(tool, arguments)

        assert path.read_bytes() == b"a\nb\n"
        assert "rejected" in result
        assert seen[0][0] == "code.cpp"
        assert seen[0][1].startswith("diff --git a/code.cpp b/code.cpp\n")

    def test_an_approved_edit_is_written(self, tmp_path: Path) -> None:
        path = write(tmp_path, b"a\n")

        ToolKit(Workspace(tmp_path), lambda _p, _d: True).replace_line("code.cpp", 1, "x")

        assert path.read_bytes() == b"x\n"

    def test_a_change_with_no_effect_does_not_ask(self, tmp_path: Path) -> None:
        write(tmp_path, b"a\n")

        def fail(_path: str, _diff: str) -> bool:
            raise AssertionError("nothing to approve")

        result = ToolKit(Workspace(tmp_path), fail).replace_line("code.cpp", 1, "a")

        assert "does not change" in result
