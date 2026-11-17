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


async def test_notify_prepends_the_context_header(fake, clock, config_dir) -> None:
    env = {"ASK_ME_CONFIG_DIR": str(config_dir)}
    async with make_channel(fake, clock) as channel:
        await notify(channel, "Build done", "success", env, header="📁 ask-me · 🌿 main · 📝 cache layer")
    assert fake.calls_for("sendMessage")[0]["text"] == "📁 ask-me · 🌿 main · 📝 cache layer\n✅ Build done"


async def test_a_long_notification_is_split_with_the_header_only_on_the_first_part(fake, clock, config_dir) -> None:
    env = {"ASK_ME_CONFIG_DIR": str(config_dir)}
    body = "\n\n".join(f"para {i} " + "z" * 1500 for i in range(6))  # about 9.4k chars
    async with make_channel(fake, clock) as channel:
        await notify(channel, body, "info", env, header="📁 ask-me")
    texts = [call["text"] for call in fake.calls_for("sendMessage")]
    assert len(texts) == 3
    assert all(len(text) <= 4096 for text in texts)
    assert texts[0].startswith("📁 ask-me\nℹ️ para 0 ")
    assert texts[1].startswith("(2/3) ") and texts[2].startswith("(3/3) ")
    assert all("📁" not in text for text in texts[1:])
    assert texts[0].split("\n", 1)[1][len("ℹ️ ") :] + texts[1][6:] + texts[2][6:] == body


async def test_muted_notify_with_a_header_sends_nothing(fake, clock, config_dir) -> None:
    env = {"ASK_ME_CONFIG_DIR": str(config_dir)}
    set_muted(True, env)
    async with make_channel(fake, clock) as channel:
        assert await notify(channel, "x", "info", env, header="📁 p") == "muted"
    assert fake.calls == []


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
