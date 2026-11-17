from __future__ import annotations

import json
import random
from pathlib import Path

import anyio
import pytest
from fakes import FakeClock, FakeTelegram

from ask_me.ask import MAX_OPTIONS, ask
from ask_me.inbox import Inbox, Pending
from ask_me.channel import Incoming
from ask_me.telegram import TelegramApiError, TelegramClient

pytestmark = pytest.mark.anyio


def make_channel(fake: FakeTelegram, clock: FakeClock) -> TelegramClient:
    return TelegramClient(
        fake.token,
        str(fake.chat_id),
        transport=fake.transport,
        base_url="https://telegram.test",
        sleep=clock.sleep,
    )


async def run_ask(fake: FakeTelegram, clock: FakeClock, config_dir: Path, question: str, **kwargs) -> dict:
    kwargs.setdefault("timeout_seconds", 1500)
    kwargs.setdefault("rng", random.Random(0))
    async with make_channel(fake, clock) as channel:
        result = await ask(channel, Inbox(config_dir, clock), question, clock=clock, **kwargs)
    return result.to_dict()


def files(config_dir: Path, sub: str) -> list[str]:
    folder = config_dir / sub
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def first_message_id(fake: FakeTelegram) -> int:
    return fake.sent_ids()[0]


# ---- the three ways to answer ----------------------------------------------------------------
async def test_ask_answered_via_button(fake, clock, config_dir) -> None:
    def script(f: FakeTelegram) -> None:
        if f.messages and not f.script_done:
            f.script_done = True
            f.user_tap(first_message_id(f), 1)

    fake.script_done = False
    fake.script = script
    result = await run_ask(fake, clock, config_dir, "Deploy now?", options=["Yes", "No"], project="my-app")
    assert result == {"answered": True, "answer": "No", "via": "button"}

    (sent,) = fake.calls_for("sendMessage")
    assert sent["text"] == "❓ (my-app) Deploy now?"
    keyboard = sent["reply_markup"]["inline_keyboard"]
    assert [row[0]["text"] for row in keyboard] == ["Yes", "No"]
    for row in keyboard:
        data = row[0]["callback_data"]
        assert data.startswith("am:") and data.count(":") == 2 and len(data.encode()) <= 64
    assert len(fake.callback_answers) == 1
    # message edited to show the answer, buttons gone
    (edit,) = fake.calls_for("editMessageText")
    assert "✅ No" in edit["text"]
    assert "reply_markup" not in edit
    # registry cleaned up
    assert files(config_dir, "pending") == []
    assert files(config_dir, "inbox") == []


async def test_ask_answered_via_reply_to_the_question(fake, clock, config_dir) -> None:
    def script(f: FakeTelegram) -> None:
        if f.messages and not f.script_done:
            f.script_done = True
            f.user_text("use the blue one", reply_to=first_message_id(f))

    fake.script_done = False
    fake.script = script
    result = await run_ask(fake, clock, config_dir, "Which colour?")
    assert result == {"answered": True, "answer": "use the blue one", "via": "text"}
    (sent,) = fake.calls_for("sendMessage")
    # no options -> force_reply so the phone opens the reply box
    assert sent["reply_markup"] == {"force_reply": True}
    assert "✅ use the blue one" in fake.calls_for("editMessageText")[0]["text"]


async def test_ask_answered_via_plain_text_message(fake, clock, config_dir) -> None:
    def script(f: FakeTelegram) -> None:
        if f.messages and not f.script_done:
            f.script_done = True
            f.user_text("just do it")

    fake.script_done = False
    fake.script = script
    result = await run_ask(fake, clock, config_dir, "Proceed?", options=["Yes", "No"])
    assert result == {"answered": True, "answer": "just do it", "via": "text"}


async def test_plain_text_is_ignored_when_free_text_is_not_allowed(fake, clock, config_dir) -> None:
    def script(f: FakeTelegram) -> None:
        if f.messages and not f.script_done:
            f.script_done = True
            f.user_text("free text nobody asked for")
            f.user_tap(first_message_id(f), 0)

    fake.script_done = False
    fake.script = script
    result = await run_ask(fake, clock, config_dir, "Proceed?", options=["Yes", "No"], allow_free_text=False)
    assert result == {"answered": True, "answer": "Yes", "via": "button"}


async def test_message_sent_before_the_question_is_not_an_answer(fake, clock, config_dir) -> None:
    fake.user_text("old chatter that arrived while nobody was asking")
    clock.advance(600)
    result = await run_ask(fake, clock, config_dir, "Proceed?", options=["Yes"], timeout_seconds=30)
    assert result == {"answered": False, "reason": "timeout"}


