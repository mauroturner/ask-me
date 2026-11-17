"""``ask-me hook``: Claude Code hook entry point (S7).

Contract: always exit 0 and never write to stdout, so Claude's behaviour is never blocked or altered.
Diagnostics go to stderr.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any, TextIO

import httpx

from .channel import truncate
from .config import Config, NotConfiguredError, is_muted, load_config
from .telegram import DEFAULT_BASE_URL, TelegramClient

STOP_EXCERPT_LIMIT = 800
HTTP_TIMEOUT_SECONDS = 10.0
TOTAL_TIMEOUT_SECONDS = 20.0


def project_name(cwd: Any) -> str | None:
    if not isinstance(cwd, str):
        return None
    segments = [segment for segment in re.split(r"[\\/]", cwd) if segment]
    return segments[-1] if segments else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def format_hook_message(payload: Mapping[str, Any], config: Config) -> str | None:
    """Return the text to send, or None when this event should be skipped."""
    event = payload.get("hook_event_name")
    if not isinstance(event, str) or event not in config.hook_events:
        return None
    project = project_name(payload.get("cwd"))
    where = f" ({project})" if project else ""

    if event == "Notification":
        if config.notification_types is not None and payload.get("notification_type") not in config.notification_types:
            return None
        message = _text(payload.get("message"))
        return f"🔔 Claude needs you{where}" + (f": {message}" if message else "")

    if event == "Stop":
        if payload.get("stop_hook_active") is True:
            return None
        text = f"✅ Claude finished{where}"
        last = _text(payload.get("last_assistant_message"))
        if last:
            text += "\n\n" + truncate(last.strip(), STOP_EXCERPT_LIMIT)
        return text

    return f"🔔 Claude event {event}{where}"


async def _send(text: str, config: Config, transport: httpx.AsyncBaseTransport | None, base_url: str) -> None:
    async with TelegramClient(
        config.bot_token,
        config.chat_id,
        base_url=base_url,
        transport=transport,
        timeout=HTTP_TIMEOUT_SECONDS,
    ) as client:
        await client.send_message(text)


def run_hook(
    raw_stdin: str,
    env: Mapping[str, str] | None,
    stderr: TextIO,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    base_url: str = DEFAULT_BASE_URL,
    total_timeout: float = TOTAL_TIMEOUT_SECONDS,
) -> int:
    """Process one hook invocation. Returns the exit code, which is always 0."""
    try:
        payload = json.loads(raw_stdin)
        if not isinstance(payload, dict):
            print("ask-me hook: ignoring input that is not a JSON object", file=stderr)
            return 0
        config = load_config(env)
        if is_muted(env):
            return 0
        text = format_hook_message(payload, config)
        if text is None:
            return 0

        async def deliver() -> None:
            await asyncio.wait_for(_send(text, config, transport, base_url), timeout=total_timeout)

        asyncio.run(deliver())
    except NotConfiguredError as exc:
        print(f"ask-me hook: {exc}", file=stderr)
    except ValueError as exc:  # invalid JSON
        print(f"ask-me hook: invalid hook input: {exc}", file=stderr)
    except asyncio.TimeoutError:
        print("ask-me hook: timed out sending the notification", file=stderr)
    except Exception as exc:  # never let anything escape: the hook must not affect Claude
        print(f"ask-me hook: {type(exc).__name__}: {exc}", file=stderr)
    return 0
