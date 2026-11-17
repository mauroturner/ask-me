"""``ask-me setup``: interactive wizard that validates the bot and captures the chat id (S3)."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import TextIO

import httpx

from .clock import Clock, SystemClock
from .config import ENV_TOKEN, resolve_config_dir, save_config
from .inbox import Inbox
from .telegram import (
    DEFAULT_BASE_URL,
    TelegramApiError,
    TelegramClient,
    TelegramError,
    parse_update,
)

SETUP_WAIT_SECONDS = 120
POLL_SECONDS = 30


async def run_setup(
    env: Mapping[str, str],
    *,
    read_token: Callable[[str], str],
    stdout: TextIO,
    stderr: TextIO,
    transport: httpx.AsyncBaseTransport | None = None,
    base_url: str = DEFAULT_BASE_URL,
    clock: Clock | None = None,
) -> int:
    clock = clock or SystemClock()

    def say(message: str) -> None:
        print(message, file=stdout)

    def fail(message: str) -> int:
        print(f"ask-me setup: {message}", file=stderr)
        return 1

    token = (env.get(ENV_TOKEN) or "").strip() or read_token("Telegram bot token (from @BotFather): ").strip()
    if not token:
        return fail("no bot token provided.")

    async with TelegramClient(token, None, base_url=base_url, transport=transport, sleep=clock.sleep) as client:
        try:
            bot = await client.get_me()
        except TelegramApiError as exc:
            if exc.error_code == 401:
                return fail("invalid bot token: Telegram rejected it. Create one with @BotFather and try again.")
            return fail(str(exc))
        except TelegramError as exc:
            return fail(f"could not reach Telegram: {exc}")

        try:
            webhook = await client.get_webhook_info()
        except TelegramError as exc:
            return fail(str(exc))
        if webhook.get("url"):
            return fail(
                "this bot has a webhook set, so getUpdates cannot be used. Remove it first "
                "(call deleteWebhook for this bot) and run setup again."
            )

        username = bot.get("username", "<bot username>")
        say(f"Token OK: @{username}")
        say(f"Open https://t.me/{username} and send /start (waiting up to {SETUP_WAIT_SECONDS} s)...")

        chat_id: str | None = None
        last_update_id: int | None = None
        deadline = clock.now() + SETUP_WAIT_SECONDS
        offset: int | None = None
        while chat_id is None:
            remaining = deadline - clock.now()
            if remaining <= 0:
                return fail(f"timed out after {SETUP_WAIT_SECONDS} s without receiving a message. Run setup again.")
            try:
                raw = await client.get_updates_raw(offset, max(1, min(POLL_SECONDS, math.ceil(remaining))))
            except TelegramError as exc:
                return fail(str(exc))
            for update in raw:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    last_update_id = max(last_update_id or update_id, update_id)
                    offset = last_update_id + 1
                incoming = parse_update(update)
                if chat_id is None and incoming is not None and incoming.kind == "message" and incoming.chat_type == "private":
                    chat_id = incoming.chat_id

        confirmed = TelegramClient(token, chat_id, base_url=base_url, transport=transport, sleep=clock.sleep)
        async with confirmed:
            try:
                await confirmed.send_message("ask-me is set up ✅ You will get notifications and questions here.")
            except TelegramError as exc:
                return fail(f"could not send the confirmation message: {exc}")

    path = save_config(token, chat_id, env)
    if last_update_id is not None:
        # The /start we just consumed must not be replayed to a later `ask` poller.
        Inbox(resolve_config_dir(env), clock).confirm_through(last_update_id)
    say(f"Done. Config saved to {path}")
    say("Next: run `ask-me test` to check it end to end.")
    return 0