async def test_foreign_chat_cannot_answer(fake, clock, config_dir) -> None:
    def script(f: FakeTelegram) -> None:
        if f.messages and not f.script_done:
            f.script_done = True
            f.user_text("i am an intruder", chat_id=999, reply_to=first_message_id(f))
            f.user_tap(first_message_id(f), 0, chat_id=999)

    fake.script_done = False
    fake.script = script
    result = await run_ask(fake, clock, config_dir, "Proceed?", options=["Yes"], timeout_seconds=30)
    assert result == {"answered": False, "reason": "timeout"}
    assert fake.callback_answers == []


# ---- timeout ------------------------------------------------------------------------------------
async def test_timeout_edits_the_message_to_expired(fake, clock, config_dir) -> None:
    result = await run_ask(fake, clock, config_dir, "Proceed?", options=["Yes", "No"], timeout_seconds=60)
    assert result == {"answered": False, "reason": "timeout"}
    (edit,) = fake.calls_for("editMessageText")
    assert "⌛ expired" in edit["text"]
    assert "reply_markup" not in edit
    assert clock.now() >= 1_700_000_000.0 + 60
    assert files(config_dir, "pending") == []


async def test_timeout_is_capped_at_24_hours(fake, clock, config_dir) -> None:
    start = clock.now()
    await run_ask(fake, clock, config_dir, "Proceed?", options=["Yes"], timeout_seconds=10**9)
    assert 86400 <= clock.now() - start < 86400 + 60


# ---- progress for the MCP idle timeout -------------------------------------------------------
async def test_progress_is_reported_every_60_seconds_while_waiting(fake, clock, config_dir) -> None:
    reports: list[tuple[float, float]] = []

    async def on_progress(elapsed: float, total: float) -> None:
        reports.append((elapsed, total))

    await run_ask(
        fake, clock, config_dir, "Proceed?", options=["Yes"], timeout_seconds=300, on_progress=on_progress
    )
    assert len(reports) >= 3
    elapsed = [r[0] for r in reports]
    assert elapsed == sorted(elapsed) and len(set(elapsed)) == len(elapsed)
    assert all(b - a >= 60 for a, b in zip(elapsed, elapsed[1:]))
    assert all(total == 300 for _, total in reports)


# ---- shared inbox: concurrency -------------------------------------------------------------
async def test_two_concurrent_asks_each_get_their_own_answer(fake, clock, config_dir) -> None:
    def script(f: FakeTelegram) -> None:
        if len(f.messages) >= 2 and not f.script_done:
            f.script_done = True
            first, second = f.sent_ids()
            f.user_tap(second, 0)  # answers the *second* ask: "x"
            f.user_tap(first, 1)  # answers the *first* ask: "b"

    fake.script_done = False
    fake.script = script
    results: dict[str, dict] = {}

    async def one(name: str, options: list[str]) -> None:
        results[name] = await run_ask(fake, clock, config_dir, f"question {name}", options=options)

    async with anyio.create_task_group() as tg:
        tg.start_soon(one, "A", ["a", "b"])
        tg.start_soon(one, "B", ["x", "y"])

    assert results["A"] == {"answered": True, "answer": "b", "via": "button"}
    assert results["B"] == {"answered": True, "answer": "x", "via": "button"}
    # one process held the long poll; the other got HTTP 409 and fell back to the shared inbox
    assert fake.conflicts >= 1
    assert len(fake.callback_answers) == 2
    assert files(config_dir, "pending") == []
    assert files(config_dir, "inbox") == []


async def test_plain_text_goes_to_the_newest_pending_question_only(fake, clock, config_dir) -> None:
    def script(f: FakeTelegram) -> None:
        if len(f.messages) >= 2 and not f.script_done:
            f.script_done = True
            f.user_text("for the newest")
        if f.script_done and f.calls_for("editMessageText") and not f.second_done:
            f.second_done = True
            f.user_text("for the older one", reply_to=f.sent_ids()[0])

    fake.script_done = False
    fake.second_done = False
    fake.script = script
    results: dict[str, dict] = {}

    async def one(name: str) -> None:
        results[name] = await run_ask(fake, clock, config_dir, f"question {name}")

    async with anyio.create_task_group() as tg:
        tg.start_soon(one, "older")
        while not fake.messages:
            await anyio.sleep(0)
        clock.advance(5)
        tg.start_soon(one, "newer")

    assert results["newer"]["answer"] == "for the newest"
    assert results["older"]["answer"] == "for the older one"


async def test_http_409_backs_off_then_reads_the_inbox_again(fake, clock, config_dir) -> None:
    fake.queue_response("getUpdates", 409, {"ok": False, "error_code": 409, "description": "Conflict: terminated"})

    def script(f: FakeTelegram) -> None:
        if f.messages and not f.script_done:
            f.script_done = True
            f.user_tap(first_message_id(f), 0)

    fake.script_done = False
    fake.script = script
    result = await run_ask(fake, clock, config_dir, "Proceed?", options=["Yes"])
    assert result["answer"] == "Yes"
    assert len(clock.sleeps) == 1 and 0.5 <= clock.sleeps[0] <= 2.0


