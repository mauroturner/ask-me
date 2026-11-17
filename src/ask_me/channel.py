"""Channel abstraction (S0): the MVP ships Telegram only, but everything above this layer
(ask, notify, hook) talks to this small Protocol so another chat service can be added later."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

ELLIPSIS = "…"
MESSAGE_LIMIT = 4096
DEFAULT_MAX_MESSAGE_PARTS = 5


def truncate(text: str, limit: int) -> str:
    """Cut ``text`` to at most ``limit`` characters, ending with ``…`` when something was removed."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + ELLIPSIS


def _marker(index: int, total: int) -> str:
    return f"({index}/{total}) "


def _split_point(text: str, budget: int) -> int:
    """Where to cut ``text`` so the first piece has at most ``budget`` characters.

    Prefers the last paragraph break, then the last line break, then the last space, else a hard cut.
    The separator stays at the end of the first piece, so joining the pieces restores the text. Breaks in
    the first quarter of the window are skipped, so a short leading line (such as the context header) is
    never sent alone.
    """
    window = text[:budget]
    for separator in ("\n\n", "\n", " "):
        index = window.rfind(separator)
        cut = index + len(separator)
        if index >= budget // 4 and window[:cut].strip():
            return cut
    return budget


def split_message(
    text: str, limit: int = MESSAGE_LIMIT, max_parts: int = DEFAULT_MAX_MESSAGE_PARTS
) -> list[str]:
    """Split ``text`` into consecutive messages of at most ``limit`` characters.

    Parts after the first start with ``(k/n) ``. At most ``max_parts`` parts are produced; when the text
    does not fit, the last part ends with ``… (truncated, <N> more characters)``.
    """
    max_parts = max(1, max_parts)
    if len(text) <= limit:
        return [text]
    marker_room = len(_marker(max_parts, max_parts))
    note_room = len(f"{ELLIPSIS} (truncated, {len(text)} more characters)")
    chunks: list[str] = []
    note = ""
    remaining = text
    while True:
        room = limit - (marker_room if chunks else 0)
        if len(remaining) <= room:
            chunks.append(remaining)
            break
        if len(chunks) == max_parts - 1:  # the last part we may send
            cut = _split_point(remaining, room - note_room)
            kept = remaining[:cut].rstrip()
            note = f"{ELLIPSIS} (truncated, {len(remaining) - len(kept)} more characters)"
            chunks.append(kept + note)
            break
        cut = _split_point(remaining, room)
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
        if not remaining.strip():
            break
    total = len(chunks)
    return [chunk if index == 0 else _marker(index + 1, total) + chunk for index, chunk in enumerate(chunks)]


@dataclass(frozen=True)
class SentMessage:
    """What a send produced: every part's message id (in order) and the text of the last part."""

    ids: list[int]
    last_text: str


@dataclass(frozen=True)
class Incoming:
    """A channel-neutral inbound event from the configured chat: a button press or a text message."""

    update_id: int
    chat_id: str
    kind: Literal["callback", "message"]
    chat_type: str | None = None
    text: str | None = None
    message_id: int | None = None
    reply_to_message_id: int | None = None
    callback_id: str | None = None
    data: str | None = None
    date: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Incoming:
        return cls(**{key: raw.get(key) for key in cls.__dataclass_fields__ if key in raw})


@dataclass(frozen=True)
class PollResult:
    updates: list[Incoming] = field(default_factory=list)
    # Highest update id seen, including ignored foreign ones, so the caller can confirm them.
    last_update_id: int | None = None


class Channel(Protocol):
    async def send_message(
        self,
        text: str,
        *,
        buttons: Sequence[tuple[str, str]] | None = None,
        force_reply: bool = False,
    ) -> int:
        """Send ``text``; ``buttons`` are ``(label, callback_data)`` pairs. Returns the message id."""
        ...

    async def send_parts(
        self,
        text: str,
        *,
        buttons: Sequence[tuple[str, str]] | None = None,
        force_reply: bool = False,
    ) -> SentMessage:
        """Like ``send_message``, but long text becomes several messages; markup goes on the last one."""
        ...

    async def edit_message_text(self, message_id: int, text: str) -> None:
        """Replace the text of a sent message and remove its buttons."""
        ...

    async def answer_callback(self, callback_id: str) -> None: ...

    async def poll_updates(self, offset: int | None, timeout: int) -> PollResult:
        """Long-poll for new inbound events from the configured chat only."""
        ...
