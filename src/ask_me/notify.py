"""One-way notifications (S6, S8): level emoji, mute switch."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from .channel import Channel
from .config import is_muted

LEVEL_PREFIXES = {
    "info": "ℹ️",
    "success": "✅",
    "warning": "⚠️",
    "important": "🚨",
}
LEVELS = tuple(LEVEL_PREFIXES)


def format_notification(message: str, level: str = "info") -> str:
    if level not in LEVEL_PREFIXES:
        raise ValueError(f"level must be one of: {', '.join(LEVELS)}")
    return f"{LEVEL_PREFIXES[level]} {message}"


async def notify(
    channel: Channel,
    message: str,
    level: str = "info",
    env: Mapping[str, str] | None = None,
) -> Literal["sent", "muted"]:
    """Send ``message`` unless muted. Muting never affects ``ask``."""
    text = format_notification(message, level)
    if is_muted(env):
        return "muted"
    await channel.send_message(text)
    return "sent"
