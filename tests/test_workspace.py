from __future__ import annotations

import os
from pathlib import Path

import pytest

from patchpilot.errors import WorkspaceError
from patchpilot.workspace import Workspace


def test_rejects_a_path_that_is_not_a_directory(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError):
        Workspace(tmp_path / "missing")


def test_lists_only_code_files_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.cpp").write_text("int b;")
    (tmp_path / "a.h").write_text("int a;")
    (tmp_path / "notes.txt").write_text("not code")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.hpp").write_text("int c;")

    names = [Workspace(tmp_path).relative(p) for p in Workspace(tmp_path).code_files()]

    assert names == ["a.h", "b.cpp", "sub/c.hpp"]


def test_skips_ignored_directories(tmp_path: Path) -> None:
    for directory in (".git", ".venv", "build"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "hidden.cpp").write_text("int x;")
    (tmp_path / "main.cpp").write_text("int main() {}")

    names = [p.name for p in Workspace(tmp_path).code_files()]

    assert names == ["main.cpp"]


def test_source_files_excludes_headers(tmp_path: Path) -> None:
    (tmp_path / "main.cpp").write_text("")
    (tmp_path / "util.cc").write_text("")
    (tmp_path / "util.h").write_text("")

    names = [p.name for p in Workspace(tmp_path).source_files()]

    assert names == ["main.cpp", "util.cc"]


class TestResolve:
    def test_returns_existing_code_file(self, workspace: Workspace) -> None:
        assert workspace.resolve("main.cpp") == workspace.root / "main.cpp"

    def test_rejects_parent_directory_traversal(self, workspace: Workspace) -> None:
        (workspace.root.parent / "outside.cpp").write_text("int x;")

        assert workspace.resolve("../outside.cpp") is None

    def test_rejects_absolute_paths_outside_the_workspace(
        self, workspace: Workspace, tmp_path: Path
    ) -> None:
        outside = tmp_path / "elsewhere.cpp"
        outside.write_text("int x;")

        assert workspace.resolve(str(outside)) is None

    def test_rejects_non_code_files(self, workspace: Workspace) -> None:
        (workspace.root / ".env").write_text("SECRET=1")
        (workspace.root / "notes.txt").write_text("hello")

        assert workspace.resolve(".env") is None
        assert workspace.resolve("notes.txt") is None

    def test_rejects_missing_files_and_directories(self, workspace: Workspace) -> None:
        (workspace.root / "folder.cpp").mkdir()

        assert workspace.resolve("nope.cpp") is None
        assert workspace.resolve("folder.cpp") is None

    def test_rejects_files_in_ignored_directories(self, workspace: Workspace) -> None:
        (workspace.root / "build").mkdir()
        (workspace.root / "build" / "gen.cpp").write_text("int x;")

        assert workspace.resolve("build/gen.cpp") is None

    def test_rejects_embedded_nul_byte(self, workspace: Workspace) -> None:
        assert workspace.resolve("main.cpp\x00.txt") is None

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need elevated rights on Windows")
    def test_rejects_symlink_pointing_outside(self, workspace: Workspace, tmp_path: Path) -> None:
        target = tmp_path / "secret.cpp"
        target.write_text("int secret;")
        (workspace.root / "link.cpp").symlink_to(target)

        assert workspace.resolve("link.cpp") is None
        assert "link.cpp" not in [p.name for p in workspace.code_files()]


class TestReadAndWrite:
    def test_line_endings_survive_a_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "crlf.cpp"
        path.write_bytes(b"int a;\r\nint b;\r\n")

        lines = Workspace.read_lines(path)
        Workspace.write_lines(path, lines)

        assert lines == ["int a;\r\n", "int b;\r\n"]
        assert path.read_bytes() == b"int a;\r\nint b;\r\n"

    def test_invalid_utf8_raises_workspace_error(self, tmp_path: Path) -> None:
        path = tmp_path / "latin1.cpp"
        path.write_bytes(b"// caf\xe9\n")

        with pytest.raises(WorkspaceError):
            Workspace.read_lines(path)


class TestSnapshot:
    def test_restore_reverts_modified_files(self, workspace: Workspace) -> None:
        path = workspace.root / "main.cpp"
        original = path.read_bytes()
        snapshot = workspace.snapshot()

        path.write_text("corrupted")
        Workspace.restore(snapshot)

        assert path.read_bytes() == original

    def test_restore_recreates_deleted_files(self, workspace: Workspace) -> None:
        path = workspace.root / "main.cpp"
        original = path.read_bytes()
        snapshot = workspace.snapshot()

        path.unlink()
        Workspace.restore(snapshot)

        assert path.read_bytes() == original
