"""Regression probes from the independent S5 verification: several asks sharing one config dir."""

from __future__ import annotations

import os
import random
from pathlib import Path

import anyio
import pytest
from fakes import FakeClock, FakeTelegram

from ask_me.ask import ask
from ask_me.channel import Incoming
from ask_me.inbox import Inbox
from ask_me.telegram import TelegramClient

pytestmark = pytest.mark.anyio


def entries(config_dir: Path, sub: str) -> list[Path]:
    """Unconsumed JSON state files (claim ``*.lock`` markers are expected to remain)."""
    return list((config_dir / sub).glob("*.json"))


def make_channel(fake: FakeTelegram, clock: FakeClock) -> TelegramClient:
    return TelegramClient(
        fake.token, str(fake.chat_id), transport=fake.transport,
        base_url="https://telegram.test", sleep=clock.sleep,
    )


async def run(fake: FakeTelegram, clock: FakeClock, config_dir: Path, question: str, **kw) -> dict:
    kw.setdefault("timeout_seconds", 1500)
    kw.setdefault("rng", random.Random(0))
    async with make_channel(fake, clock) as channel:
        return (await ask(channel, Inbox(config_dir, clock), question, clock=clock, **kw)).to_dict()


async def start_staggered(fake, clock, config_dir, tg, specs, results) -> None:
    """Start each ask in ``specs`` (name, kwargs), waiting until its message is sent, 3 s apart."""
    for name, kw in specs:
        sent_before = len(fake.messages)

        async def one(name=name, kw=kw):
            results[name] = await run(fake, clock, config_dir, f"q-{name}", **kw)

        tg.start_soon(one)
        while len(fake.messages) == sent_before:
            await anyio.sleep(0)
        clock.advance(3)


@pytest.mark.parametrize("order", [(0, 1, 2), (2, 1, 0), (1, 2, 0), (1, 0, 2)])
async def test_three_concurrent_asks_get_only_their_own_answer(fake, clock, config_dir, order) -> None:
    def script(f):
        if len(f.messages) >= 3 and not f.done:
            f.done = True
            a, b, c = f.sent_ids()
            actions = [lambda: f.user_tap(a, 1), lambda: f.user_tap(b, 0), lambda: f.user_text("c-text", reply_to=c)]
            for index in order:
                actions[index]()

    fake.done = False
    fake.script = script
    results: dict = {}
    async with anyio.create_task_group() as tg:
        await start_staggered(
            fake, clock, config_dir, tg,
            [("A", {"options": ["a0", "a1"]}), ("B", {"options": ["b0", "b1"]}), ("C", {})],
            results,
        )
    assert results["A"] == {"answered": True, "answer": "a1", "via": "button"}
    assert results["B"] == {"answered": True, "answer": "b0", "via": "button"}
    assert results["C"] == {"answered": True, "answer": "c-text", "via": "text"}
    assert len(fake.callback_answers) == 2
    assert entries(config_dir, "pending") == [] and entries(config_dir, "inbox") == []


async def test_plain_text_goes_to_newest_and_reply_to_older(fake, clock, config_dir) -> None:
    def script(f):
        if len(f.messages) >= 2 and not f.d1:
            f.d1 = True
            f.user_text("plain1")
        if f.d1 and f.calls_for("editMessageText") and not f.d2:
            f.d2 = True
            f.user_text("reply-old", reply_to=f.sent_ids()[0])

    fake.d1 = fake.d2 = False
    fake.script = script
    results: dict = {}
    async with anyio.create_task_group() as tg:
        await start_staggered(fake, clock, config_dir, tg, [("old", {}), ("new", {})], results)
    assert results["new"]["answer"] == "plain1"
    assert results["old"]["answer"] == "reply-old"


async def test_foreign_chat_is_never_accepted(fake, clock, config_dir) -> None:
    def script(f):
        if f.messages and not f.d:
            f.d = True
            mid = f.sent_ids()[0]
            f.user_tap(mid, 0, chat_id=999)
            f.user_text("evil", chat_id=999, reply_to=mid)
            f.user_text("evil plain", chat_id=999)
            f.user_text("good", reply_to=mid)

    fake.d = False
    fake.script = script
    result = await run(fake, clock, config_dir, "q", options=["Yes"], allow_free_text=True)
    assert result["answer"] == "good"
    assert Inbox(config_dir, clock).read_offset() == 104  # foreign updates confirmed...
    assert entries(config_dir, "inbox") == []  # ...but never stored


