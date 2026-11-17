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
from ask_me.hook import run_hook

BASE_URL = "https://telegram.test"


def configure(config_dir: Path, fake: FakeTelegram, **extra) -> dict[str, str]:
    data = {"telegram": {"botToken": fake.token, "chatId": str(fake.chat_id)}, **extra}
    (config_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")
    return {"ASK_ME_CONFIG_DIR": str(config_dir)}


def hook(env: dict[str, str], payload: object, fake: FakeTelegram, git=None) -> tuple[int, str]:
    stderr = io.StringIO()
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    code = run_hook(raw, env, stderr, transport=fake.transport, base_url=BASE_URL, git=git)
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
    assert sent_texts(fake) == ["📁 cool-app\n🔔 Claude needs you: Claude needs your permission to use Bash"]


def test_windows_cwd_uses_the_last_path_segment(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    hook(env, {"hook_event_name": "Notification", "message": "hi", "cwd": "C:\\Users\\me\\proj\\"}, fake)
    assert sent_texts(fake) == ["📁 proj\n🔔 Claude needs you: hi"]


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
    assert sent_texts(fake) == ["📁 api\n✅ Claude finished\n\nAll tests pass. Refactor done."]


def test_header_has_project_branch_and_session_from_git(fake, config_dir) -> None:
    env = configure(config_dir, fake)

    def git(cwd: str, args: list[str]) -> str | None:
        assert cwd == "/work/api/src"
        return {"--show-toplevel": "/work/api", "--abbrev-ref": "main"}[args[1]]

    payload = {"hook_event_name": "Stop", "cwd": "/work/api/src", "session_id": "a1b2c3d4-e5f6"}
    hook(env, payload, fake, git=git)
    assert sent_texts(fake) == ["📁 api · 🌿 main · 🧵 a1b2c3\n✅ Claude finished"]


def test_other_events_carry_the_header_too(fake, config_dir) -> None:
    env = configure(config_dir, fake, hooks={"events": ["SubagentStop"]})
    hook(env, {"hook_event_name": "SubagentStop", "cwd": "/work/api", "session_id": "zzzzzzzz"}, fake)
    assert sent_texts(fake) == ["📁 api · 🧵 zzzzzz\n🔔 Claude event SubagentStop"]


@pytest.mark.parametrize("session_id", [None, "", 7, ["x"]])
def test_session_part_needs_a_non_empty_string(fake, config_dir, session_id) -> None:
    env = configure(config_dir, fake)
    hook(env, {"hook_event_name": "Stop", "cwd": "/work/api", "session_id": session_id}, fake)
    assert sent_texts(fake) == ["📁 api\n✅ Claude finished"]


def test_env_project_overrides_in_the_hook(fake, config_dir) -> None:
    env = {**configure(config_dir, fake), "ASK_ME_PROJECT": "my-label"}
    hook(env, {"hook_event_name": "Stop", "cwd": "/work/api"}, fake)
    assert sent_texts(fake) == ["📁 my-label\n✅ Claude finished"]


def test_a_crashing_git_runner_never_breaks_the_hook(fake, config_dir) -> None:
    env = configure(config_dir, fake)

    def boom(cwd: str, args: list[str]) -> str | None:
        raise RuntimeError("git exploded")

    code, _ = hook(env, {"hook_event_name": "Stop", "cwd": "/work/api"}, fake, git=boom)
    assert code == 0
    assert sent_texts(fake) == ["📁 api\n✅ Claude finished"]


def test_stop_sends_the_full_last_message_when_it_is_long(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    last = "\n\n".join(f"section {i} " + "w" * 1000 for i in range(6))  # about 6k chars
    hook(env, {"hook_event_name": "Stop", "cwd": "/work/api", "last_assistant_message": last}, fake)
    first, second = sent_texts(fake)
    assert len(first) <= 4096 and len(second) <= 4096
    assert first.startswith("📁 api\n✅ Claude finished\n\nsection 0 ")
    assert second.startswith("(2/2) ") and "📁" not in second
    prefix = "📁 api\n✅ Claude finished\n\n"
    assert first[len(prefix) :] + second[len("(2/2) ") :] == last


def test_stop_message_is_capped_by_max_message_parts(fake, config_dir) -> None:
    env = configure(config_dir, fake, maxMessageParts=1)
    hook(env, {"hook_event_name": "Stop", "cwd": "/work/api", "last_assistant_message": "w" * 9000}, fake)
    (text,) = sent_texts(fake)
    assert len(text) <= 4096 and "… (truncated, " in text


def test_stop_without_last_assistant_message_sends_only_the_header(fake, config_dir) -> None:
    env = configure(config_dir, fake)
    hook(env, {"hook_event_name": "Stop", "cwd": "/work/api"}, fake)
    assert sent_texts(fake) == ["📁 api\n✅ Claude finished"]


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
    assert sent_texts(fake) == ["📁 b\n✅ Claude finished"]


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
