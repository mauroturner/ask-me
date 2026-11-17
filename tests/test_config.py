from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from ask_me.config import NOT_CONFIGURED_MESSAGE, NotConfiguredError, load_config, resolve_config_dir, save_config


def write_config(config_dir: Path, data: dict) -> None:
    (config_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")


def env_for(config_dir: Path, **extra: str) -> dict[str, str]:
    return {"ASK_ME_CONFIG_DIR": str(config_dir), **extra}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 5), (1, 1), (8, 8), (0, 5), (-2, 5), ("3", 5), (True, 5), (2.5, 5)],
)
def test_max_message_parts(config_dir: Path, value, expected: int) -> None:
    data: dict = {"telegram": {"botToken": "1:abc", "chatId": 42}}
    if value is not None:
        data["maxMessageParts"] = value
    write_config(config_dir, data)
    assert load_config(env_for(config_dir)).max_message_parts == expected


def test_config_dir_comes_from_env(config_dir: Path) -> None:
    assert resolve_config_dir(env_for(config_dir)) == config_dir


def test_config_dir_defaults_to_home_dot_ask_me() -> None:
    assert resolve_config_dir({}) == Path.home() / ".ask-me"


def test_file_config_with_defaults(config_dir: Path) -> None:
    write_config(config_dir, {"telegram": {"botToken": "1:abc", "chatId": 42}})
    config = load_config(env_for(config_dir))
    assert config.bot_token == "1:abc"
    assert config.chat_id == "42"
    assert config.ask_timeout_seconds == 1500
    assert config.hook_events == ("Notification", "Stop")
    assert config.notification_types is None


def test_file_config_custom_values(config_dir: Path) -> None:
    write_config(
        config_dir,
        {
            "telegram": {"botToken": "1:abc", "chatId": "42"},
            "askTimeoutSeconds": 600,
            "hooks": {"events": ["Stop"], "notificationTypes": ["permission_prompt"]},
        },
    )
    config = load_config(env_for(config_dir))
    assert config.ask_timeout_seconds == 600
    assert config.hook_events == ("Stop",)
    assert config.notification_types == ("permission_prompt",)


def test_env_overrides_take_precedence_over_file(config_dir: Path) -> None:
    write_config(config_dir, {"telegram": {"botToken": "1:file", "chatId": 42}})
    env = env_for(config_dir, ASK_ME_TELEGRAM_TOKEN="2:env", ASK_ME_TELEGRAM_CHAT_ID="99")
    config = load_config(env)
    assert config.bot_token == "2:env"
    assert config.chat_id == "99"


def test_env_alone_is_enough_without_a_file(config_dir: Path) -> None:
    env = env_for(config_dir, ASK_ME_TELEGRAM_TOKEN="2:env", ASK_ME_TELEGRAM_CHAT_ID="99")
    config = load_config(env)
    assert (config.bot_token, config.chat_id) == ("2:env", "99")


def test_missing_config_raises_clear_error(config_dir: Path) -> None:
    with pytest.raises(NotConfiguredError) as excinfo:
        load_config(env_for(config_dir))
    assert str(excinfo.value) == "ask-me is not configured. Run: ask-me setup"
    assert NOT_CONFIGURED_MESSAGE == "ask-me is not configured. Run: ask-me setup"


def test_missing_chat_id_raises_clear_error(config_dir: Path) -> None:
    write_config(config_dir, {"telegram": {"botToken": "1:abc"}})
    with pytest.raises(NotConfiguredError):
        load_config(env_for(config_dir))


def test_corrupt_config_file_counts_as_not_configured(config_dir: Path) -> None:
    (config_dir / "config.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(NotConfiguredError):
        load_config(env_for(config_dir))


def test_save_config_round_trips_and_preserves_other_fields(config_dir: Path) -> None:
    write_config(config_dir, {"askTimeoutSeconds": 900, "hooks": {"events": ["Stop"]}})
    path = save_config("7:tok", 1234, env_for(config_dir))
    assert path == config_dir / "config.json"
    config = load_config(env_for(config_dir))
    assert (config.bot_token, config.chat_id) == ("7:tok", "1234")
    assert config.ask_timeout_seconds == 900
    assert config.hook_events == ("Stop",)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes only")
def test_save_config_writes_mode_0600(config_dir: Path) -> None:
    path = save_config("7:tok", 1234, env_for(config_dir))
    assert (os.stat(path).st_mode & 0o777) == 0o600
