from __future__ import annotations

import httpx
import pytest
from fakes import FakeClock, FakeTelegram

from ask_me.telegram import (
    TelegramApiError,
    TelegramClient,
    TelegramConflictError,
    TelegramNetworkError,
    TelegramRateLimitError,
)

pytestmark = pytest.mark.anyio


def make_client(fake: FakeTelegram, clock: FakeClock, **kwargs) -> TelegramClient:
    return TelegramClient(
        fake.token,
        str(fake.chat_id),
        transport=fake.transport,
        base_url="https://telegram.test",
        sleep=clock.sleep,
        **kwargs,
    )


async def test_send_message_is_plain_text_to_the_configured_chat(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        message_id = await client.send_message("hello")
    (payload,) = fake.calls_for("sendMessage")
    assert payload == {"chat_id": str(fake.chat_id), "text": "hello"}
    assert "parse_mode" not in payload
    assert message_id in fake.messages


async def test_long_text_is_truncated_to_4096_chars_with_ellipsis(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        await client.send_message("x" * 5000)
    (payload,) = fake.calls_for("sendMessage")
    assert len(payload["text"]) == 4096
    assert payload["text"].endswith("…")
    assert payload["text"][:-1] == "x" * 4095


async def test_text_at_the_limit_is_not_truncated(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        await client.send_message("y" * 4096)
    assert fake.calls_for("sendMessage")[0]["text"] == "y" * 4096


async def test_buttons_become_one_inline_keyboard_row_each(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        await client.send_message("pick", buttons=[("Yes", "am:n:0"), ("No", "am:n:1")])
    (payload,) = fake.calls_for("sendMessage")
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "Yes", "callback_data": "am:n:0"}], [{"text": "No", "callback_data": "am:n:1"}]]
    }


async def test_force_reply_uses_force_reply_markup(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        await client.send_message("say something", force_reply=True)
    assert fake.calls_for("sendMessage")[0]["reply_markup"] == {"force_reply": True}


async def test_edit_message_text_drops_the_keyboard_and_truncates(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        message_id = await client.send_message("pick", buttons=[("Yes", "am:n:0")])
        await client.edit_message_text(message_id, "z" * 5000)
    (payload,) = fake.calls_for("editMessageText")
    assert payload["message_id"] == message_id
    assert len(payload["text"]) == 4096
    assert "reply_markup" not in payload
    assert fake.messages[message_id]["reply_markup"] is None


async def test_edit_message_reply_markup_removes_buttons(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        message_id = await client.send_message("pick", buttons=[("Yes", "am:n:0")])
        await client.edit_message_reply_markup(message_id)
    (payload,) = fake.calls_for("editMessageReplyMarkup")
    assert payload["reply_markup"] == {"inline_keyboard": []}


async def test_answer_callback_query(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        await client.answer_callback("cb1")
    assert fake.calls_for("answerCallbackQuery") == [{"callback_query_id": "cb1"}]


async def test_429_waits_retry_after_and_retries(fake, clock) -> None:
    body = {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 7}}
    fake.queue_response("sendMessage", 429, body)
    async with make_client(fake, clock) as client:
        await client.send_message("hello")
    assert clock.sleeps == [7]
    assert len(fake.calls_for("sendMessage")) == 2
    assert len(fake.messages) == 1


async def test_429_gives_up_after_three_retries(fake, clock) -> None:
    body = {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 1}}
    for _ in range(4):
        fake.queue_response("sendMessage", 429, body)
    async with make_client(fake, clock) as client:
        with pytest.raises(TelegramRateLimitError):
            await client.send_message("hello")
    assert len(fake.calls_for("sendMessage")) == 4
    assert clock.sleeps == [1, 1, 1]


async def test_network_error_is_typed_and_never_leaks_the_token(fake, clock) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    client = TelegramClient(
        fake.token, "555", transport=httpx.MockTransport(boom), base_url="https://telegram.test", sleep=clock.sleep
    )
    async with client:
        with pytest.raises(TelegramNetworkError) as excinfo:
            await client.send_message("hello")
    assert fake.token not in str(excinfo.value)


async def test_api_error_carries_code_and_description(fake, clock) -> None:
    fake.queue_response("sendMessage", 400, {"ok": False, "error_code": 400, "description": "Bad Request: nope"})
    async with make_client(fake, clock) as client:
        with pytest.raises(TelegramApiError) as excinfo:
            await client.send_message("hello")
    assert excinfo.value.error_code == 400
    assert "nope" in str(excinfo.value)


async def test_wrong_token_is_an_api_error_401(fake, clock) -> None:
    client = TelegramClient(
        "999:WRONG", "555", transport=fake.transport, base_url="https://telegram.test", sleep=clock.sleep
    )
    async with client:
        with pytest.raises(TelegramApiError) as excinfo:
            await client.get_me()
    assert excinfo.value.error_code == 401
    assert "WRONG" not in str(excinfo.value)


async def test_updates_from_foreign_chats_are_ignored_but_confirmed(fake, clock) -> None:
    fake.user_text("intruder", chat_id=999)
    mine = fake.user_text("hello bot")
    async with make_client(fake, clock) as client:
        result = await client.poll_updates(None, 0)
    assert [u.text for u in result.updates] == ["hello bot"]
    assert result.updates[0].update_id == mine["update_id"]
    assert result.last_update_id == mine["update_id"]


async def test_only_updates_of_the_configured_chat_in_callbacks_count(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        message_id = await client.send_message("pick", buttons=[("Yes", "am:n:0")])
        fake.user_tap(message_id, 0, chat_id=999)
        result = await client.poll_updates(None, 0)
    assert result.updates == []
    assert result.last_update_id is not None


async def test_poll_normalizes_messages_and_callbacks(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        message_id = await client.send_message("pick", buttons=[("Yes", "am:n:0")])
        fake.user_tap(message_id, 0)
        fake.user_text("my answer", reply_to=message_id)
        result = await client.poll_updates(None, 0)
    callback, message = result.updates
    assert (callback.kind, callback.data, callback.message_id) == ("callback", "am:n:0", message_id)
    assert callback.callback_id is not None
    assert (message.kind, message.text, message.reply_to_message_id) == ("message", "my answer", message_id)


async def test_poll_sends_offset_and_timeout(fake, clock) -> None:
    async with make_client(fake, clock) as client:
        await client.poll_updates(42, 25)
    (payload,) = fake.calls_for("getUpdates")
    assert payload["offset"] == 42
    assert payload["timeout"] == 25


async def test_conflict_409_raises_typed_error(fake, clock) -> None:
    fake.queue_response("getUpdates", 409, {"ok": False, "error_code": 409, "description": "Conflict"})
    async with make_client(fake, clock) as client:
        with pytest.raises(TelegramConflictError):
            await client.poll_updates(None, 0)
