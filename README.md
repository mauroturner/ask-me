# ask-me

Get a Telegram message when your autonomous Claude Code agent needs a decision or finishes — and answer from your phone.

## The problem

You leave Claude Code working on a long task. It stops at a permission prompt or a question, and nothing moves until you come back. Or it finishes and you only find out an hour later. ask-me sends those moments to Telegram. You answer from your phone, and the agent continues with your answer.

## How it works

- **MCP tools `notify` and `ask`.** Claude calls them when you ask to be notified, or when it needs a decision.
- **Claude Code hooks `Notification` and `Stop`.** These forward permission prompts, idle prompts, and task completion without any instruction to Claude.
- **CLI `ask-me`.** Set up the bot, send messages from any script, mute and unmute.

```
 Claude Code ──► MCP: notify / ask ──┐
      │                              ├──► ask-me ──► Telegram Bot API ──► your phone
      └──► hooks: Notification/Stop ─┘                                        │
                                          ◄────── your reply or button ───────┘
```

## Quick start (about 5 minutes)

**a. Create a bot.** In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token. It looks like `123456789:ABCdef...`.

**b. Install.**

Requirements: Python 3.10+ and [uv](https://docs.astral.sh/uv/), or pipx.

```bash
uv tool install git+https://github.com/mauroturner/ask-me
```

Alternative: `pipx install git+https://github.com/mauroturner/ask-me`. Either command puts an `ask-me` command on your PATH. On Windows it is a real `ask-me.exe`, so hook commands can call it directly.

Contributors who want to work from source:

```bash
git clone https://github.com/mauroturner/ask-me && cd ask-me
uv sync
uv run ask-me help
```

**c. Set up.**

```bash
ask-me setup
```

Paste the token when asked. Then open the link the wizard prints and send `/start` to your bot within 2 minutes. The wizard captures your chat id, sends a confirmation message, and writes the config file.

**d. Register the MCP server with Claude Code.**

```bash
claude mcp add --transport stdio --scope user ask-me -- ask-me mcp
```

If `ask-me` is not on PATH, use uvx as the command instead:

```bash
claude mcp add --transport stdio --scope user ask-me -- uvx --from git+https://github.com/mauroturner/ask-me ask-me mcp
```

**e. Register the hooks.** Merge this into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Notification": [
      { "hooks": [ { "type": "command", "command": "ask-me hook" } ] }
    ],
    "Stop": [
      { "hooks": [ { "type": "command", "command": "ask-me hook" } ] }
    ]
  }
}
```

If Claude Code can't find the `ask-me` command, use its absolute path, as printed by `uv tool dir --bin` (or `where ask-me` on Windows).

**f. Test.**

```bash
ask-me test
```

You should receive `ask-me is working ✅` on your phone.

## Tell Claude when to use it

Claude needs instructions to know when to call `notify` and `ask`. Paste the block from [`docs/CLAUDE.snippet.md`](docs/CLAUDE.snippet.md) into `~/.claude/CLAUDE.md` (all projects) or into the project's `CLAUDE.md`.

Prompts that trigger it:

- "Notify me when you finish."
- "This is important, ping me."
- "I'm going out. If you need a decision, ask me on Telegram."

## Reference

### CLI commands

| Command | What it does |
|---------|--------------|
| `ask-me setup` | Validates the token, captures your chat id, writes the config |
| `ask-me test` | Sends `ask-me is working ✅` |
| `ask-me notify "<text>" [--level <level>]` | Sends a message. Levels: `info` (default), `success`, `warning`, `important` |
| `ask-me hook` | Reads a Claude Code hook event from stdin. Always exits 0 and prints nothing to stdout |
| `ask-me mcp` | Runs the MCP server over stdio |
| `ask-me mute` | Mutes `notify` and hooks. Does not affect `ask` |
| `ask-me unmute` | Lifts the mute |
| `ask-me status` | Shows whether ask-me is configured, the masked token, the chat id, and the mute state |
| `ask-me help` | Prints usage |

### MCP tools

**`notify({ message, level? })`**

- `level`: `info` (default), `success`, `warning`, or `important`. Each adds a prefix: ℹ️ ✅ ⚠️ 🚨.
- Returns the plain text `sent` or `muted`.

**`ask({ question, options?, allow_free_text?, timeout_seconds? })`**

- `options`: up to 10 buttons. Omit them for a free-text question.
- `allow_free_text`: defaults to `true`.
- `timeout_seconds`: defaults to `askTimeoutSeconds` (1500).
- Returns JSON text:

```json
{ "answered": true, "answer": "Option B", "via": "button" }
```

```json
{ "answered": false, "reason": "timeout" }
```

`via` is `"button"` or `"text"`.

### Configuration

The config file lives at `<configDir>/config.json`. `configDir` is `~/.ask-me` unless `ASK_ME_CONFIG_DIR` is set.

```json
{
  "telegram": {
    "botToken": "123456789:ABCdef...",
    "chatId": "987654321"
  },
  "askTimeoutSeconds": 1500,
  "hooks": {
    "events": ["Notification", "Stop"],
    "notificationTypes": ["permission_prompt", "idle_prompt"]
  }
}
```

| Field | Default | Meaning |
|-------|---------|---------|
| `telegram.botToken` | none | Bot token from BotFather |
| `telegram.chatId` | none | Your private chat with the bot |
| `askTimeoutSeconds` | `1500` | How long `ask` waits for an answer (max 86400) |
| `hooks.events` | `["Notification","Stop"]` | Hook events that ask-me forwards |
| `hooks.notificationTypes` | all | Optional allow-list of `Notification` types |

Environment variables take precedence over the file:

| Variable | Overrides |
|----------|-----------|
| `ASK_ME_TELEGRAM_TOKEN` | `telegram.botToken` |
| `ASK_ME_TELEGRAM_CHAT_ID` | `telegram.chatId` |
| `ASK_ME_CONFIG_DIR` | Config directory |

A template with empty values is in [`.env.example`](.env.example).

## Long waits

Claude Code aborts a stdio MCP tool call after 30 minutes without a response or progress. The defaults are set to work within that limit:

- `ask` sends an MCP progress notification every 60 seconds while it waits, as long as the request carries a progress token.
- The default `askTimeoutSeconds` is 1500 (25 minutes), which is below the 30-minute limit.

To wait longer, raise these settings:

1. **ask-me timeout.** Set `askTimeoutSeconds` in the config file, up to 86400.
2. **Claude Code idle timeout.** `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` sets the idle limit in milliseconds (default `1800000`). Set it to `0` to disable the idle limit. Put it in the `env` block of `~/.claude/settings.json` or in your shell.
3. **Overall tool timeout.** `MCP_TOOL_TIMEOUT` (milliseconds) caps the total time of one call. It is very long by default, so you usually don't need to change it.

## Security

- **The token** lives in `~/.ask-me/config.json`, outside the repository. On POSIX systems the file is written with mode `0600`.
- **Only your chat accepts commands.** Messages from any chat other than `telegram.chatId` are ignored.
- **The bot can only message people who started it.** Telegram does not let a bot write first to someone who has not sent `/start`.
- **Never commit `.env`.** It holds your token. Keep it out of version control.
- **Anyone with your token can impersonate the bot.** If the token leaks, open [@BotFather](https://t.me/BotFather), send `/revoke`, choose your bot, and update your config.

## FAQ

**Why Telegram and not WhatsApp?**
Telegram's Bot API is free. You create a bot with @BotFather in about a minute, and it uses plain HTTPS and JSON. WhatsApp's official Cloud API requires a Meta business account, a dedicated phone number, pre-approved message templates, and a 24-hour customer-service window. Unofficial libraries risk getting the account banned, and Twilio is paid. The MVP ships Telegram behind a `Channel` interface, so WhatsApp can be added later.

**Can several Claude sessions use the same bot at once?**
Yes. All sessions share one inbox. To answer a specific question, reply to its message in Telegram. A plain message goes to the newest pending question.

**Can I mute it when I am at my desk?**
Yes. Run `ask-me mute` to silence `notify` and hooks. `ask` still works, so a blocking question still reaches you. Run `ask-me unmute` to resume.

## Limitations

- Telegram only, for now.
- One chat per installation.
- Uses long polling, not webhooks.
- Not published to PyPI yet. Install from the repository.

## Contributing

```bash
uv sync
uv run pytest
```

Pull requests are welcome. A good first contribution is a WhatsApp channel behind the `Channel` interface.

## License

MIT. See [LICENSE](LICENSE).
