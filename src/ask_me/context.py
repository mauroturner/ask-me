"""Context header (S1-S4): one line telling which session a message comes from.

``📁 <project> · 🌿 <branch> · 📝 <context>`` (MCP/CLI) or ``... · 🧵 <session>`` (hooks). Every part is
optional; git lookups are best effort and never raise.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .channel import truncate

ENV_PROJECT = "ASK_ME_PROJECT"
CONTEXT_LIMIT = 60
SESSION_LENGTH = 6
GIT_TIMEOUT_SECONDS = 2
SEPARATOR = " · "

# (working directory, git arguments) -> trimmed stdout, or None on any failure.
GitRunner = Callable[[str, Sequence[str]], str | None]


def subprocess_git(cwd: str, args: Sequence[str]) -> str | None:
    """Run ``git -C <cwd> <args>``: no shell, short timeout, no console window on Windows."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    extra: dict[str, Any] = {"creationflags": flags} if flags else {}
    try:
        done = subprocess.run(
            ["git", "-C", cwd, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
            shell=False,
            **extra,
        )
        if done.returncode != 0:
            return None
        return done.stdout.decode("utf-8").strip() or None
    except Exception:  # git missing, timeout, not a repo, bad encoding: the part is simply omitted
        return None


def _git(runner: GitRunner | None, cwd: str, args: Sequence[str]) -> str | None:
    try:
        return (runner or subprocess_git)(cwd, args)
    except Exception:
        return None


def _last_segment(path: str) -> str | None:
    segments = [segment for segment in re.split(r"[\\/]", path) if segment]
    return segments[-1] if segments else None


def _project(cwd: str | None, env: Mapping[str, str], runner: GitRunner | None) -> str | None:
    override = (env.get(ENV_PROJECT) or "").strip()
    if override:
        return override
    if not cwd:
        return None
    top = _git(runner, cwd, ["rev-parse", "--show-toplevel"])
    return (_last_segment(top) if top else None) or _last_segment(cwd)


def _branch(cwd: str | None, runner: GitRunner | None) -> str | None:
    if not cwd:
        return None
    branch = _git(runner, cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
    return None if branch in (None, "", "HEAD") else branch


def normalize_context(label: str | None) -> str | None:
    """Collapse whitespace and cap the label at 60 characters."""
    if not isinstance(label, str):
        return None
    collapsed = " ".join(label.split())
    return truncate(collapsed, CONTEXT_LIMIT) if collapsed else None


def session_label(session_id: Any) -> str | None:
    """First characters of a hook ``session_id``, when it is a non-empty string."""
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()[:SESSION_LENGTH]
    return None


def build_header(
    cwd: Any,
    env: Mapping[str, str] | None,
    *,
    context: str | None = None,
    session: Any = None,
    git: GitRunner | None = None,
) -> str | None:
    """Return the header line, or None when no part is known."""
    env = os.environ if env is None else env
    directory = cwd if isinstance(cwd, str) and cwd.strip() else None
    parts = [
        f"📁 {project}" if (project := _project(directory, env, git)) else None,
        f"🌿 {branch}" if (branch := _branch(directory, git)) else None,
        f"📝 {label}" if (label := normalize_context(context)) else None,
        f"🧵 {sid}" if (sid := session_label(session)) else None,
    ]
    known = [part for part in parts if part]
    return SEPARATOR.join(known) if known else None


def with_header(header: str | None, body: str) -> str:
    return f"{header}\n{body}" if header else body
