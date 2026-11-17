"""MCP server (stdio) exposing ``notify`` and ``ask`` (S6)."""

from __future__ import annotations

import json
import os
import random
from collections.abc import Mapping

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .ask import ask as run_ask
from .clock import Clock, SystemClock
from .config import Config, NotConfiguredError, load_config, resolve_config_dir
from .context import GitRunner, build_header
from .inbox import Inbox
from .notify import LEVELS
from .notify import notify as run_notify
from .telegram import DEFAULT_BASE_URL, TelegramClient, TelegramError

NOTIFY_DESCRIPTION = (
    "Send the user a one-way message on their phone (Telegram). Use it when the user asked to be "
    'notified ("notify me when you finish", "avisame cuando termines", "let me know when it is done") '
    'or when something is important enough that they should know right away ("this is important"). '
    "Do not use it for routine progress chatter. It does not wait for a reply: use `ask` when you need "
    "a decision. level is one of info (default), success, warning, important. "
    "Set `context` to a 2–6 word label of the task you are working on (e.g. \"refactor auth\"), so the "
    "user can tell parallel sessions apart. Returns 'sent', or 'muted' when the user muted notifications."
)

ASK_DESCRIPTION = (
    "Ask the user a question on their phone (Telegram) and wait for the answer. Use it when you need a "
    "decision or confirmation and the user said they are away, unattended or not at the computer; do not "
    "use it for things you can decide yourself. Provide `options` (max 10) to show one tappable button "
    "each; free-text replies are also accepted unless allow_free_text is false. Blocks until the user "
    "answers or timeout_seconds passes (default from config, max 86400). Returns JSON text: "
    '{"answered": true, "answer": "...", "via": "button"|"text"} or {"answered": false, "reason": "timeout"}. '
    "If it times out, pick the safest reversible choice or stop and wait; do not assume consent. "
    "Set `context` to a 2–6 word label of the task you are working on (e.g. \"refactor auth\"), so the "
    "user can tell parallel sessions apart."
)


def build_server(
    env: Mapping[str, str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    base_url: str = DEFAULT_BASE_URL,
    clock: Clock | None = None,
    rng: random.Random | None = None,
    cwd: str | None = None,
    git: GitRunner | None = None,
) -> MCPServer:
    clock = clock or SystemClock()
    server = MCPServer(
        "ask-me",
        instructions="Reach the user on their phone: `notify` to tell them something, `ask` to get a decision.",
    )

    def header_for(context: str | None) -> str | None:
        return build_header(cwd if cwd is not None else os.getcwd(), env, context=context, git=git)

    def make_client() -> tuple[TelegramClient, Config]:
        try:
            config = load_config(env)
        except NotConfiguredError as exc:
            raise ToolError(str(exc)) from None
        client = TelegramClient(
            config.bot_token,
            config.chat_id,
            base_url=base_url,
            transport=transport,
            sleep=clock.sleep,
            max_parts=config.max_message_parts,
        )
        return client, config

    @server.tool(name="notify", description=NOTIFY_DESCRIPTION)
    async def notify(message: str, level: str = "info", context: str | None = None) -> str:
        if level not in LEVELS:
            raise ToolError(f"level must be one of: {', '.join(LEVELS)}")
        client, _config = make_client()
        try:
            async with client:
                return await run_notify(client, message, level, env, header_for(context))
        except TelegramError as exc:
            raise ToolError(str(exc)) from None

    @server.tool(name="ask", description=ASK_DESCRIPTION)
    async def ask(
        question: str,
        ctx: Context,
        options: list[str] | None = None,
        allow_free_text: bool = True,
        timeout_seconds: int | None = None,
        context: str | None = None,
    ) -> str:
        client, config = make_client()
        timeout = timeout_seconds if timeout_seconds is not None else config.ask_timeout_seconds

        async def report(elapsed: float, total: float) -> None:
            # A no-op unless the client sent a progressToken; keeps Claude Code's idle timeout at bay.
            await ctx.report_progress(elapsed, total, "Waiting for the user's answer")

        try:
            async with client:
                result = await run_ask(
                    client,
                    Inbox(resolve_config_dir(env), clock),
                    question,
                    options,
                    allow_free_text=allow_free_text,
                    timeout_seconds=timeout,
                    header=header_for(context),
                    clock=clock,
                    rng=rng,
                    on_progress=report,
                )
        except (TelegramError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        return json.dumps(result.to_dict(), ensure_ascii=False)

    return server


def run_stdio(env: Mapping[str, str] | None = None) -> None:
    build_server(env).run("stdio")
