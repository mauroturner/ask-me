"""``ask-me hook``: Claude Code hook entry point (S7).

Contract: always exit 0 and never write to stdout, so Claude's behaviour is never blocked or altered.
Diagnostics go to stderr.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any, TextIO

import httpx

from .config import Config, NotConfiguredError, is_muted, load_config
from .context import GitRunner, build_header, with_header
from .telegram import DEFAULT_BASE_URL, TelegramClient

HTTP_TIMEOUT_SECONDS = 10.0
TOTAL_TIMEOUT_SECONDS = 20.0


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def format_hook_message(
    payload: Mapping[str, Any],
    config: Config,
    env: Mapping[str, str] | None = None,
    git: GitRunner | None = None,
) -> str | None:
    """Return the text to send, or None when this event should be skipped."""
    event = payload.get("hook_event_name")
    if not isinstance(event, str) or event not in config.hook_events:
        return None

    def with_context(body: str) -> str:
        header = build_header(payload.get("cwd"), env, session=payload.get("session_id"), git=git)
        return with_header(header, body)

    if event == "Notification":
        if config.notification_types is not None and payload.get("notification_type") not in config.notification_types:
            return None
        message = _text(payload.get("message"))
        return with_context("🔔 Claude needs you" + (f": {message}" if message else ""))

    if event == "Stop":
        if payload.get("stop_hook_active") is True:
            return None
        text = "✅ Claude finished"
        last = _text(payload.get("last_assistant_message"))
        if last:
            text += "\n\n" + last.strip()
        return with_context(text)

    return with_context(f"🔔 Claude event {event}")


async def _send(text: str, config: Config, transport: httpx.AsyncBaseTransport | None, base_url: str) -> None:
    async with TelegramClient(
        config.bot_token,
        config.chat_id,
        base_url=base_url,
        transport=transport,
        timeout=HTTP_TIMEOUT_SECONDS,
        max_parts=config.max_message_parts,
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
    git: GitRunner | None = None,
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
        text = format_hook_message(payload, config, env, git)
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
