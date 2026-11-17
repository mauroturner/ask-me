from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fakes import FakeTelegram

from ask_me.config import set_muted
from ask_me.hook import STOP_EXCERPT_LIMIT, run_hook

BASE_URL = "https://telegram.test"


def configure(config_dir: Path, fake: FakeTelegram, **extra) -> dict[str, str]:
    data = {"telegram": {"botToken": fake.token, "chatId": str(fake.chat_id)}, **extra}
    (config_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")
    return {"ASK_ME_CONFIG_DIR": str(config_dir)}


def hook(env: dict[str, str], payload: object, fake: FakeTelegram) -> tuple[int, str]:
    stderr = io.StringIO()
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    code = run_hook(raw, env, stderr, transport=fake.transport, base_url=BASE_URL)
    return code, stderr.getvalue()


def sent_texts(fake: FakeTelegram) -> list[str]:
    return [payload["text"] for payload in fake.calls_for("sendMessage")]


def test_notification_event_is_formatted_with_project_name(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    code, _ = hook(
        env,
        {
            "hook_event_name": "Notification",
            "message": "Claude needs your permission to use Bash",
            "notification_type": "permission_prompt",
            "cwd": "/home/me/projects/cool-app",
        },
        fake,
    )
    assert code == 0
    assert sent_texts(fake) == ["🔔 Claude needs you (cool-app): Claude needs your permission to use Bash"]


def test_windows_cwd_uses_the_last_path_segment(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    hook(env, {"hook_event_name": "Notification", "message": "hi", "cwd": "C:\\Users\\me\\proj\\"}, fake)
    assert sent_texts(fake) == ["🔔 Claude needs you (proj): hi"]


def test_stop_event_includes_an_excerpt_of_the_last_assistant_message(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    hook(
        env,
        {
            "hook_event_name": "Stop",
            "cwd": "/work/api",
            "stop_hook_active": False,
            "last_assistant_message": "All tests pass. Refactor done.",
        },
        fake,
    )
    assert sent_texts(fake) == ["✅ Claude finished (api)\n\nAll tests pass. Refactor done."]


def test_stop_excerpt_is_capped_at_800_chars(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    hook(env, {"hook_event_name": "Stop", "cwd": "/work/api", "last_assistant_message": "w" * 3000}, fake)
    (text,) = sent_texts(fake)
    header, excerpt = text.split("\n\n", 1)
    assert header == "✅ Claude finished (api)"
    assert len(excerpt) == STOP_EXCERPT_LIMIT == 800
    assert excerpt.endswith("…")


def test_stop_without_last_assistant_message_sends_only_the_header(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    hook(env, {"hook_event_name": "Stop", "cwd": "/work/api"}, fake)
    assert sent_texts(fake) == ["✅ Claude finished (api)"]


def test_missing_fields_never_crash(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    code, _ = hook(env, {"hook_event_name": "Notification"}, fake)
    assert code == 0
    assert sent_texts(fake) == ["🔔 Claude needs you"]
    code, _ = hook(env, {"hook_event_name": "Notification", "message": 42, "cwd": None, "unknown": [1]}, fake)
    assert code == 0


def test_notification_types_allow_list_filters_notifications(fake, config_dir) -> None:
    env = configure(config_dir, fake, hooks={"notificationTypes": ["permission_prompt"]})
    hook(env, {"hook_event_name": "Notification", "message": "idle", "notification_type": "idle_prompt"}, fake)
    hook(env, {"hook_event_name": "Notification", "message": "no type"}, fake)
    assert sent_texts(fake) == []
    hook(env, {"hook_event_name": "Notification", "message": "ask", "notification_type": "permission_prompt"}, fake)
    assert sent_texts(fake) == ["🔔 Claude needs you: ask"]


def test_stop_ignores_the_notification_types_filter(fake, config_dir) -> None:
    env = configure(config_dir, fake, hooks={"notificationTypes": ["permission_prompt"]})
    hook(env, {"hook_event_name": "Stop", "cwd": "/a/b"}, fake)
    assert sent_texts(fake) == ["✅ Claude finished (b)"]


def test_stop_hook_active_is_skipped(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    code, _ = hook(env, {"hook_event_name": "Stop", "stop_hook_active": True, "cwd": "/a/b"}, fake)
    assert code == 0
    assert fake.calls == []


def test_events_not_in_config_are_skipped(fake, config_dir) -> None:
    env = configure(config_dir, fake, hooks={"events": ["Stop"]})
    hook(env, {"hook_event_name": "Notification", "message": "hi"}, fake)
    assert fake.calls == []
    hook(env, {"hook_event_name": "Stop", "cwd": "/a/b"}, fake)
    assert len(sent_texts(fake)) == 1


def test_muted_hooks_send_nothing(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    set_muted(True, env)
    code, _ = hook(env, {"hook_event_name": "Stop", "cwd": "/a/b"}, fake)
    assert code == 0
    assert fake.calls == []


def test_network_failure_still_exits_0_and_reports_on_stderr(fake, config_dir) -> None:
    env = configure(config_dir, fake)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    stderr = io.StringIO()
    code = run_hook(
        json.dumps({"hook_event_name": "Stop", "cwd": "/a/b"}),
        env,
        stderr,
        transport=httpx.MockTransport(boom),
        base_url=BASE_URL,
    )
    assert code == 0
    assert "ask-me" in stderr.getvalue()
    assert fake.token not in stderr.getvalue()


@pytest.mark.parametrize("raw", ["not json", "", "[]", "null", '"text"', "{"])
def test_invalid_stdin_exits_0(fake, config_dir, raw: str) -> None:
    env = configure(config_dir, fake)
    code, _ = hook(env, raw, fake)
    assert code == 0
    assert fake.calls == []


def test_missing_config_exits_0_with_the_setup_hint_on_stderr(fake, config_dir) -> None:
    code, err = hook({"ASK_ME_CONFIG_DIR": str(config_dir)}, {"hook_event_name": "Stop"}, fake)
    assert code == 0
    assert "ask-me is not configured. Run: ask-me setup" in err
    assert fake.calls == []


def test_hook_subprocess_prints_nothing_to_stdout_and_exits_0(tmp_path) -> None:
    env = {"ASK_ME_CONFIG_DIR": str(tmp_path / "cfg"), "PATH": "", "SYSTEMROOT": "C:\\Windows"}
    for stdin in ('{"hook_event_name":"Notification","message":"x","cwd":"/tmp/proj"}', "not json"):
        done = subprocess.run(
            [sys.executable, "-m", "ask_me", "hook"],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        assert done.returncode == 0
        assert done.stdout == ""
