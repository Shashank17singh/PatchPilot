from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from patchpilot import gitops
from patchpilot.cli import main
from patchpilot.diffs import unified_diff
from patchpilot.errors import GitError
from patchpilot.workspace import Workspace

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, check=True
    )
    return completed.stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    git(root, "config", "core.autocrlf", "false")
    (root / "main.cpp").write_text("int a;\n")
    (root / "other.cpp").write_text("int b;\n")
    git(root, "add", ".")
    git(root, "commit", "--quiet", "-m", "initial")
    return root


class TestPatchFormat:
    @pytest.mark.parametrize(
        ("before", "after"),
        [
            (b"a\nb\nc\n", b"a\nx\nc\n"),
            (b"a\r\nb\r\n", b"a\r\nx\r\n"),
            (b"a\nb", b"a\nx"),
            (b"a\nb", b"a\nb\n"),
            (b"a\nb\n", b"a\nb"),
            (b"a\x0cb\nc\n", b"a\x0cb\nd\n"),
        ],
        ids=["lf", "crlf", "no-final-newline", "adds-newline", "drops-newline", "form-feed"],
    )
    def test_git_apply_reproduces_the_exact_bytes(
        self, repository: Path, before: bytes, after: bytes
    ) -> None:
        path = repository / "main.cpp"
        path.write_bytes(before)
        patch = repository / "fix.patch"
        patch.write_bytes(unified_diff("main.cpp", before.decode(), after.decode()).encode("utf-8"))

        git(repository, "apply", "--whitespace=nowarn", "fix.patch")

        assert path.read_bytes() == after

    def test_no_change_gives_an_empty_diff(self) -> None:
        assert unified_diff("x.cpp", "a\n", "a\n") == ""

    def test_workspace_diff_covers_every_changed_file(self, repository: Path) -> None:
        workspace = Workspace(repository)
        snapshot = workspace.snapshot()
        (repository / "main.cpp").write_text("int changed;\n")

        patch = workspace.diff(snapshot)

        assert "a/main.cpp" in patch
        assert "other.cpp" not in patch
        assert workspace.changed_files(snapshot) == [repository / "main.cpp"]


class TestRepositoryState:
    def test_recognises_a_repository(self, repository: Path) -> None:
        assert gitops.is_repository(repository)

    def test_recognises_a_plain_directory(self, tmp_path: Path) -> None:
        assert not gitops.is_repository(tmp_path)

    def test_a_clean_tree_has_no_uncommitted_files(self, repository: Path) -> None:
        assert gitops.uncommitted_files(repository) == set()

    def test_finds_modified_and_untracked_files(self, repository: Path) -> None:
        (repository / "main.cpp").write_text("int z;\n")
        (repository / "new.cpp").write_text("int n;\n")

        assert gitops.uncommitted_files(repository) == {
            (repository / "main.cpp").resolve(),
            (repository / "new.cpp").resolve(),
        }

    def test_handles_renames(self, repository: Path) -> None:
        git(repository, "mv", "other.cpp", "renamed.cpp")

        assert (repository / "renamed.cpp").resolve() in gitops.uncommitted_files(repository)


class TestCommit:
    def test_commits_only_the_named_files(self, repository: Path) -> None:
        (repository / "main.cpp").write_text("int fixed;\n")
        (repository / "other.cpp").write_text("int user_work;\n")
        git(repository, "add", "other.cpp")

        revision = gitops.commit_files(repository, [repository / "main.cpp"], "fix: bug")

        assert git(repository, "rev-parse", "--short", "HEAD").strip() == revision
        assert git(repository, "show", "--name-only", "--format=", "HEAD").split() == ["main.cpp"]
        assert "other.cpp" in git(repository, "diff", "--cached", "--name-only")

    def test_a_failed_commit_raises(self, repository: Path) -> None:
        with pytest.raises(GitError, match="git commit failed"):
            gitops.commit_files(repository, [repository / "missing.cpp"], "fix: bug")

    def test_the_message_uses_the_bug_and_summary(self) -> None:
        message = gitops.commit_message("add   is\nwrong", "Changed the operator.")

        assert message == "fix: add is wrong\n\nChanged the operator."

    def test_a_long_subject_is_shortened(self) -> None:
        subject = gitops.commit_message("x" * 200, "").splitlines()[0]

        assert len(subject) <= 70
        assert subject.endswith("...")


class TestCommandLine:
    """Drive the real CLI with a test double in place of the model."""

    @pytest.fixture(autouse=True)
    def configure(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GROQ_API_KEY", "key")
        monkeypatch.setattr("builtins.input", lambda _="": "y")

    @staticmethod
    def use_fix(monkeypatch: pytest.MonkeyPatch, content: str = "int fixed;\n") -> None:
        def solve(self: object, bug: str) -> bool:
            workspace = self._workspace  # type: ignore[attr-defined]
            self.original_files = workspace.snapshot()  # type: ignore[attr-defined]
            self.summary = "Changed it."  # type: ignore[attr-defined]
            (workspace.root / "main.cpp").write_text(content)
            return True

        monkeypatch.setattr("patchpilot.cli.CodingAgent.solve", solve)
        monkeypatch.setattr("patchpilot.cli.create_client", lambda _: object())

    def test_commit_flag_commits_the_fix(
        self, repository: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.use_fix(monkeypatch)

        assert main([str(repository), "--commit", "--build", "g++"]) == 0

        assert git(repository, "log", "-1", "--format=%s").strip() == "fix: y"
        assert "Changed it." in git(repository, "log", "-1", "--format=%b")

    def test_patch_flag_writes_an_applicable_patch(
        self, repository: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.use_fix(monkeypatch)
        patch = tmp_path / "fix.patch"

        assert main([str(repository), "--patch-file", str(patch), "--build", "g++"]) == 0

        git(repository, "checkout", "--", "main.cpp")
        git(repository, "apply", str(patch))
        assert (repository / "main.cpp").read_text() == "int fixed;\n"

    def test_commit_outside_a_repository_fails_before_prompting(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def fail(_: str = "") -> str:
            raise AssertionError("must not prompt")

        monkeypatch.setattr("builtins.input", fail)
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "main.cpp").write_text("int a;\n")

        assert main([str(plain), "--commit"]) == 2
        assert "needs a git repository" in capsys.readouterr().err

    def test_a_file_with_earlier_uncommitted_work_is_not_committed(
        self,
        repository: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self.use_fix(monkeypatch)
        (repository / "main.cpp").write_text("int my_unfinished_work;\n")
        before = git(repository, "rev-parse", "HEAD")

        assert main([str(repository), "--commit", "--build", "g++"]) == 0

        assert git(repository, "rev-parse", "HEAD") == before
        assert "Not committing: main.cpp" in capsys.readouterr().out

    def test_a_failed_commit_keeps_the_fix_and_still_succeeds(
        self,
        repository: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        self.use_fix(monkeypatch)

        def refuse(*_: object) -> str:
            raise GitError("git commit failed: hook said no")

        monkeypatch.setattr("patchpilot.cli.gitops.commit_files", refuse)

        assert main([str(repository), "--commit", "--build", "g++"]) == 0

        assert (repository / "main.cpp").read_text() == "int fixed;\n"
        assert "hook said no" in capsys.readouterr().out
