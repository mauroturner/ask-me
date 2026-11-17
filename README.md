# ask-me

Get a Telegram message when your autonomous Claude Code agent needs a decision or finishes — and answer from your phone.

ask-me connects [Claude Code](https://code.claude.com) to a Telegram bot that only you can talk to. When Claude needs you, your phone buzzes. You tap a button or type a reply, and Claude continues with your answer.

---

## Contents

1. [What is ask-me?](#what-is-ask-me)
2. [What is it for?](#what-is-it-for)
3. [How it works](#how-it-works)
4. [Requirements](#requirements)
5. [Installation](#installation)
6. [Connect it to Claude Code](#connect-it-to-claude-code)
7. [Everyday use](#everyday-use)
8. [Reference](#reference)
9. [Long waits](#long-waits)
10. [Security](#security)
11. [Troubleshooting](#troubleshooting)
12. [Update and uninstall](#update-and-uninstall)
13. [FAQ](#faq)
14. [Limitations](#limitations)
15. [Project structure](#project-structure)
16. [Contributing](#contributing)
17. [License](#license)

---

## What is ask-me?

ask-me is a small command-line tool written in Python. It has three parts:

| Part | What it is | Who uses it |
|------|------------|-------------|
| **MCP server** | Gives Claude two tools: `notify` (send you a message) and `ask` (ask you a question and wait for the answer) | Claude, when you ask it to keep you posted or when it needs a decision |
| **Hook handler** | Forwards Claude Code events — permission prompts, idle prompts, "task finished" — to Telegram | Claude Code itself, automatically |
| **CLI** | `ask-me setup`, `ask-me notify`, `ask-me mute`, … | You, and any script you write |

Everything runs on your machine. The only external service is the Telegram Bot API.

## What is it for?

You leave an agent working on a long task and walk away. Without ask-me, any of these stops the work until you happen to come back:

| Situation | Without ask-me | With ask-me |
|-----------|----------------|-------------|
| Claude needs a decision ("Postgres or SQLite?") | It waits in the terminal | You get the question with buttons and answer from your phone |
| Claude hits a permission prompt | Nothing moves | You get `🔔 Claude needs you: …` so you know to come back |
| The task finishes | You find out an hour later | You get `✅ Claude finished` with Claude's last message |
| You said "this is important, ping me" | Claude has no way to ping you | Claude calls `notify` with a short summary |

Typical prompts once it is installed:

- "Notify me when you finish."
- "This is important, ping me when the migration is done."
- "I'm going out. If you need a decision, ask me on Telegram."

## How it works

```
 Claude Code ──► MCP tools: notify / ask ──┐
      │                                    ├──► ask-me ──► Telegram Bot API ──► your phone
      └──► hooks: Notification / Stop ─────┘                                        │
                                                 ◄────── your reply or button ──────┘
```

1. **Sending.** ask-me sends plain-text messages through your bot to your private chat. Every message starts with a line that says which project, branch and session it comes from. A text longer than Telegram's 4096-character limit is split into several consecutive messages.
2. **Asking.** A question is sent with one button per option, or with a reply box when there are no options. ask-me long-polls Telegram until you answer or the timeout expires. Then it edits the message to show `✅ <your answer>` or `⌛ expired`.
3. **Several sessions at once.** Every running Claude session shares one on-disk inbox under `~/.ask-me/`. Whichever session polls Telegram writes the updates there, and each session takes only the answers to its own questions.
4. **Only you.** Messages from any chat other than yours are ignored.

## Requirements

| Requirement | Notes |
|-------------|-------|
| Python 3.10 or newer | Installed automatically by uv if missing |
| [uv](https://docs.astral.sh/uv/) (recommended) or [pipx](https://pipx.pypa.io/) | Installs ask-me as an isolated command |
| [Claude Code](https://code.claude.com) | The `claude` command must be available |
| A Telegram account | On your phone, with Telegram Desktop optional |

Works on Windows, macOS and Linux.

## Installation

Five steps, about five minutes. Each step ends with what you should see.

### Step 1 — Install uv

Skip this step if `uv --version` already works.

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS and Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal and check: `uv --version` prints a version number.

### Step 2 — Create your Telegram bot

1. In Telegram, open [@BotFather](https://t.me/BotFather) and send `/newbot`.
2. Choose a **display name** (anything, for example `Ask Me (Claude)`).
3. Choose a **username**. It must be unique and end in `bot`, for example `yourname_askme_bot`.
4. BotFather replies with a **token** like `123456789:ABCdef...`. Copy it.

Treat the token like a password. Don't paste it into chats, issues or commits.

### Step 3 — Install ask-me

```bash
uv tool install git+https://github.com/mauroturner/ask-me
```

With pipx instead: `pipx install git+https://github.com/mauroturner/ask-me`.

Check: `ask-me help` prints the list of commands. If the command is not found, run `uv tool update-shell` and open a new terminal.

> On Windows this creates a real `ask-me.exe`, so Claude Code hooks can call it directly.

### Step 4 — Link the bot to your chat

```bash
ask-me setup
```

1. Paste the token when asked.
2. The wizard prints a link like `https://t.me/yourname_askme_bot`. Open it and press **Start** (or send `/start`) within 120 seconds.
3. The wizard captures your chat id, sends a confirmation message to your phone and saves the config.

Expected output:

```
Token OK: @yourname_askme_bot
Open https://t.me/yourname_askme_bot and send /start (waiting up to 120 s)...
Done. Config saved to /home/you/.ask-me/config.json
Next: run `ask-me test` to check it end to end.
```

### Step 5 — Test it

```bash
ask-me test
```

Your phone receives `ask-me is working ✅`.

## Connect it to Claude Code

### 1. Register the MCP server

```bash
claude mcp add --transport stdio --scope user ask-me -- ask-me mcp
```

`--scope user` makes the tools available in every project. Check with `claude mcp list`: `ask-me` should appear as connected.

If `ask-me` is not on the PATH that Claude Code sees, use uvx as the command:

```bash
claude mcp add --transport stdio --scope user ask-me -- uvx --from git+https://github.com/mauroturner/ask-me ask-me mcp
```

### 2. Register the hooks

Hooks forward events without Claude having to decide anything. Merge this into `~/.claude/settings.json` (create the file if it does not exist, and keep any settings you already have):

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

| Hook | When it fires | What you receive |
|------|---------------|------------------|
| `Notification` | Claude waits on a permission prompt, is idle waiting for input, or needs other input | A context header, then `🔔 Claude needs you: <message>` |
| `Stop` | Claude finishes its turn | A context header, then `✅ Claude finished` and Claude's full last message (split into several messages when it is long, see `maxMessageParts`) |

The hook always exits with code 0 and prints nothing, so it can never block or change what Claude does. If Claude Code can't find `ask-me`, use the absolute path printed by `uv tool dir --bin` (or `where ask-me` on Windows).

Prefer fewer messages? Keep only the `Notification` hook, or filter by type in the config (see `hooks.notificationTypes` below).

### 3. Tell Claude when to use the tools

Claude needs instructions to know when to call `notify` and `ask`. Copy the block from [`docs/CLAUDE.snippet.md`](docs/CLAUDE.snippet.md) into:

- `~/.claude/CLAUDE.md` to use it in every project, or
- the project's `CLAUDE.md` to use it in one project.

Restart Claude Code so it loads the MCP server and the instructions.

## Everyday use

**Ask to be notified.** Tell Claude "notify me when you finish". It calls `notify` at the end:

```
📁 my-project · 🌿 feat/cache · 📝 cache layer
✅ Refactor done: 14 files changed, all tests pass. Next step: review the PR.
```

**Know which session wrote to you.** The first line of every message is a context header, so you can tell parallel sessions apart:

```
📁 my-project · 🌿 main · 🧵 a1b2c3
🔔 Claude needs you: Claude needs your permission to use Bash
```

| Part | Meaning |
|------|---------|
| `📁` | Project: the name of the git repository that contains Claude's working directory (the folder name when there is no repository), or the value of `ASK_ME_PROJECT` |
| `🌿` | Current git branch. Omitted outside a repository or on a detached HEAD |
| `📝` | Label of the task, set by Claude through the `context` parameter (MCP tools) or by `--context` (CLI) |
| `🧵` | First 6 characters of the Claude session id (hook messages only) |

Unknown parts are left out. If nothing is known, the message has no header.

**Long messages.** A message longer than 4096 characters is sent as several consecutive messages, split at the last paragraph break, line break or space that fits. Only the first one has the header; the others start with `(2/3) `, `(3/3) ` and so on. At most `maxMessageParts` messages are sent (5 by default); beyond that, the last one ends with `… (truncated, <N> more characters)`.

**Answer questions from your phone.** Tell Claude "I'm away, ask me on Telegram if you need anything". When it needs a decision:

```
📁 my-project · 🌿 feat/cache · 📝 cache layer
❓ Which database should I use for the cache?
[ Redis ]  [ SQLite ]  [ In-memory ]
```

- Tap a button, or
- reply to the message with your own text (long-press → Reply; replying to any part of a long question works), or
- just send a message: it goes to the newest open question.

The message then changes to `✅ Redis`, and Claude continues.

**Mute while you are at your desk.**

```bash
ask-me mute     # silences notify and hooks
ask-me unmute
```

`ask` is never muted, because a waiting question would otherwise block Claude.

**Send messages from your own scripts.**

```bash
long-build.sh && ask-me notify "Build finished" --level success \
               || ask-me notify "Build failed" --level important
```

## Reference

### CLI commands

| Command | What it does |
|---------|--------------|
| `ask-me setup` | Validates the token, captures your chat id, writes the config |
| `ask-me test` | Sends `ask-me is working ✅` |
| `ask-me notify "<text>" [--level <level>] [--context <label>]` | Sends a message. Levels: `info` (default), `success`, `warning`, `important`. `--context` is a short label of the task, shown in the header (max 60 characters) |
| `ask-me hook` | Reads a Claude Code hook event from stdin. Always exits 0 and prints nothing to stdout |
| `ask-me mcp` | Runs the MCP server over stdio (Claude Code starts it for you) |
| `ask-me mute` | Mutes `notify` and hooks. Does not affect `ask` |
| `ask-me unmute` | Lifts the mute |
| `ask-me status` | Shows whether ask-me is configured, the masked token, the chat id and the mute state |
| `ask-me help` | Prints usage |

`python -m ask_me <command>` works too.

### MCP tools

**`notify({ message, level?, context? })`** — send a one-way message.

| Parameter | Default | Notes |
|-----------|---------|-------|
| `message` | required | Plain text. Texts over 4096 characters are split into several messages |
| `level` | `info` | `info` ℹ️, `success` ✅, `warning` ⚠️, `important` 🚨 |
| `context` | none | 2–6 word label of the task Claude is working on (for example `refactor auth`). Shown as `📝` in the header. Whitespace is collapsed and the label is cut at 60 characters |

Returns the plain text `sent` or `muted`.

**`ask({ question, options?, allow_free_text?, timeout_seconds?, context? })`** — ask and wait.

| Parameter | Default | Notes |
|-----------|---------|-------|
| `question` | required | Shown with `❓` below the context header. A long question is split into several messages; the buttons are on the last one |
| `options` | none | Up to 10 buttons. Omit them for a free-text question |
| `allow_free_text` | `true` | Accept typed replies as well as buttons |
| `timeout_seconds` | `askTimeoutSeconds` (1500) | Maximum 86400 |
| `context` | none | Same as in `notify` |

Returns JSON text:

```json
{ "answered": true, "answer": "Redis", "via": "button" }
```

```json
{ "answered": false, "reason": "timeout" }
```

`via` is `"button"` or `"text"`.

### Configuration

The config file is `<configDir>/config.json`. `configDir` is `~/.ask-me` (on Windows `C:\Users\<you>\.ask-me`) unless `ASK_ME_CONFIG_DIR` is set. `ask-me setup` writes it for you.

```json
{
  "telegram": {
    "botToken": "123456789:ABCdef...",
    "chatId": "987654321"
  },
  "askTimeoutSeconds": 1500,
  "maxMessageParts": 5,
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
| `maxMessageParts` | `5` | Maximum number of Telegram messages one text may be split into (minimum 1). Beyond it, the last message ends with `… (truncated, <N> more characters)` |
| `hooks.events` | `["Notification", "Stop"]` | Hook events that ask-me forwards |
| `hooks.notificationTypes` | all | Optional allow-list of `Notification` types, such as `permission_prompt`, `idle_prompt`, `elicitation_dialog` |

Environment variables take precedence over the file. They are useful in CI or containers:

| Variable | Overrides |
|----------|-----------|
| `ASK_ME_TELEGRAM_TOKEN` | `telegram.botToken` |
| `ASK_ME_TELEGRAM_CHAT_ID` | `telegram.chatId` |
| `ASK_ME_CONFIG_DIR` | The config directory |
| `ASK_ME_PROJECT` | The project name shown in the message header (instead of the git repository or folder name) |

Runtime state lives next to the config: `pending/` (open questions), `inbox/` (received updates), `state/offset` (last confirmed Telegram update) and `muted`.

## Long waits

Claude Code aborts a stdio MCP tool call after 30 minutes without a response or progress. The defaults work within that limit:

- `ask` sends an MCP progress notification every 60 seconds while it waits, when the request carries a progress token.
- The default `askTimeoutSeconds` is 1500 (25 minutes), below the 30-minute limit.

To wait longer, raise these settings:

1. **ask-me timeout.** Set `askTimeoutSeconds` in the config file, up to 86400.
2. **Claude Code idle timeout.** `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT` sets the idle limit in milliseconds (default `1800000`). Set it to `0` to disable it. Put it in the `env` block of `~/.claude/settings.json` or in your shell.
3. **Overall tool timeout.** `MCP_TOOL_TIMEOUT` (milliseconds) caps the total time of one call. It is very long by default, so you rarely need to change it.

When a question times out, Claude receives `{"answered": false, "reason": "timeout"}`. The snippet in `docs/CLAUDE.snippet.md` tells it to pick the safest reversible option or stop and notify you.

## Security

- **The token** lives in `~/.ask-me/config.json`, outside any repository. On macOS and Linux the file is written with mode `0600`.
- **Only your chat counts.** Messages and button taps from any chat other than `telegram.chatId` are ignored.
- **The bot cannot message strangers.** Telegram only lets a bot write to people who pressed Start.
- **Never commit `.env` or `config.json`.** The repository's `.gitignore` excludes them.
- **Leaked token?** Open [@BotFather](https://t.me/BotFather), send `/revoke`, choose your bot, then run `ask-me setup` again with the new token.
- **Keep messages free of secrets.** Messages go through Telegram's servers. The CLAUDE.md snippet tells Claude never to include secrets or large code in them.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ask-me: command not found` | The uv tool directory is not on PATH | `uv tool update-shell`, then open a new terminal |
| `ask-me is not configured. Run: ask-me setup` | No config file and no environment variables | Run `ask-me setup` |
| Setup times out waiting for `/start` | `/start` was not sent within 120 s, or was sent to another bot | Run `ask-me setup` again and press Start in the bot the wizard links to |
| Setup says the bot has a webhook set | Another tool configured a webhook on this bot | Remove the webhook (Bot API `deleteWebhook`) or create a new bot for ask-me |
| `ask-me test` works but Claude never notifies | The MCP server is not registered, or Claude has no instructions | Check `claude mcp list`, paste the CLAUDE.md snippet and restart Claude Code |
| No message when Claude finishes | Hooks are not in `~/.claude/settings.json`, or ask-me is muted | Check the hooks block and run `ask-me status` |
| Claude Code cannot find `ask-me hook` | Claude Code uses a different PATH | Use the absolute path from `uv tool dir --bin` |
| `ask` stops after about 30 minutes | Claude Code's MCP idle timeout | See [Long waits](#long-waits) |
| Your answer went to the wrong question | A plain message goes to the newest open question | Reply to the specific question message instead |

`ask-me status` is the quickest health check. Hook errors are written to stderr and never shown to Claude.

## Update and uninstall

**Update to the latest version:**

```bash
uv tool install --force git+https://github.com/mauroturner/ask-me
```

**Uninstall:**

```bash
claude mcp remove ask-me --scope user   # unregister the MCP server
uv tool uninstall ask-me                # remove the command
```

Then delete the `hooks` entries for `ask-me` from `~/.claude/settings.json`, and delete `~/.ask-me/` to remove your token and state. You can also delete the bot with `/deletebot` in @BotFather.

## FAQ

**Why Telegram and not WhatsApp?**
Telegram's Bot API is free. You create a bot with @BotFather in about a minute, and it uses plain HTTPS and JSON with built-in buttons and replies. WhatsApp's official Cloud API requires a Meta business account, a dedicated phone number, pre-approved message templates and a 24-hour customer-service window. Unofficial WhatsApp libraries risk getting your account banned, and Twilio is paid. ask-me ships Telegram behind a `Channel` interface, so WhatsApp can be added later.

**Can several Claude sessions use the same bot at once?**
Yes. All sessions share one inbox, and each one takes only the answers to its own questions. Reply to a specific message to answer that question.

**Does ask-me send my code anywhere?**
Only the text Claude puts in `notify` and `ask`, the context header (project name, git branch, task label, short session id) and, for the `Stop` hook, Claude's last message. Nothing else leaves your machine.

**How do I tell several sessions apart?**
Every message starts with a header such as `📁 my-project · 🌿 feat/cache · 📝 cache layer`. The project and branch come from Claude's working directory, the `📝` label comes from the `context` parameter that the CLAUDE.md snippet asks Claude to set, and hook messages carry the first 6 characters of the session id (`🧵`). Set `ASK_ME_PROJECT` to use your own project name, for example per terminal.

**Can I use it without Claude Code?**
Yes. `ask-me notify` works from any script, CI job or other agent.

**Does it cost anything?**
No. The Telegram Bot API is free.

## Limitations

- Telegram only, for now.
- One chat per installation.
- Uses long polling, not webhooks.
- Not published to PyPI yet. Install from the repository.
- If a Claude session is killed while an `ask` is waiting, its question stays open until its timeout. Until then, plain (non-reply) messages go to that dead question. Reply to the specific message to reach a live one.
- Tapping a button on a question that already expired does nothing (Telegram may show a loading spinner briefly).

## Project structure

```
src/ask_me/
├── cli.py            # `ask-me` command and subcommands
├── setup_wizard.py   # `ask-me setup`
├── config.py         # config file, env overrides, mute flag
├── telegram.py       # async Telegram Bot API client (httpx)
├── channel.py        # channel-neutral interface and message splitting (Telegram today, WhatsApp later)
├── notify.py         # message levels and formatting
├── context.py        # message header: project, branch, task label, session
├── ask.py            # ask flow: send question, wait, match the answer
├── inbox.py          # shared on-disk inbox for concurrent sessions
├── hook.py           # Claude Code Notification/Stop hook handler
└── mcp_server.py     # MCP server exposing `notify` and `ask`
tests/                # pytest suite with a fake Telegram (httpx.MockTransport)
docs/CLAUDE.snippet.md
```

## Contributing

```bash
git clone https://github.com/mauroturner/ask-me && cd ask-me
uv sync
uv run pytest
uv run ask-me help
```

The tests never call the real Telegram API, so they need no token. Pull requests are welcome. A good first contribution is a WhatsApp channel behind the `Channel` interface.

## License

MIT. See [LICENSE](LICENSE).
