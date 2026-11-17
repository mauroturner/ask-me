# ask-me — Telegram MVP

Objective: let an autonomous Claude Code agent reach its (away) human over Telegram — one-way notifications and two-way decisions answered from the phone.
Branch: `feat/telegram-mvp` · Delivery: `exception-ok` (greenfield repo, no remote yet; work-unit commits on one branch) · Runner: `pytest` (`uv run pytest`).

## Specs

### S0 — Channel decision (Telegram over WhatsApp)
Telegram Bot API: free, a bot is created in a minute with @BotFather, plain HTTPS + JSON, inline buttons and replies for two-way answers, no business verification. WhatsApp: the official Meta Cloud API needs a Meta business account, a dedicated phone number, pre-approved message templates and a 24h customer-service window; unofficial libraries (whatsapp-web.js, Baileys) risk account bans; Twilio is paid. → MVP ships Telegram only, behind a `Channel` interface so WhatsApp can be added later.

### S1 — Package & stack
- Python >= 3.10, `src/ask_me/` layout, `pyproject.toml` (hatchling build backend), managed with `uv` (`uv.lock` committed). Runtime deps only `mcp` (official MCP Python SDK, FastMCP) and `httpx`. Dev deps (`[dependency-groups] dev`): `pytest` (async tests via the `anyio` pytest plugin that ships with anyio).
- One console script `ask-me = "ask_me.cli:main"` (`[project.scripts]`; on Windows pip/uv generate a real `ask-me.exe`) with subcommands: `setup`, `test`, `notify`, `hook`, `mcp`, `mute`, `unmute`, `status`, `help`. `python -m ask_me` works too.
- Install for users: `uv tool install git+https://github.com/mauroturner/ask-me` (or `pipx install …`). Works on Windows, macOS and Linux.
- Type hints throughout; standard library first.
- License MIT. No telemetry. Never log the bot token.

### S2 — Configuration & secrets
- Config file: `<configDir>/config.json` where `configDir` = `ASK_ME_CONFIG_DIR` env or `~/.ask-me`. Outside any repo by design.
- Fields: `telegram.botToken`, `telegram.chatId`, `askTimeoutSeconds` (default 1500), `hooks.events` (default `["Notification","Stop"]`).
- Env overrides take precedence: `ASK_ME_TELEGRAM_TOKEN`, `ASK_ME_TELEGRAM_CHAT_ID`.
- On POSIX the config file is written with mode `0600`.
- Missing token/chat → clear error: `ask-me is not configured. Run: ask-me setup`.
- `.env.example` documents the env vars with placeholder values only.

### S3 — `ask-me setup`
Interactive wizard: reads token (prompt or env), validates it with `getMe`, tells the user to open `t.me/<bot_username>` and send `/start`, long-polls `getUpdates` (max 120 s) to capture `message.chat.id` of a private chat, sends a confirmation message, writes the config (S2). Fails with a clear message if a webhook is set or the token is invalid.

### S4 — Telegram client
- `sendMessage` plain text (no `parse_mode`), truncated to the 4096-char limit with a `…` suffix.
- Optional inline keyboard; `editMessageText`/`editMessageReplyMarkup` to remove buttons once answered or expired; `answerCallbackQuery` on every handled callback.
- HTTP 429 → wait `parameters.retry_after` seconds and retry (max 3). Network errors surface as typed errors.
- Only updates whose chat id equals the configured `chatId` are ever considered; everything else is ignored.
- Base URL and the `httpx` transport are injectable for tests (`httpx.MockTransport`).

### S5 — `ask` (two-way decision) with a shared inbox
- Sends the question (prefixed `❓`, includes project folder name when known) with one button per option (max 10; `callback_data` = `am:<nonce>:<index>`, ≤ 64 bytes).
- Free-text answers accepted when `allowFreeText` (default true): a reply to the question message (`reply_to_message.message_id`), or a plain message which goes to the newest pending question.
- Questions with no options use `reply_markup: { force_reply: true }` so the phone opens the reply box directly (a message carries one `reply_markup`, so buttons and force_reply are exclusive).
- Returns `{ answered: true, answer, via: "button" | "text" }` or `{ answered: false, reason: "timeout" }`. On answer, the message is edited to show `✅ <answer>`; on timeout to `⌛ expired`.
- Concurrency (several Claude sessions on one bot): pending questions are registered under `<configDir>/pending/<nonce>.json`; any process that long-polls `getUpdates` persists the offset in `<configDir>/state/offset` and writes every update from the configured chat to `<configDir>/inbox/<update_id>.json`; each asker claims its match by atomic `rename` (a failed rename means another process claimed it). HTTP 409 Conflict (another poller) → short random backoff, then read the inbox. Inbox files older than 24 h are pruned. Pending file removed on answer/timeout.
- `timeoutSeconds` default from config (1500), max 86400.
- Claude Code aborts a stdio MCP tool call after 30 min without a response or progress (`CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT`, default 1800000 ms). The MCP `ask` tool therefore sends an MCP progress notification every 60 s while waiting when the request carries a `progressToken`; default timeout stays below 30 min (1500 s); README documents raising both.