async def test_answer_arriving_through_the_inbox_while_another_process_polls(fake, clock, config_dir) -> None:
    """Another process holds the long poll (we only ever get 409) and stores the callback in the inbox."""
    inbox = Inbox(config_dir, clock)
    for _ in range(50):
        fake.queue_response("getUpdates", 409, {"ok": False, "error_code": 409, "description": "Conflict"})

    async def other_process_stores_answer() -> None:
        while not fake.messages:
            await anyio.sleep(0)
        data = fake.calls_for("sendMessage")[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        inbox.store(
            [Incoming(update_id=7, chat_id=str(fake.chat_id), kind="callback", callback_id="cb7", data=data)],
            7,
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(other_process_stores_answer)
        result = await run_ask(fake, clock, config_dir, "Proceed?", options=["Yes"])
    assert result == {"answered": True, "answer": "Yes", "via": "button"}
    assert fake.callback_answers == ["cb7"]


# ---- validation -------------------------------------------------------------------------------
async def test_more_than_ten_options_is_rejected(fake, clock, config_dir) -> None:
    with pytest.raises(ValueError):
        await run_ask(fake, clock, config_dir, "q", options=[str(i) for i in range(MAX_OPTIONS + 1)])
    assert fake.calls == []


async def test_no_options_and_no_free_text_is_rejected(fake, clock, config_dir) -> None:
    with pytest.raises(ValueError):
        await run_ask(fake, clock, config_dir, "q", allow_free_text=False)


async def test_webhook_conflict_is_an_error_not_a_backoff(fake, clock, config_dir) -> None:
    fake.queue_response(
        "getUpdates",
        409,
        {"ok": False, "error_code": 409, "description": "Conflict: can't use getUpdates method while webhook is active"},
    )
    with pytest.raises(TelegramApiError):
        await run_ask(fake, clock, config_dir, "q", options=["Yes"])
    assert files(config_dir, "pending") == []


# ---- inbox primitives ---------------------------------------------------------------------------
def test_claim_is_exclusive(config_dir, clock) -> None:
    inbox = Inbox(config_dir, clock)
    inbox.store([Incoming(update_id=1, chat_id="1", kind="message", text="hi")], 1)
    (entry,) = inbox.entries()
    assert inbox.claim(entry) is True
    assert inbox.claim(entry) is False
    assert inbox.entries() == []


def test_store_persists_offset_and_skips_confirmed_updates(config_dir, clock) -> None:
    inbox = Inbox(config_dir, clock)
    assert inbox.read_offset() is None
    inbox.store([Incoming(update_id=10, chat_id="1", kind="message", text="a")], 10)
    assert inbox.read_offset() == 11
    assert (config_dir / "state" / "offset").read_text(encoding="utf-8").strip() == "11"
    # a redelivery of an already confirmed update is not stored again
    (entry,) = inbox.entries()
    inbox.claim(entry)
    inbox.store([Incoming(update_id=10, chat_id="1", kind="message", text="a")], 10)
    assert inbox.entries() == []


def test_foreign_update_ids_still_advance_the_offset(config_dir, clock) -> None:
    inbox = Inbox(config_dir, clock)
    inbox.store([], 55)
    assert inbox.read_offset() == 56


def test_inbox_files_older_than_24h_are_pruned(config_dir, clock) -> None:
    inbox = Inbox(config_dir, clock)
    inbox.store([Incoming(update_id=1, chat_id="1", kind="message", text="old")], 1)
    clock.advance(86400 - 10)
    inbox.store([Incoming(update_id=2, chat_id="1", kind="message", text="fresh")], 2)
    clock.advance(20)
    inbox.prune()
    assert [e.update_id for e in inbox.entries()] == [2]


def test_expired_pending_registrations_are_ignored(config_dir, clock) -> None:
    inbox = Inbox(config_dir, clock)
    now = clock.now()
    inbox.register(Pending("live", 1, now, now + 100, True))
    inbox.register(Pending("dead", 2, now - 500, now - 1, True))
    assert [p.nonce for p in inbox.pending()] == ["live"]


def test_state_files_are_valid_json_or_text(config_dir, clock) -> None:
    inbox = Inbox(config_dir, clock)
    inbox.store([Incoming(update_id=3, chat_id="1", kind="message", text="x")], 3)
    stored = json.loads((config_dir / "inbox" / "3.json").read_text(encoding="utf-8"))
    assert stored["incoming"]["text"] == "x"
