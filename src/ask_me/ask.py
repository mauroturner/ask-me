"""``ask``: send a question, wait for the human's answer (S5)."""

from __future__ import annotations

import contextlib
import math
import random
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from .channel import MESSAGE_LIMIT, Channel, Incoming, truncate
from .clock import Clock, SystemClock
from .context import with_header
from .inbox import Inbox, InboxEntry, Pending
from .telegram import TelegramConflictError, TelegramError, TelegramNetworkError

MAX_OPTIONS = 10
MAX_TIMEOUT_SECONDS = 86400
POLL_SECONDS = 25
PROGRESS_INTERVAL_SECONDS = 60.0
# A plain message only answers a question if it was sent (roughly) after the question was.
CLOCK_SKEW_SECONDS = 5
CALLBACK_PREFIX = "am"

ProgressCallback = Callable[[float, float], Awaitable[None]]


@dataclass(frozen=True)
class AskResult:
    answered: bool
    answer: str | None = None
    via: Literal["button", "text"] | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        if self.answered:
            return {"answered": True, "answer": self.answer, "via": self.via}
        return {"answered": False, "reason": self.reason}


def format_question(question: str, header: str | None) -> str:
    return with_header(header, f"❓ {question}")


def _match(
    entry: InboxEntry,
    *,
    nonce: str,
    message_ids: list[int],
    options: list[str],
    allow_free_text: bool,
    created_at: float,
    others: list[Pending],
    is_newest: bool,
) -> tuple[str, Literal["button", "text"]] | None:
    """Return ``(answer, via)`` when this inbox entry answers *our* question."""
    incoming: Incoming = entry.incoming
    if incoming.kind == "callback":
        prefix = f"{CALLBACK_PREFIX}:{nonce}:"
        data = incoming.data or ""
        if not data.startswith(prefix):
            return None
        try:
            index = int(data[len(prefix) :])
        except ValueError:
            return None
        return (options[index], "button") if 0 <= index < len(options) else None

    if not allow_free_text or not incoming.text:
        return None
    if incoming.reply_to_message_id in message_ids:
        return incoming.text, "text"
    if incoming.reply_to_message_id is not None and any(
        other.owns(incoming.reply_to_message_id) for other in others
    ):
        return None  # a reply to somebody else's question
    # Plain message (or a reply to something that is not a question): newest pending question wins.
    if not is_newest:
        return None
    if incoming.date is not None and incoming.date < int(created_at) - CLOCK_SKEW_SECONDS:
        return None  # stale chatter from before this question existed
    return incoming.text, "text"


async def ask(
    channel: Channel,
    inbox: Inbox,
    question: str,
    options: list[str] | None = None,
    *,
    allow_free_text: bool = True,
    timeout_seconds: float = 1500,
    header: str | None = None,
    clock: Clock | None = None,
    rng: random.Random | None = None,
    on_progress: ProgressCallback | None = None,
    progress_interval: float = PROGRESS_INTERVAL_SECONDS,
    poll_seconds: int = POLL_SECONDS,
) -> AskResult:
    clock = clock or SystemClock()
    rng = rng or random.Random()
    options = list(options or [])
    if not question.strip():
        raise ValueError("question must not be empty")
    if len(options) > MAX_OPTIONS:
        raise ValueError(f"at most {MAX_OPTIONS} options are supported")
    if any(not option.strip() for option in options):
        raise ValueError("options must not be empty strings")
    if not options and not allow_free_text:
        raise ValueError("a question without options needs allow_free_text")
    timeout = max(1.0, min(float(timeout_seconds), float(MAX_TIMEOUT_SECONDS)))

    inbox.prune()
    nonce = secrets.token_hex(4)
    created_at = clock.now()
    deadline = created_at + timeout
    text = format_question(question, header)
    buttons = [(option, f"{CALLBACK_PREFIX}:{nonce}:{index}") for index, option in enumerate(options)]
    sent = await channel.send_parts(text, buttons=buttons or None, force_reply=not buttons)
    message_id = sent.ids[-1]
    inbox.register(Pending(nonce, message_id, created_at, deadline, allow_free_text, list(sent.ids)))

    try:
        outcome = await _wait_for_answer(
            channel,
            inbox,
            nonce=nonce,
            message_ids=sent.ids,
            options=options,
            allow_free_text=allow_free_text,
            created_at=created_at,
            deadline=deadline,
            clock=clock,
            rng=rng,
            on_progress=on_progress,
            progress_interval=progress_interval,
            poll_seconds=poll_seconds,
        )
    finally:
        inbox.unregister(nonce)

    if outcome is None:
        await _edit_quietly(channel, message_id, _with_status(sent.last_text, "⌛ expired"))
        return AskResult(answered=False, reason="timeout")
    answer, via = outcome
    await _edit_quietly(channel, message_id, _with_status(sent.last_text, f"✅ {answer}"))
    return AskResult(answered=True, answer=answer, via=via)


