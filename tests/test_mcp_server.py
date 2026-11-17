from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from fakes import FakeClock, FakeTelegram
from mcp import Client

from ask_me.mcp_server import build_server

pytestmark = pytest.mark.anyio


def configure(config_dir: Path, fake: FakeTelegram) -> dict[str, str]:
    data = {"telegram": {"botToken": fake.token, "chatId": str(fake.chat_id)}}
    (config_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")
    return {"ASK_ME_CONFIG_DIR": str(config_dir)}


def server_for(env: dict[str, str], fake: FakeTelegram, clock: FakeClock):
    return build_server(
        env,
        transport=fake.transport,
        base_url="https://telegram.test",
        clock=clock,
        rng=random.Random(0),
        project="demo",
    )


async def test_server_lists_notify_and_ask_with_guidance(fake, clock, config_dir) -> None:
    server = server_for(configure(config_dir, fake), fake, clock)
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    assert set(tools) == {"notify", "ask"}
    assert "notify me" in tools["notify"].description.lower()
    assert "away" in tools["ask"].description.lower()
    assert set(tools["notify"].input_schema["properties"]) == {"message", "level"}
    assert set(tools["ask"].input_schema["properties"]) == {
        "question",
        "options",
        "allow_free_text",
        "timeout_seconds",
    }
    assert tools["notify"].input_schema["required"] == ["message"]
    assert tools["ask"].input_schema["required"] == ["question"]


async def test_notify_tool_sends_and_returns_sent(fake, clock, config_dir) -> None:
    server = server_for(configure(config_dir, fake), fake, clock)
    async with Client(server) as client:
        result = await client.call_tool("notify", {"message": "done", "level": "success"})
    assert result.content[0].text == "sent"
    assert fake.calls_for("sendMessage")[0]["text"] == "✅ done"


async def test_notify_tool_reports_muted(fake, clock, config_dir) -> None:
    env = configure(config_dir, fake)
    (config_dir / "muted").write_text("muted", encoding="utf-8")
    async with Client(server_for(env, fake, clock)) as client:
        result = await client.call_tool("notify", {"message": "done"})
    assert result.content[0].text == "muted"
    assert fake.calls == []


async def test_ask_tool_returns_json_text(fake, clock, config_dir) -> None:
    server = server_for(configure(config_dir, fake), fake, clock)

    def script(f: FakeTelegram) -> None:
        if f.messages and not f.script_done:
            f.script_done = True
            f.user_tap(f.sent_ids()[0], 0)

    fake.script_done = False
    fake.script = script
    async with Client(server) as client:
        result = await client.call_tool("ask", {"question": "Ship it?", "options": ["Yes", "No"]})
    assert json.loads(result.content[0].text) == {"answered": True, "answer": "Yes", "via": "button"}
    assert fake.calls_for("sendMessage")[0]["text"] == "❓ (demo) Ship it?"


async def test_ask_tool_timeout_result(fake, clock, config_dir) -> None:
    server = server_for(configure(config_dir, fake), fake, clock)
    async with Client(server) as client:
        result = await client.call_tool("ask", {"question": "Ship it?", "options": ["Yes"], "timeout_seconds": 30})
    assert json.loads(result.content[0].text) == {"answered": False, "reason": "timeout"}


async def test_ask_tool_reports_progress_when_the_client_asks_for_it(fake, clock, config_dir) -> None:
    server = server_for(configure(config_dir, fake), fake, clock)
    seen: list[float] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append(progress)

    async with Client(server) as client:
        await client.call_tool(
            "ask",
            {"question": "Ship it?", "options": ["Yes"], "timeout_seconds": 300},
            progress_callback=on_progress,
        )
    assert len(seen) >= 3
    assert seen == sorted(seen)


async def test_tools_fail_clearly_when_not_configured(fake, clock, config_dir) -> None:
    server = server_for({"ASK_ME_CONFIG_DIR": str(config_dir)}, fake, clock)
    async with Client(server) as client:
        result = await client.call_tool("notify", {"message": "hi"}, )
    assert result.is_error is True
    assert "ask-me is not configured. Run: ask-me setup" in result.content[0].text
