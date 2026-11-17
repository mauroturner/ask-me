"""Command line interface: ``ask-me <command>`` (S1, S8)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import TextIO

import httpx

from . import __version__
from .clock import Clock
from .config import (
    NotConfiguredError,
    is_muted,
    load_config,
    mask_token,
    set_muted,
)
from .context import GitRunner, build_header
from .hook import run_hook
from .notify import LEVELS, notify
from .setup_wizard import run_setup
from .telegram import DEFAULT_BASE_URL, TelegramClient, TelegramError

TEST_MESSAGE = "ask-me is working ✅"

COMMANDS: dict[str, str] = {
    "setup": "interactive wizard: connect a Telegram bot and capture your chat id",
    "test": "send a test message to check the connection",
    "notify": "send a one-way message: ask-me notify \"<text>\" [--level info|success|warning|important] [--context <label>]",
    "hook": "Claude Code hook entry point (reads the hook JSON on stdin; always exits 0)",
    "mcp": "run the MCP server over stdio (tools: notify, ask)",
    "mute": "stop notify and hooks from sending (ask still works)",
    "unmute": "resume notify and hooks",
    "status": "show whether ask-me is configured, the masked token, chat id and mute state",
    "help": "show this help",
}


def build_parser() -> argparse.ArgumentParser:
    lines = "\n".join(f"  {name:<8} {text}" for name, text in COMMANDS.items())
    parser = argparse.ArgumentParser(
        prog="ask-me",
        description="Reach your away human over Telegram from Claude Code: notifications and two-way decisions.",
        epilog=f"commands:\n{lines}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"ask-me {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, text in COMMANDS.items():
        command = sub.add_parser(name, help=text, description=text)
        if name == "notify":
            command.add_argument("text", help="message to send")
            command.add_argument("--level", choices=LEVELS, default="info", help="importance (default: info)")
            command.add_argument(
                "--context",
                help="short label of what you are working on, shown in the message header (max 60 characters)",
            )
    return parser


def _read_stdin(stdin: TextIO | None) -> str:
    stream = stdin if stdin is not None else sys.stdin
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        return buffer.read().decode("utf-8-sig", errors="replace")
    return stream.read()


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    base_url: str = DEFAULT_BASE_URL,
    read_token: Callable[[str], str] = getpass.getpass,
    clock: Clock | None = None,
    git: GitRunner | None = None,
    cwd: str | None = None,
) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr

    # The hook must exit 0 whatever it is given, so it bypasses argparse (which exits 2 on bad input).
    if args_list and args_list[0] == "hook":
        try:
            raw = _read_stdin(stdin)
        except Exception as exc:
            print(f"ask-me hook: could not read stdin: {exc}", file=err)
            return 0
        return run_hook(raw, env, err, transport=transport, base_url=base_url, git=git)

    parser = build_parser()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            args = parser.parse_args(args_list)
    except SystemExit as exc:  # argparse: --help/--version exit 0, usage errors 2
        return exc.code if isinstance(exc.code, int) else 2
    if args.command in (None, "help"):
        out.write(parser.format_help())
        return 0

    if args.command == "mute":
        set_muted(True, env)
        print("muted: notify and hooks are silenced (ask still works)", file=out)
        return 0
    if args.command == "unmute":
        set_muted(False, env)
        print("unmuted", file=out)
        return 0
    if args.command == "status":
        return _status(env, out)
    if args.command == "mcp":
        from .mcp_server import run_stdio

        run_stdio(env)
        return 0
    if args.command == "setup":
        return asyncio.run(
            run_setup(env, read_token=read_token, stdout=out, stderr=err, transport=transport, base_url=base_url, clock=clock)
        )
    return asyncio.run(_send_command(args, env, out, err, transport, base_url, clock, git, cwd))


def _status(env: Mapping[str, str], out: TextIO) -> int:
    try:
        config = load_config(env)
    except NotConfiguredError:
        print("configured: no (run: ask-me setup)", file=out)
        print(f"muted: {'yes' if is_muted(env) else 'no'}", file=out)
        return 0
    print("configured: yes", file=out)
    print(f"token: {mask_token(config.bot_token)}", file=out)
    print(f"chat id: {config.chat_id}", file=out)
    print(f"muted: {'yes' if is_muted(env) else 'no'}", file=out)
    return 0


async def _send_command(
    args: argparse.Namespace,
    env: Mapping[str, str],
    out: TextIO,
    err: TextIO,
    transport: httpx.AsyncBaseTransport | None,
    base_url: str,
    clock: Clock | None,
    git: GitRunner | None,
    cwd: str | None,
) -> int:
    try:
        config = load_config(env)
    except NotConfiguredError as exc:
        print(str(exc), file=err)
        return 1
    client = TelegramClient(
        config.bot_token,
        config.chat_id,
        base_url=base_url,
        transport=transport,
        sleep=clock.sleep if clock else asyncio.sleep,
        max_parts=config.max_message_parts,
    )
    try:
        async with client:
            if args.command == "test":
                await client.send_message(TEST_MESSAGE)
                print("test message sent", file=out)
            else:
                header = build_header(cwd if cwd is not None else os.getcwd(), env, context=args.context, git=git)
                print(await notify(client, args.text, args.level, env, header), file=out)
    except TelegramError as exc:
        print(f"ask-me: {exc}", file=err)
        return 1
    return 0