async def test_foreign_only_then_timeout(fake, clock, config_dir) -> None:
    def script(f):
        if f.messages and not f.d:
            f.d = True
            f.user_tap(f.sent_ids()[0], 0, chat_id=999)
            f.user_text("evil", chat_id=999)

    fake.d = False
    fake.script = script
    result = await run(fake, clock, config_dir, "q", options=["Yes"], timeout_seconds=60)
    assert result == {"answered": False, "reason": "timeout"}
    assert Inbox(config_dir, clock).read_offset() == 102
    assert entries(config_dir, "inbox") == [] and entries(config_dir, "pending") == []
    (edit,) = fake.calls_for("editMessageText")
    assert edit["text"].endswith("⌛ expired") and "reply_markup" not in edit


async def test_entry_taken_by_another_process_is_not_reported(fake, clock, config_dir, monkeypatch) -> None:
    real_claim = Inbox.claim
    state = {"stolen": False}

    def stealing_claim(self, entry):
        if not state["stolen"]:
            state["stolen"] = True
            os.rename(entry.path, str(entry.path) + ".taken-by-other")
        return real_claim(self, entry)

    monkeypatch.setattr(Inbox, "claim", stealing_claim)

    def script(f):
        if f.messages and not f.d:
            f.d = True
            f.user_tap(f.sent_ids()[0], 0)
        elif f.d and not f.d2:
            f.d2 = True
            f.user_text("second", reply_to=f.sent_ids()[0])

    fake.d = fake.d2 = False
    fake.script = script
    result = await run(fake, clock, config_dir, "q", options=["Yes"])
    assert result["answer"] == "second" and result["via"] == "text"


async def test_restart_does_not_replay_confirmed_updates(fake, clock, config_dir) -> None:
    def script(f):
        if f.messages and not f.d:
            f.d = True
            f.u = f.user_text("hello", reply_to=f.sent_ids()[0])
        if f.d and f.redeliver:
            f._updates.append(f.u)
            f.clock.advance(10)

    fake.d = fake.redeliver = False
    fake.script = script
    first = await run(fake, clock, config_dir, "q1")
    assert first["answer"] == "hello"
    assert Inbox(config_dir, clock).read_offset() == fake.u["update_id"] + 1
    fake.redeliver = True
    second = await run(fake, clock, config_dir, "q2", timeout_seconds=40)
    assert second == {"answered": False, "reason": "timeout"}
    assert [c.get("offset") for c in fake.calls_for("getUpdates")][-1] == fake.u["update_id"] + 1


async def test_ten_options_fit_callback_limit_and_callbacks_are_answered(fake, clock, config_dir) -> None:
    options = [f"opt{i}" for i in range(10)]

    def script(f):
        if f.messages and not f.d:
            f.d = True
            f.user_tap(f.sent_ids()[0], 9)

    fake.d = False
    fake.script = script
    result = await run(fake, clock, config_dir, "q", options=options)
    assert result["answer"] == "opt9"
    keyboard = fake.calls_for("sendMessage")[0]["reply_markup"]["inline_keyboard"]
    assert len(keyboard) == 10
    assert max(len(row[0]["callback_data"].encode()) for row in keyboard) <= 64
    assert len(fake.callback_answers) == 1


async def test_permanent_409_resolves_through_the_inbox(fake, clock, config_dir) -> None:
    for _ in range(100):
        fake.queue_response(
            "getUpdates", 409,
            {"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates request"},
        )
    inbox = Inbox(config_dir, clock)
    results: dict = {}

    async def other_process():
        while len(fake.messages) < 2:
            await anyio.sleep(0)
        sends = fake.calls_for("sendMessage")
        first_a = sends[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        second_y = sends[1]["reply_markup"]["inline_keyboard"][1][0]["callback_data"]
        inbox.store(
            [
                Incoming(update_id=7, chat_id="555", kind="callback", callback_id="c7", data=second_y),
                Incoming(update_id=8, chat_id="555", kind="callback", callback_id="c8", data=first_a),
            ],
            8,
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(other_process)
        await start_staggered(
            fake, clock, config_dir, tg, [("A", {"options": ["a", "b"]}), ("B", {"options": ["x", "y"]})], results
        )
    assert results["A"]["answer"] == "a" and results["B"]["answer"] == "y"
    assert sorted(fake.callback_answers) == ["c7", "c8"]
