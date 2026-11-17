from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fakes import FakeClock, FakeTelegram

from ask_me.cli import main

SUBCOMMANDS = ["setup", "test", "notify", "hook", "mcp", "mute", "unmute", "status", "help"]


def run(
    argv: list[str], config_dir: Path, fake: FakeTelegram | None = None, **kwargs
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    env = {"ASK_ME_CONFIG_DIR": str(config_dir), **(kwargs.pop("env", None) or {})}
    code = main(
        argv,
        env=env,
        stdout=out,
        stderr=err,
        transport=fake.transport if fake else None,
        base_url="https://telegram.test",
        **kwargs,
    )
    return code, out.getvalue(), err.getvalue()


def configure(config_dir: Path, fake: FakeTelegram) -> None:
    data = {"telegram": {"botToken": fake.token, "chatId": str(fake.chat_id)}}
    (config_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")


def test_help_lists_every_subcommand_and_exits_0(config_dir) -> None:
    code, out, _ = run(["help"], config_dir)
    assert code == 0
    for name in SUBCOMMANDS:
        assert name in out


def test_no_arguments_prints_usage(config_dir) -> None:
    code, out, _ = run([], config_dir)
    assert code == 0
    assert "usage" in out.lower()


def test_unknown_subcommand_is_a_usage_error(config_dir) -> None:
    code, _, err = run(["frobnicate"], config_dir)
    assert code == 2
    assert err


def test_notify_sends_with_level(fake, config_dir) -> None:
    configure(config_dir, fake)
    code, out, _ = run(["notify", "deploy finished", "--level", "important"], config_dir, fake)
    assert code == 0
    assert out.strip() == "sent"
    assert fake.calls_for("sendMessage")[0]["text"] == "🚨 deploy finished"


def test_notify_when_muted_sends_nothing(fake, config_dir) -> None:
    configure(config_dir, fake)
    run(["mute"], config_dir)
    code, out, _ = run(["notify", "psst"], config_dir, fake)
    assert code == 0
    assert out.strip() == "muted"
    assert fake.calls == []


def test_notify_without_config_fails_with_setup_hint(fake, config_dir) -> None:
    code, _, err = run(["notify", "hi"], config_dir, fake)
    assert code == 1
    assert "ask-me is not configured. Run: ask-me setup" in err


def test_notify_rejects_an_unknown_level(fake, config_dir) -> None:
    configure(config_dir, fake)
    code, _, _ = run(["notify", "hi", "--level", "loud"], config_dir, fake)
    assert code == 2
    assert fake.calls == []


def test_test_command_sends_the_working_message_even_when_muted(fake, config_dir) -> None:
    configure(config_dir, fake)
    run(["mute"], config_dir)
    code, _, _ = run(["test"], config_dir, fake)
    assert code == 0
    assert fake.calls_for("sendMessage")[0]["text"] == "ask-me is working ✅"


def test_mute_and_unmute_toggle_the_marker_file(config_dir) -> None:
    assert run(["mute"], config_dir)[0] == 0
    assert (config_dir / "muted").exists()
    assert run(["unmute"], config_dir)[0] == 0
    assert not (config_dir / "muted").exists()


def test_status_when_configured_masks_the_token(fake, config_dir) -> None:
    configure(config_dir, fake)
    code, out, _ = run(["status"], config_dir)
    assert code == 0
    assert "configured: yes" in out
    assert "123456:****" in out
    assert "SECRET-TOKEN" not in out
    assert str(fake.chat_id) in out
    assert "muted: no" in out


def test_status_when_not_configured(config_dir) -> None:
    run(["mute"], config_dir)
    code, out, _ = run(["status"], config_dir)
    assert code == 0
    assert "configured: no" in out
    assert "muted: yes" in out


def test_hook_subcommand_never_writes_stdout_and_exits_0(fake, config_dir) -> None:
    configure(config_dir, fake)
    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["hook"],
        env={"ASK_ME_CONFIG_DIR": str(config_dir)},
        stdin=io.StringIO(json.dumps({"hook_event_name": "Stop", "cwd": "/a/proj"})),
        stdout=out,
        stderr=err,
        transport=fake.transport,
        base_url="https://telegram.test",
    )
    assert code == 0
    assert out.getvalue() == ""
    assert fake.calls_for("sendMessage")[0]["text"] == "✅ Claude finished (proj)"


def test_hook_survives_bogus_extra_arguments(config_dir) -> None:
    out, err = io.StringIO(), io.StringIO()
    code = main(["hook", "--bogus"], env={"ASK_ME_CONFIG_DIR": str(config_dir)}, stdin=io.StringIO("x"), stdout=out, stderr=err)
    assert code == 0
    assert out.getvalue() == ""


def test_python_dash_m_ask_me_help() -> None:
    done = subprocess.run([sys.executable, "-m", "ask_me", "help"], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0
    assert "ask-me" in done.stdout


# ---- setup wizard ---------------------------------------------------------------------------
@pytest.fixture
def setup_fake(clock: FakeClock) -> FakeTelegram:
    return FakeTelegram(chat_id=777, clock=clock)


def run_setup(config_dir: Path, fake: FakeTelegram, clock: FakeClock, *, env=None, token="123456:SECRET-TOKEN"):
    prompts: list[str] = []

    def read_token(prompt: str) -> str:
        prompts.append(prompt)
        return token

    code, out, err = run(["setup"], config_dir, fake, env=env, clock=clock, read_token=read_token)
    return code, out, err, prompts


def test_setup_happy_path_writes_config_and_confirms(setup_fake, clock, config_dir) -> None:
    def script(f: FakeTelegram) -> None:
        if not f.script_done:
            f.script_done = True
            f.user_text("hello from a group", chat_id=-100, chat_type="group")
            f.user_text("/start", chat_id=777)

    setup_fake.script_done = False
    setup_fake.script = script
    code, out, err, prompts = run_setup(config_dir, setup_fake, clock)
    assert code == 0, err
    assert len(prompts) == 1
    assert "t.me/ask_me_test_bot" in out
    assert "SECRET-TOKEN" not in out + err
    saved = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
    assert saved["telegram"] == {"botToken": "123456:SECRET-TOKEN", "chatId": "777"}
    (confirmation,) = setup_fake.calls_for("sendMessage")
    assert confirmation["chat_id"] == "777"
    assert (config_dir / "state" / "offset").exists()


def test_setup_takes_the_token_from_the_environment(setup_fake, clock, config_dir) -> None:
    setup_fake.script_done = False

    def script(f: FakeTelegram) -> None:
        if not f.script_done:
            f.script_done = True
            f.user_text("/start", chat_id=777)

    setup_fake.script = script
    code, _, err, prompts = run_setup(
        config_dir, setup_fake, clock, env={"ASK_ME_TELEGRAM_TOKEN": "123456:SECRET-TOKEN"}, token="unused"
    )
    assert code == 0, err
    assert prompts == []


def test_setup_with_an_invalid_token_fails_clearly(setup_fake, clock, config_dir) -> None:
    code, _, err, _ = run_setup(config_dir, setup_fake, clock, token="999:WRONG")
    assert code == 1
    assert "invalid" in err.lower()
    assert "WRONG" not in err
    assert not (config_dir / "config.json").exists()


def test_setup_fails_when_a_webhook_is_set(setup_fake, clock, config_dir) -> None:
    setup_fake.webhook_url = "https://example.com/hook"
    code, _, err, _ = run_setup(config_dir, setup_fake, clock)
    assert code == 1
    assert "webhook" in err.lower()
    assert not (config_dir / "config.json").exists()


def test_setup_gives_up_after_120_seconds(setup_fake, clock, config_dir) -> None:
    start = clock.now()
    code, _, err, _ = run_setup(config_dir, setup_fake, clock)
    assert code == 1
    assert "120" in err
    assert 120 <= clock.now() - start < 160
    assert not (config_dir / "config.json").exists()
