# ask-me — Telegram MVP

Objective: let an autonomous Claude Code agent reach its (away) human over Telegram — one-way notifications and two-way decisions answered from the phone.
Branch: `feat/telegram-mvp` · Delivery: `exception-ok` (greenfield repo, no remote yet; work-unit commits on one branch) · Runner: `vitest` (`npm test`), typecheck `npm run typecheck`.

## Specs

### S0 — Channel decision (Telegram over WhatsApp)
Telegram Bot API: free, a bot is created in a minute with @BotFather, plain HTTPS + JSON, inline buttons and replies for two-way answers, no business verification. WhatsApp: the official Meta Cloud API needs a Meta business account, a dedicated phone number, pre-approved message templates and a 24h customer-service window; unofficial libraries (whatsapp-web.js, Baileys) risk account bans; Twilio is paid. → MVP ships Telegram only, behind a `Channel` interface so WhatsApp can be added later.

### S1 — Package & stack
- TypeScript, ESM, Node >= 20, native `fetch`. Runtime deps only `@modelcontextprotocol/sdk` and `zod`. Dev deps: `typescript`, `vitest`, `@types/node`.
- One binary `ask-me` (`package.json#bin`) with subcommands: `setup`, `test`, `notify`, `hook`, `mcp`, `mute`, `unmute`, `status`, `help`.
- Scripts: `build`, `typecheck`, `test`. Works on Windows, macOS and Linux.
- License MIT. No telemetry. Never log the bot token.

### S2 — Configuration & secrets
- Config file: `<configDir>/config.json` where `configDir` = `ASK_ME_CONFIG_DIR` env or `~/.ask-me`. Outside any repo by design.
- Fields: `telegram.botToken`, `telegram.chatId`, `askTimeoutSeconds` (default 1800), `hooks.events` (default `["Notification","Stop"]`).
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
- Base URL and `fetch` are injectable for tests.

### S5 — `ask` (two-way decision) with a shared inbox
- Sends the question (prefixed `❓`, includes project folder name when known) with one button per option (max 10; `callback_data` = `am:<nonce>:<index>`, ≤ 64 bytes).
- Free-text answers accepted when `allowFreeText` (default true): a reply to the question message (`reply_to_message.message_id`), or a plain message which goes to the newest pending question.
- Returns `{ answered: true, answer, via: "button" | "text" }` or `{ answered: false, reason: "timeout" }`. On answer, the message is edited to show `✅ <answer>`; on timeout to `⌛ expired`.
- Concurrency (several Claude sessions on one bot): pending questions are registered under `<configDir>/pending/<nonce>.json`; any process that long-polls `getUpdates` persists the offset in `<configDir>/state/offset` and writes every update from the configured chat to `<configDir>/inbox/<update_id>.json`; each asker claims its match by atomic `rename` (a failed rename means another process claimed it). HTTP 409 Conflict (another poller) → short random backoff, then read the inbox. Inbox files older than 24 h are pruned. Pending file removed on answer/timeout.
- `timeoutSeconds` default from config (1800), max 86400.

### S6 — MCP server (`ask-me mcp`, stdio)
Tools:
- `notify({ message, level? })` — level `info|success|warning|important` (emoji prefix ℹ️ ✅ ⚠️ 🚨). Returns `sent` (or `muted`).
- `ask({ question, options?, allow_free_text?, timeout_seconds? })` — S5; returns JSON text of the result.
Tool descriptions must tell Claude when to use them: notify when the user asked to be notified ("notify me when you finish", "avisame cuando termines", "this is important"); ask when a decision is needed and the user said they are away / unattended.

### S7 — `ask-me hook` (Claude Code hooks)
- Reads the hook JSON from stdin. `Notification` → `🔔 Claude needs you (<project>): <message>`. `Stop` → `✅ Claude finished (<project>)` + an excerpt (≤ 800 chars) of the last assistant message read from `transcript_path` (JSONL) when available.
- Skips events not in `hooks.events`; skips when muted; skips `Stop` when `stop_hook_active` is true.
- ALWAYS exits 0 and prints nothing to stdout (never blocks or alters Claude); errors go to stderr.

### S8 — `notify`, `test`, `mute`, `unmute`, `status`
- `ask-me notify "<text>" [--level <level>]` sends a message (usable from any script/agent).
- `ask-me test` sends `ask-me is working ✅`.
- `ask-me mute` / `unmute` toggle `<configDir>/muted` (mutes notify + hooks, never `ask`).
- `ask-me status` prints configured yes/no, masked token (`123456:****`), chat id, muted state.

### S9 — Docs for people with the same problem
README (English): problem, 5-minute quick start (BotFather → setup → `claude mcp add` → hooks snippet), tools reference, CLAUDE.md snippet so Claude knows when to call notify/ask, security notes, FAQ (why not WhatsApp), limitations. `docs/CLAUDE.snippet.md`. `.env.example`. LICENSE (MIT).

### S10 — Tests (vitest, through public interfaces, fake Telegram)
Config precedence & missing-config error; truncation; 429 retry; foreign chat ignored; ask via button; ask via reply; ask via plain text; ask timeout edits message; two concurrent asks each get their own answer; hook Notification/Stop formatting, transcript excerpt, `stop_hook_active` skip, exit 0 on network failure; mute; MCP lists `notify` and `ask`.

## Tasks
- [ ] T1 — S1, S2, S4: scaffold, config, Telegram client + tests — delegated (Sonnet writer, user request)
- [ ] T2 — S5: ask + shared inbox + tests — delegated (Sonnet writer) — Risk: item 4 (concurrency)
- [ ] T3 — S6, S7, S8, S3: MCP server, hook, CLI, setup + tests — delegated (Sonnet writer)
- [ ] T4 — S9: README + docs — delegated (Haiku drafter, user request)
- [ ] T5 — verification of T2 concurrency (independent verifier, Risk item 4)

## Log
- L1 (2026-11-17, user request, verbatim):
  > Intención: En ocasiones dejo trabajando de manera autonoma a un agente como Claude, y a veces necesita que yo tome una decisión y no estoy en la computadora. Mi idea es hacer que me mande un whatsapp o un telegram
  > Nombre del repositorio: ask-me · Descripción: sugerí una · tags: sugerí.
  > Objetivo: 1. Cada vez que Claude necesite la confirmación de algún punto con el usuario, o bien deja un mensaje importante en el chat, el mismo manda un telegram o un whatsapp (el usuario dice por ejemplo, avisame cuando termines esta tarea, o esta tarea es importante, etc...) a. Determiná que es más facil de hacer telegram o whatsapp b. Hace el plan de implementación técnico, requerimientos, tests, etc... c. Lanza un agente de sonnet para programar d. Lanza agentes haiku para tareas sencillas bien definidas como investigar algo particular, redactar, etc... e. La idea es que el repositorio sea de utilidad para quien comparte este mismo inconviente f. Haz commits, y no te olvides de que cualquier dato sensible debe ser ignorado en .gitignore.
- L2: Decision S0 — Telegram. Two Haiku research agents launched (Claude Code hooks/MCP payloads; Telegram Bot API facts).
- L3: Delivery `exception-ok`: forecast ~1800 authored lines, greenfield repo with no remote, so no PR chain; work-unit commits on `feat/telegram-mvp`.
