"""Configuration: ``<configDir>/config.json`` plus environment overrides (S2)."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .channel import DEFAULT_MAX_MESSAGE_PARTS
from .fsutil import atomic_write_text

NOT_CONFIGURED_MESSAGE = "ask-me is not configured. Run: ask-me setup"

ENV_CONFIG_DIR = "ASK_ME_CONFIG_DIR"
ENV_TOKEN = "ASK_ME_TELEGRAM_TOKEN"
ENV_CHAT_ID = "ASK_ME_TELEGRAM_CHAT_ID"

DEFAULT_ASK_TIMEOUT_SECONDS = 1500
DEFAULT_HOOK_EVENTS = ("Notification", "Stop")


class NotConfiguredError(Exception):
    """Raised when the bot token or chat id is missing."""

    def __init__(self) -> None:
        super().__init__(NOT_CONFIGURED_MESSAGE)


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_id: str
    ask_timeout_seconds: int = DEFAULT_ASK_TIMEOUT_SECONDS
    hook_events: tuple[str, ...] = DEFAULT_HOOK_EVENTS
    notification_types: tuple[str, ...] | None = None
    max_message_parts: int = DEFAULT_MAX_MESSAGE_PARTS


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def resolve_config_dir(env: Mapping[str, str] | None = None) -> Path:
    override = _env(env).get(ENV_CONFIG_DIR)
    return Path(override) if override else Path.home() / ".ask-me"


def config_path(env: Mapping[str, str] | None = None) -> Path:
    return resolve_config_dir(env) / "config.json"


def read_config_file(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the parsed config file, or ``{}`` when it is missing, unreadable or not an object."""
    try:
        data = json.loads(config_path(env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _as_section(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_tuple(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return None


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Load the config; environment variables win over the file. Raises ``NotConfiguredError``."""
    variables = _env(env)
    data = read_config_file(env)
    telegram = _as_section(data.get("telegram"))
    hooks = _as_section(data.get("hooks"))

    token = variables.get(ENV_TOKEN) or telegram.get("botToken")
    chat_id = variables.get(ENV_CHAT_ID) or telegram.get("chatId")
    if not token or chat_id in (None, ""):
        raise NotConfiguredError

    timeout = data.get("askTimeoutSeconds")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        timeout = DEFAULT_ASK_TIMEOUT_SECONDS
    max_parts = data.get("maxMessageParts")
    if not isinstance(max_parts, int) or isinstance(max_parts, bool) or max_parts < 1:
        max_parts = DEFAULT_MAX_MESSAGE_PARTS
    return Config(
        bot_token=str(token),
        chat_id=str(chat_id),
        ask_timeout_seconds=timeout,
        hook_events=_string_tuple(hooks.get("events")) or DEFAULT_HOOK_EVENTS,
        notification_types=_string_tuple(hooks.get("notificationTypes")),
        max_message_parts=max_parts,
    )


def save_config(token: str, chat_id: int | str, env: Mapping[str, str] | None = None) -> Path:
    """Write the telegram credentials, keeping any other fields already in the file (mode 0600 on POSIX)."""
    data = read_config_file(env)
    telegram = _as_section(data.get("telegram"))
    telegram["botToken"] = token
    telegram["chatId"] = str(chat_id)
    data["telegram"] = telegram
    path = config_path(env)
    atomic_write_text(path, json.dumps(data, indent=2) + "\n", private=True)
    return path


def is_muted(env: Mapping[str, str] | None = None) -> bool:
    return (resolve_config_dir(env) / "muted").exists()


def set_muted(muted: bool, env: Mapping[str, str] | None = None) -> None:
    marker = resolve_config_dir(env) / "muted"
    if muted:
        atomic_write_text(marker, "muted\n")
    else:
        marker.unlink(missing_ok=True)


def mask_token(token: str) -> str:
    """``123456:ABC...`` -> ``123456:****`` (never reveals the secret part)."""
    bot_id = token.split(":", 1)[0]
    return f"{bot_id}:****"
