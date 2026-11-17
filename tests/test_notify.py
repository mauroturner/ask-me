from __future__ import annotations

import pytest
from fakes import FakeClock, FakeTelegram

from ask_me.config import is_muted, set_muted
from ask_me.notify import LEVELS, format_notification, notify
from ask_me.telegram import TelegramClient

pytestmark = pytest.mark.anyio


def make_channel(fake: FakeTelegram, clock: FakeClock) -> TelegramClient:
    return TelegramClient(
        fake.token, str(fake.chat_id), transport=fake.transport, base_url="https://telegram.test", sleep=clock.sleep
    )


@pytest.mark.parametrize(
    ("level", "prefix"),
    [("info", "ℹ️"), ("success", "✅"), ("warning", "⚠️"), ("important", "🚨")],
)
def test_levels_prefix_the_message_with_an_emoji(level: str, prefix: str) -> None:
    assert format_notification("build done", level) == f"{prefix} build done"


def test_default_level_is_info() -> None:
    assert format_notification("hi") == "ℹ️ hi"


def test_unknown_level_is_rejected() -> None:
    with pytest.raises(ValueError):
        format_notification("hi", "loud")
    assert set(LEVELS) == {"info", "success", "warning", "important"}


async def test_notify_sends_the_formatted_message(fake, clock, config_dir) -> None:
    env = {"ASK_ME_CONFIG_DIR": str(config_dir)}
    async with make_channel(fake, clock) as channel:
        assert await notify(channel, "all green", "success", env) == "sent"
    assert fake.calls_for("sendMessage")[0]["text"] == "✅ all green"


async def test_mute_suppresses_notify_and_unmute_restores_it(fake, clock, config_dir) -> None:
    env = {"ASK_ME_CONFIG_DIR": str(config_dir)}
    assert is_muted(env) is False
    set_muted(True, env)
    assert is_muted(env) is True
    assert (config_dir / "muted").exists()
    async with make_channel(fake, clock) as channel:
        assert await notify(channel, "shh", "info", env) == "muted"
        assert fake.calls == []
        set_muted(False, env)
        assert not (config_dir / "muted").exists()
        assert await notify(channel, "hello again", "info", env) == "sent"
    assert len(fake.calls_for("sendMessage")) == 1
