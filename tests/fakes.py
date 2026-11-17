"""Test doubles: a fake clock and a fake Telegram Bot API served via httpx.MockTransport."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import anyio
import httpx

START_TIME = 1_700_000_000.0
# Number of event-loop yields a getUpdates call stays "in flight"; lets concurrent callers overlap.
POLL_YIELDS = 8


class FakeClock:
    """Virtual time: sleeping advances the clock instantly (after yielding to other tasks)."""

    def __init__(self, start: float = START_TIME) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds
        await anyio.sleep(0)


class FakeTelegram:
    """Records every Bot API call and serves scripted responses and updates."""

    def __init__(
        self,
        *,
        token: str = "123456:SECRET-TOKEN",
        chat_id: int = 555,
        clock: FakeClock | None = None,
    ) -> None:
        self.token = token
        self.chat_id = chat_id
        self.clock = clock
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages: dict[int, dict[str, Any]] = {}
        self.callback_answers: list[str] = []
        self.conflicts = 0
        self.webhook_url = ""
        self.username = "ask_me_test_bot"
        self.script: Callable[[FakeTelegram], None] | None = None
        self._overrides: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        self._updates: list[dict[str, Any]] = []
        self._next_update_id = 100
        self._next_message_id = 1000
        self._polling = False

    # ---- wiring -------------------------------------------------------------------------
    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handler)

    def calls_for(self, method: str) -> list[dict[str, Any]]:
        return [payload for name, payload in self.calls if name == method]

    def queue_response(self, method: str, status: int, body: dict[str, Any]) -> None:
        self._overrides.setdefault(method, []).append((status, body))

    # ---- simulated user actions --------------------------------------------------------
    def _now(self) -> int:
        return int(self.clock.now()) if self.clock else 1_700_000_000

    def _new_update(self, **fields: Any) -> dict[str, Any]:
        update = {"update_id": self._next_update_id, **fields}
        self._next_update_id += 1
        self._updates.append(update)
        return update

    def user_text(
        self, text: str, *, chat_id: int | None = None, reply_to: int | None = None, chat_type: str = "private"
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "message_id": self._next_message_id,
            "date": self._now(),
            "chat": {"id": chat_id if chat_id is not None else self.chat_id, "type": chat_type},
            "text": text,
        }
        self._next_message_id += 1
        if reply_to is not None:
            message["reply_to_message"] = {"message_id": reply_to}
        return self._new_update(message=message)

    def user_tap(self, message_id: int, index: int, *, chat_id: int | None = None) -> dict[str, Any]:
        keyboard = self.messages[message_id]["reply_markup"]["inline_keyboard"]
        data = keyboard[index][0]["callback_data"]
        return self._new_update(
            callback_query={
                "id": f"cb{self._next_update_id}",
                "data": data,
                "message": {
                    "message_id": message_id,
                    "chat": {"id": chat_id if chat_id is not None else self.chat_id, "type": "private"},
                },
            }
        )

    def sent_ids(self) -> list[int]:
        return list(self.messages)

    # ---- HTTP handler ---------------------------------------------------------------------
    async def _handler(self, request: httpx.Request) -> httpx.Response:
        match = re.fullmatch(r"/bot([^/]+)/(\w+)", request.url.path)
        if not match:
            return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found"})
        token, method = match.groups()
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        if token != self.token:
            return httpx.Response(401, json={"ok": False, "error_code": 401, "description": "Unauthorized"})
        queued = self._overrides.get(method)
        if queued:
            status, body = queued.pop(0)
            return httpx.Response(status, json=body)
        return await self._dispatch(method, payload)

    @staticmethod
    def _ok(result: Any) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": result})

    async def _dispatch(self, method: str, payload: dict[str, Any]) -> httpx.Response:
        if method == "getMe":
            return self._ok({"id": int(self.token.split(":")[0]), "is_bot": True, "username": self.username})
        if method == "getWebhookInfo":
            return self._ok({"url": self.webhook_url})
        if method == "sendMessage":
            message_id = self._next_message_id
            self._next_message_id += 1
            self.messages[message_id] = {
                "text": payload.get("text"),
                "reply_markup": payload.get("reply_markup"),
            }
            return self._ok({"message_id": message_id, "chat": {"id": payload.get("chat_id")}})
        if method in ("editMessageText", "editMessageReplyMarkup"):
            record = self.messages.setdefault(payload["message_id"], {})
            if "text" in payload:
                record["text"] = payload["text"]
            record["reply_markup"] = payload.get("reply_markup")
            return self._ok(True)
        if method == "answerCallbackQuery":
            self.callback_answers.append(payload["callback_query_id"])
            return self._ok(True)
        if method == "getUpdates":
            return await self._get_updates(payload)
        return httpx.Response(404, json={"ok": False, "error_code": 404, "description": "Not Found"})

    async def _get_updates(self, payload: dict[str, Any]) -> httpx.Response:
        offset = payload.get("offset")
        if offset is not None:
            self._updates = [u for u in self._updates if u["update_id"] >= offset]
        if self._polling:
            self.conflicts += 1
            return httpx.Response(
                409,
                json={
                    "ok": False,
                    "error_code": 409,
                    "description": "Conflict: terminated by other getUpdates request",
                },
            )
        self._polling = True
        try:
            for _ in range(POLL_YIELDS):
                await anyio.sleep(0)
            if self.script is not None:
                self.script(self)
            result = list(self._updates)
            if not result and self.clock is not None:
                self.clock.advance(float(payload.get("timeout", 0)))
            return self._ok(result)
        finally:
            self._polling = False