def _with_status(last_part: str, status: str) -> str:
    """Append the status line to the last part, shortening the part so the edit still fits one message."""
    suffix = truncate(f"\n\n{status}", MESSAGE_LIMIT // 4)
    return truncate(last_part, MESSAGE_LIMIT - len(suffix)) + suffix


async def _edit_quietly(channel: Channel, message_id: int, text: str) -> None:
    # The answer is already decided; failing to restyle the message must not lose it.
    with contextlib.suppress(TelegramError):
        await channel.edit_message_text(message_id, text)


async def _wait_for_answer(
    channel: Channel,
    inbox: Inbox,
    *,
    nonce: str,
    message_ids: list[int],
    options: list[str],
    allow_free_text: bool,
    created_at: float,
    deadline: float,
    clock: Clock,
    rng: random.Random,
    on_progress: ProgressCallback | None,
    progress_interval: float,
    poll_seconds: int,
) -> tuple[str, Literal["button", "text"]] | None:
    last_progress = created_at
    total = deadline - created_at
    while True:
        found = await _claim_answer(
            channel,
            inbox,
            nonce=nonce,
            message_ids=message_ids,
            options=options,
            allow_free_text=allow_free_text,
            created_at=created_at,
        )
        if found is not None:
            return found

        now = clock.now()
        if now >= deadline:
            return None
        if on_progress is not None and now - last_progress >= progress_interval:
            last_progress = now
            await on_progress(now - created_at, total)

        poll_timeout = max(1, min(poll_seconds, math.ceil(deadline - now)))
        try:
            result = await channel.poll_updates(inbox.read_offset(), poll_timeout)
        except TelegramConflictError:
            # Another process owns the long poll: it will write updates to the inbox. Back off, re-read.
            await clock.sleep(rng.uniform(0.5, 2.0))
            continue
        except TelegramNetworkError:
            await clock.sleep(rng.uniform(2.0, 5.0))
            continue
        inbox.store(result.updates, result.last_update_id)


async def _claim_answer(
    channel: Channel,
    inbox: Inbox,
    *,
    nonce: str,
    message_ids: list[int],
    options: list[str],
    allow_free_text: bool,
    created_at: float,
) -> tuple[str, Literal["button", "text"]] | None:
    entries = inbox.entries()
    if not entries:
        return None
    pending = inbox.pending()
    others = [p for p in pending if p.nonce != nonce]
    candidates = [p for p in pending if p.allow_free_text]
    newest = max(candidates, key=lambda p: (p.created_at, p.nonce), default=None)
    is_newest = newest is not None and newest.nonce == nonce

    for entry in entries:
        matched = _match(
            entry,
            nonce=nonce,
            message_ids=message_ids,
            options=options,
            allow_free_text=allow_free_text,
            created_at=created_at,
            others=others,
            is_newest=is_newest,
        )
        if matched is None or not inbox.claim(entry):
            continue
        if entry.incoming.kind == "callback" and entry.incoming.callback_id:
            with contextlib.suppress(TelegramError):
                await channel.answer_callback(entry.incoming.callback_id)
        return matched
    return None
