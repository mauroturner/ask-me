## ask-me (Telegram notifications and decisions)

When the user asks to be notified ("notify me when you finish", "avisame cuando termines", "let me know", "this is important"):

- Call the `ask-me` MCP tool `notify` when the task finishes or the important event happens. Use a 1–3 line summary.
- Always pass `context` (a 2–6 word label of the task you are working on, e.g. "refactor auth") to `notify` and `ask`, so the user can tell parallel sessions apart.

When the user says they are away or the session is unattended, and you need a decision or confirmation:

- Call the `ask-me` MCP tool `ask` with concise options instead of stopping to wait. Act on the answer.
- If it times out, choose the safest reversible option, or stop and call `notify` to report what is blocked.

Rules:

- Keep messages short. Never include secrets, tokens, or large code blocks.
- Do not spam. Send at most one `notify` per finished task, unless the user asks for progress updates.
