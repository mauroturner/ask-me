"""Async Telegram Bot API client (S4). Plain HTTPS + JSON via httpx; transport and base URL are injectable."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from types import TracebackType
from typing import Any

import httpx

from .channel import Incoming, PollResult, truncate

DEFAULT_BASE_URL = "https://api.telegram.org"
MESSAGE_LIMIT = 4096
MAX_RATE_LIMIT_RETRIES = 3
POLL_ALLOWED_UPDATES = ["message", "callback_query"]


class TelegramError(Exception):
    """Base class for every error raised by this client."""


class TelegramNetworkError(TelegramError):
    """The request never produced a Telegram answer (DNS, connection, timeout...)."""


class TelegramApiError(TelegramError):
    """Telegram answered ``ok: false``."""

    def __init__(self, method: str, error_code: int | None, description: str) -> None:
        super().__init__(f"{method} failed ({error_code}): {description}")
        self.method = method
        self.error_code = error_code
        self.description = description


class TelegramRateLimitError(TelegramApiError):
    """HTTP 429 persisted after the allowed retries."""


class TelegramConflictError(TelegramApiError):
    """HTTP 409: another process is already long-polling ``getUpdates`` for this bot."""


def parse_update(update: dict[str, Any]) -> Incoming | None:
    """Normalize a raw Bot API update; returns None for kinds ask-me does not use."""
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return None
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        message = callback.get("message")
        if not isinstance(message, dict) or not isinstance(callback.get("data"), str):
            return None
        chat = message.get("chat") or {}
        return Incoming(
            update_id=update_id,
            chat_id=str(chat.get("id")),
            chat_type=chat.get("type"),
            kind="callback",
            message_id=message.get("message_id"),
            callback_id=str(callback.get("id")),
            data=callback["data"],
        )
    message = update.get("message")
    if isinstance(message, dict) and isinstance(message.get("text"), str):
        chat = message.get("chat") or {}
        reply_to = message.get("reply_to_message")
        return Incoming(
            update_id=update_id,
            chat_id=str(chat.get("id")),
            chat_type=chat.get("type"),
            kind="message",
            text=message["text"],
            message_id=message.get("message_id"),
            reply_to_message_id=reply_to.get("message_id") if isinstance(reply_to, dict) else None,
            date=message.get("date") if isinstance(message.get("date"), int) else None,
        )
    return None


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 30.0,
        max_retries: int = MAX_RATE_LIMIT_RETRIES,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._timeout = timeout
        self._max_retries = max_retries
        self._http = httpx.AsyncClient(transport=transport, timeout=timeout)

    async def __aenter__(self) -> TelegramClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, "<token>")

    async def _call(
        self, method: str, payload: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        url = f"{self._base_url}/bot{self._token}/{method}"
        retries = 0
        while True:
            try:
                response = await self._http.post(url, json=payload or {}, timeout=timeout or self._timeout)
            except httpx.HTTPError as exc:
                # `from None`: the original exception may embed the URL, which contains the token.
                raise TelegramNetworkError(
                    f"{method}: {type(exc).__name__}: {self._scrub(str(exc))}"
                ) from None
            try:
                body = response.json()
            except ValueError:
                raise TelegramApiError(method, response.status_code, "response was not valid JSON") from None
            if not isinstance(body, dict):
                raise TelegramApiError(method, response.status_code, "unexpected response shape")
            if body.get("ok"):
                return body.get("result")

            code = body.get("error_code", response.status_code)
            description = self._scrub(str(body.get("description", "")))
            if code == 429:
                if retries >= self._max_retries:
                    raise TelegramRateLimitError(method, code, description)
                retries += 1
                retry_after = (body.get("parameters") or {}).get("retry_after", 1)
                await self._sleep(float(retry_after) if isinstance(retry_after, (int, float)) else 1.0)
                continue
            # A 409 caused by a webhook is a configuration problem, not another poller.
            if code == 409 and "webhook" not in description.lower():
                raise TelegramConflictError(method, code, description)
            raise TelegramApiError(method, code, description)

    # ---- setup helpers ------------------------------------------------------------------
    async def get_me(self) -> dict[str, Any]:
        return await self._call("getMe")

    async def get_webhook_info(self) -> dict[str, Any]:
        return await self._call("getWebhookInfo")

    async def get_updates_raw(self, offset: int | None, timeout: int) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": POLL_ALLOWED_UPDATES}
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload, timeout=timeout + 15)
        return result if isinstance(result, list) else []

    # ---- Channel ------------------------------------------------------------------------
    def _require_chat(self) -> str:
        if not self._chat_id:
            raise TelegramError("no chat id configured")
        return self._chat_id

    async def send_message(
        self,
        text: str,
        *,
        buttons: Sequence[tuple[str, str]] | None = None,
        force_reply: bool = False,
    ) -> int:
        payload: dict[str, Any] = {"chat_id": self._require_chat(), "text": truncate(text, MESSAGE_LIMIT)}
        # A message carries a single reply_markup, so buttons and force_reply are exclusive.
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": label, "callback_data": data}] for label, data in buttons]
            }
        elif force_reply:
            payload["reply_markup"] = {"force_reply": True}
        result = await self._call("sendMessage", payload)
        return int(result["message_id"])

    async def edit_message_text(self, message_id: int, text: str) -> None:
        # Omitting reply_markup removes the inline keyboard.
        await self._call(
            "editMessageText",
            {"chat_id": self._require_chat(), "message_id": message_id, "text": truncate(text, MESSAGE_LIMIT)},
        )

    async def edit_message_reply_markup(self, message_id: int) -> None:
        """Remove the inline keyboard from a message, leaving its text untouched."""
        await self._call(
            "editMessageReplyMarkup",
            {"chat_id": self._require_chat(), "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )

    async def answer_callback(self, callback_id: str) -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id})

    async def poll_updates(self, offset: int | None, timeout: int) -> PollResult:
        chat_id = self._require_chat()
        raw = await self.get_updates_raw(offset, timeout)
        update_ids = [u["update_id"] for u in raw if isinstance(u.get("update_id"), int)]
        updates = [
            incoming
            for incoming in (parse_update(u) for u in raw)
            if incoming is not None and incoming.chat_id == chat_id
        ]
        return PollResult(updates=updates, last_update_id=max(update_ids) if update_ids else None)
