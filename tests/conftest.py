from __future__ import annotations

from pathlib import Path

import pytest
from fakes import FakeClock, FakeTelegram


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake(clock: FakeClock) -> FakeTelegram:
    return FakeTelegram(clock=clock)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    path = tmp_path / "ask-me-config"
    path.mkdir()
    return path


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Never let a test see or touch the real ~/.ask-me or real credentials."""
    for name in ("ASK_ME_CONFIG_DIR", "ASK_ME_TELEGRAM_TOKEN", "ASK_ME_TELEGRAM_CHAT_ID", "ASK_ME_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


@pytest.fixture(autouse=True)
def _no_real_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Message tests must not depend on the git binary or on this repository's branch."""
    monkeypatch.setattr("ask_me.context.subprocess_git", lambda cwd, args: None)