### S6 — MCP server (`ask-me mcp`, stdio)
Tools:
- `notify({ message, level? })` — level `info|success|warning|important` (emoji prefix ℹ️ ✅ ⚠️ 🚨). Returns `sent` (or `muted`).
- `ask({ question, options?, allow_free_text?, timeout_seconds? })` — S5; returns JSON text of the result.
Tool descriptions must tell Claude when to use them: notify when the user asked to be notified ("notify me when you finish", "avisame cuando termines", "this is important"); ask when a decision is needed and the user said they are away / unattended.

### S7 — `ask-me hook` (Claude Code hooks)
- Reads the hook JSON from stdin (fields per https://code.claude.com/docs/en/hooks: common `session_id`, `transcript_path`, `cwd`, `hook_event_name`; Notification adds `message`, optional `title`, `notification_type` e.g. `permission_prompt`, `idle_prompt`, `elicitation_dialog`; Stop adds `stop_hook_active`, `last_assistant_message`). `<project>` = basename of `cwd`.
- `Notification` → `🔔 Claude needs you (<project>): <message>`. `Stop` → `✅ Claude finished (<project>)` + an excerpt (≤ 800 chars) of `last_assistant_message` when present. Unknown/missing fields never crash.
- Config `hooks.notificationTypes` (optional allow-list; default all) filters Notification by `notification_type`.
- Skips events not in `hooks.events`; skips when muted; skips `Stop` when `stop_hook_active` is true.
- ALWAYS exits 0 and prints nothing to stdout (never blocks or alters Claude); errors go to stderr.

### S8 — `notify`, `test`, `mute`, `unmute`, `status`
- `ask-me notify "<text>" [--level <level>]` sends a message (usable from any script/agent).
- `ask-me test` sends `ask-me is working ✅`.
- `ask-me mute` / `unmute` toggle `<configDir>/muted` (mutes notify + hooks, never `ask`).
- `ask-me status` prints configured yes/no, masked token (`123456:****`), chat id, muted state.

### S9 — Docs for people with the same problem
README (English): problem, 5-minute quick start (BotFather → setup → `claude mcp add` → hooks snippet), tools reference, CLAUDE.md snippet so Claude knows when to call notify/ask, security notes, FAQ (why not WhatsApp), limitations. `docs/CLAUDE.snippet.md`. `.env.example`. LICENSE (MIT).

### S10 — Tests (pytest, through public interfaces, fake Telegram via `httpx.MockTransport`)
Config precedence & missing-config error; truncation; 429 retry; foreign chat ignored; ask via button; ask via reply; ask via plain text; ask timeout edits message; two concurrent asks each get their own answer; hook Notification/Stop formatting, `last_assistant_message` excerpt, notificationTypes filter, `stop_hook_active` skip, exit 0 on network failure; mute; MCP lists `notify` and `ask`.

## Tasks
- [x] T1 — S1, S2, S4: scaffold, config, Telegram client + tests — delegated (Sonnet writer, user request) — commit `4c66632`
- [x] T2 — S5: ask + shared inbox + tests — delegated (Sonnet writer) — Risk: item 4 (concurrency) — commit `6553cb0` (verification pending T5)
- [x] T3 — S6, S7, S8, S3: MCP server, hook, CLI, setup + tests — delegated (Sonnet writer) — commit `02f0a29`
- [x] T4 — S9: README + docs — delegated (Haiku drafter, user request) — commit `8e91ca3`; `.env.example` left uncommitted (agent cannot read `.env*`; user commits it)
- [ ] T5 — verification of T2 concurrency (independent verifier, Risk item 4)

## Log
- L1 (2026-11-17, user request, verbatim):
  > Intención: En ocasiones dejo trabajando de manera autonoma a un agente como Claude, y a veces necesita que yo tome una decisión y no estoy en la computadora. Mi idea es hacer que me mande un whatsapp o un telegram
  > Nombre del repositorio: ask-me · Descripción: sugerí una · tags: sugerí.
  > Objetivo: 1. Cada vez que Claude necesite la confirmación de algún punto con el usuario, o bien deja un mensaje importante en el chat, el mismo manda un telegram o un whatsapp (el usuario dice por ejemplo, avisame cuando termines esta tarea, o esta tarea es importante, etc...) a. Determiná que es más facil de hacer telegram o whatsapp b. Hace el plan de implementación técnico, requerimientos, tests, etc... c. Lanza un agente de sonnet para programar d. Lanza agentes haiku para tareas sencillas bien definidas como investigar algo particular, redactar, etc... e. La idea es que el repositorio sea de utilidad para quien comparte este mismo inconviente f. Haz commits, y no te olvides de que cualquier dato sensible debe ser ignorado en .gitignore.
- L2: Decision S0 — Telegram. Two Haiku research agents launched (Claude Code hooks/MCP payloads; Telegram Bot API facts).
- L4: Hooks research (official docs): Stop payload carries `last_assistant_message` (preferred over transcript, which may lag); Notification/Stop command hooks default 600 s timeout; exit 0 = no effect, exit 2 on Stop blocks stopping → hook must always exit 0. stdio MCP idle timeout 30 min → progress notifications + default ask timeout 1500 s. On Windows exec-form hooks can't spawn `.cmd` shims → document `node <abs>/dist/cli.js hook`.
- L5: Telegram research (core.telegram.org/bots/api, FAQ): text 1–4096 chars; callback_data 1–64 bytes; offset = last update_id + 1 confirms; unconfirmed updates kept ≤ 24 h; getUpdates fails while a webhook is set; answerCallbackQuery required; ~1 msg/s per chat; 429 carries `parameters.retry_after`. 409 on concurrent pollers is community-reported, not in official docs → handled defensively.
- L6: T1–T3 delegated to one Sonnet writer (user asked for it); T4 drafted by Haiku in parallel from the specs (disjoint surfaces). Parent owns git and commits in work units.
- L7 (2026-11-17, user, verbatim): "por cierto, hacé tus pushs sobre este repo 'https://github.com/mauroturner/ask-me'" → remote `origin` added, `main` pushed. `.env.example` created by the user (agent writes to `.env*` are denied by permission settings).
- L8 (2026-11-17, user, verbatim): "por cierto, no te comenté. pero me gustaría que por defecto la programación esté en python." → TypeScript writer stopped mid-T1 (only RED tests existed), its untracked files removed; S1/S4/S10 rewritten for Python + uv + httpx + official `mcp` SDK; T1–T4 reopened. Side benefit: console scripts are real `.exe` on Windows, so `ask-me hook` works in hook exec form.
- L9: Writer result: `uv run pytest -q` 104 passed, 1 skipped (POSIX 0600 test on Windows); parent spot check identical. Accepted deviations: installed `mcp` is 2.3.0 → `mcp.server.mcpserver.MCPServer` (no `fastmcp`); edited messages keep the question and append `✅ <answer>` / `⌛ expired`; plain messages older than the question (−5 s) are ignored; a 409 mentioning "webhook" is a hard error; >10 options rejected. Open: small stale-offset race (mitigated), callbacks on expired questions are not answered. `.gitignore` gets `!uv.lock`.
- L10: Native assess `a8146d0..HEAD` (committed-only, untracked excluded): risk medium, review_due false (under_budget). T2 to be escalated as item 4 after T5.
- L11: T5 independent verifier (Sonnet, scratch clone): S0–S2, S4, S7, S8, S10 PASS; S3/S6/S9 not checked (need live bot). BLOCKER: `Inbox.claim` via `os.rename` not exclusive on Windows (2–16 winners in stress). Fix `fe77a65`: exclusive `O_CREAT|O_EXCL` lock file kept until prune; RED observed on old code (3 winners, redelivery double-claim), GREEN 107 passed 1 skipped. Advisories documented in README Limitations, not fixed: dead pending file captures plain text until its timeout; stale callbacks not acknowledged; offset read-then-write may regress (store skips < offset).
- L3: Delivery `exception-ok`: forecast ~1800 authored lines, greenfield repo with no remote, so no PR chain; work-unit commits on `feat/telegram-mvp`.
