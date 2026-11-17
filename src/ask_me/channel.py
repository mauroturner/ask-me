"""Channel abstraction (S0): the MVP ships Telegram only, but everything above this layer
(ask, notify, hook) talks to this small Protocol so another chat service can be added later."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

ELLIPSIS = "…"


def truncate(text: str, limit: int) -> str:
    """Cut ``text`` to at most ``limit`` characters, ending with ``…`` when something was removed."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + ELLIPSIS


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

    async def edit_message_text(self, message_id: int, text: str) -> None:
        """Replace the text of a sent message and remove its buttons."""
        ...

    async def answer_callback(self, callback_id: str) -> None: ...

    async def poll_updates(self, offset: int | None, timeout: int) -> PollResult:
        """Long-poll for new inbound events from the configured chat only."""
        ...
