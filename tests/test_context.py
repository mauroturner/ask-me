from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ask_me import context
from ask_me.context import build_header, normalize_context, with_header

REAL_GIT = context.subprocess_git  # captured before the autouse fixture replaces it


def fake_git(top: str | None = None, branch: str | None = None):
    def run(cwd: str, args: list[str]) -> str | None:
        if args == ["rev-parse", "--show-toplevel"]:
            return top
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return branch
        return None

    return run


def test_full_header_for_mcp_and_cli() -> None:
    header = build_header(
        "/work/repo/src", {}, context="cache layer", git=fake_git(top="/work/repo", branch="feat/cache")
    )
    assert header == "📁 repo · 🌿 feat/cache · 📝 cache layer"


def test_full_header_for_hooks_uses_the_session_part() -> None:
    header = build_header("/work/repo", {}, session="a1b2c3", git=fake_git(top="/work/repo", branch="main"))
    assert header == "📁 repo · 🌿 main · 🧵 a1b2c3"


def test_project_is_the_repo_root_even_from_a_subfolder() -> None:
    assert build_header("/work/repo/deep/sub", {}, git=fake_git(top="/work/repo")) == "📁 repo"


def test_windows_style_toplevel_and_cwd() -> None:
    assert build_header("C:\\w\\repo\\src", {}, git=fake_git(top="C:/w/repo")) == "📁 repo"
    assert build_header("C:\\Users\\me\\proj\\", {}, git=fake_git()) == "📁 proj"


def test_project_falls_back_to_the_working_directory_name() -> None:
    assert build_header("/home/me/cool-app", {}, git=fake_git(top=None)) == "📁 cool-app"


def test_env_project_overrides_the_git_name() -> None:
    header = build_header("/work/repo", {"ASK_ME_PROJECT": "custom"}, git=fake_git(top="/work/repo", branch="main"))
    assert header == "📁 custom · 🌿 main"


def test_empty_env_project_is_ignored() -> None:
    assert build_header("/work/repo", {"ASK_ME_PROJECT": "  "}, git=fake_git(top="/work/repo")) == "📁 repo"


@pytest.mark.parametrize("branch", [None, "", "HEAD"])
def test_branch_is_omitted_when_unknown_or_detached(branch: str | None) -> None:
    assert build_header("/work/repo", {}, git=fake_git(top="/work/repo", branch=branch)) == "📁 repo"


def test_no_known_part_means_no_header() -> None:
    assert build_header(None, {}, git=fake_git()) is None
    assert build_header(42, {}, git=fake_git()) is None  # type: ignore[arg-type]
    assert build_header("", {}, git=fake_git()) is None


def test_env_project_works_without_a_working_directory() -> None:
    assert build_header(None, {"ASK_ME_PROJECT": "x"}, context="t") == "📁 x · 📝 t"


def test_a_git_runner_that_raises_is_swallowed() -> None:
    def boom(cwd: str, args: list[str]) -> str | None:
        raise OSError("git exploded")

    assert build_header("/work/repo", {}, git=boom) == "📁 repo"


def test_context_is_normalized() -> None:
    assert normalize_context("  refactor \n  auth\t layer ") == "refactor auth layer"
    assert normalize_context("   ") is None
    assert normalize_context(None) is None
    long = normalize_context("x" * 100)
    assert long is not None and len(long) == 60 and long.endswith("…")
    assert normalize_context("y" * 60) == "y" * 60


def test_header_normalizes_context_and_ignores_blank_session() -> None:
    assert build_header("/a/b", {}, context=" a \n b ", session="  ", git=fake_git()) == "📁 b · 📝 a b"


def test_with_header_joins_with_a_newline_or_returns_the_body() -> None:
    assert with_header("📁 repo", "❓ hi?") == "📁 repo\n❓ hi?"
    assert with_header(None, "❓ hi?") == "❓ hi?"


# ---- the real subprocess runner -----------------------------------------------------------------
def test_subprocess_runner_uses_short_timeout_no_shell_and_captures_output(monkeypatch) -> None:
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return subprocess.CompletedProcess(command, 0, stdout="main\n".encode(), stderr=b"")

    monkeypatch.setattr(context.subprocess, "run", fake_run)
    assert REAL_GIT("/work/repo", ["rev-parse", "--abbrev-ref", "HEAD"]) == "main"
    assert seen["command"][:3] == ["git", "-C", "/work/repo"]
    assert seen["command"][3:] == ["rev-parse", "--abbrev-ref", "HEAD"]
    kwargs = seen["kwargs"]
    assert kwargs["timeout"] == 2
    assert not kwargs.get("shell")
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.PIPE and kwargs["stderr"] == subprocess.PIPE
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("git"),
        subprocess.TimeoutExpired("git", 2),
        OSError("nope"),
    ],
)
def test_subprocess_runner_never_raises(monkeypatch, failure: Exception) -> None:
    def fake_run(command, **kwargs):
        raise failure

    monkeypatch.setattr(context.subprocess, "run", fake_run)
    assert REAL_GIT("/x", ["rev-parse", "HEAD"]) is None


def test_subprocess_runner_returns_none_on_nonzero_exit_or_bad_utf8(monkeypatch) -> None:
    results = iter(
        [
            subprocess.CompletedProcess([], 128, stdout=b"", stderr=b"fatal"),
            subprocess.CompletedProcess([], 0, stdout=b"\xff\xfe", stderr=b""),
            subprocess.CompletedProcess([], 0, stdout=b"\n", stderr=b""),
        ]
    )
    monkeypatch.setattr(context.subprocess, "run", lambda command, **kwargs: next(results))
    assert REAL_GIT("/x", ["a"]) is None
    assert REAL_GIT("/x", ["a"]) is None
    assert REAL_GIT("/x", ["a"]) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_real_git_repository_from_a_subfolder(tmp_path: Path) -> None:
    repo = tmp_path / "my-real-repo"
    (repo / "pkg" / "sub").mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("checkout", "-q", "-b", "feat/real")
    git("commit", "-q", "--allow-empty", "-m", "init")
    header = build_header(str(repo / "pkg" / "sub"), {}, context="demo", git=REAL_GIT)
    assert header == "📁 my-real-repo · 🌿 feat/real · 📝 demo"
    git("checkout", "-q", "--detach")
    assert build_header(str(repo), {}, git=REAL_GIT) == "📁 my-real-repo"
