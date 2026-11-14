# CLAUDE.md

ccmux — Telegram bot that bridges Telegram Forum topics to Claude Code sessions via tmux windows. Each topic is bound to one tmux window running one Claude Code instance.

Tech stack: Python, python-telegram-bot, tmux, uv.

## Common Commands

```bash
uv run ruff check src/ tests/         # Lint — MUST pass before committing
uv run ruff format src/ tests/        # Format — auto-fix, then verify with --check
uv run pyright src/ccbot/             # Type check — MUST be 0 errors (venv set in pyproject [tool.pyright])
uv run pytest --tb=short -q           # Tests — MUST pass (pythonpath set in pyproject [tool.pytest.ini_options])
# on a fresh checkout first: uv sync --frozen --extra voice --extra dev
# (scripts/typecheck.py and scripts/run_tests.py do not exist in this repo)
# Under systemd, the service is restarted with:
systemctl --user restart ccbot
# ./scripts/restart.sh is only for a bot started with `uv run ccbot` in the tmux `__main__` window
./scripts/restart.sh
ccbot new [DIR]                       # Create a session + Telegram topic via the running bot
ccbot selfcheck                       # Check the terminal parser against the installed Claude Code
ccbot hook --install                  # Auto-install the five hooks (SessionStart, SessionEnd, Notification, Stop, StopFailure)
```

## Core Design Constraints

- **1 Topic = 1 Window = 1 Session** — all internal routing keyed by tmux window ID (`@0`, `@12`), not window name. Window names kept as display names. Same directory can have multiple windows.
- **Topic-only** — no backward-compat for non-topic mode. No `active_sessions`, no `/list`, no General topic routing.
- **No message truncation** at parse layer — splitting only at send layer (`split_message`, 4096 char limit).
- **MarkdownV2 only** — use `safe_reply`/`safe_edit`/`safe_send` helpers (auto fallback to plain text). Internal queue/UI code calls bot API directly with its own fallback.
- **Hook-based session tracking** — `SessionStart` hook writes `session_map.json`; monitor polls it to detect session changes. Other hook events (`SessionEnd`, `Notification`, `Stop`, `StopFailure`) are appended to `hook_events.jsonl`, which the bot reads.
- **Message queue per user** — FIFO ordering, message merging (3800 char limit), tool_use/tool_result pairing.
- **Rate limiting** — `AIORateLimiter(max_retries=5)` on the Application (30/s global). On restart, the global bucket is pre-filled to avoid burst against Telegram's server-side counter.

## Code Conventions

- Every `.py` file starts with a module-level docstring: purpose clear within 10 lines, one-sentence summary first line, then core responsibilities and key components.
- Telegram interaction: prefer inline keyboards over reply keyboards; use `edit_message_text` for in-place updates; keep callback data under 64 bytes; use `answer_callback_query` for instant feedback.

## Configuration

- Config directory: `~/.ccbot/` by default, override with `CCBOT_DIR` env var.
- `.env` loading priority: local `.env` > config dir `.env`.
- State files: `state.json` (thread bindings), `session_map.json` (hook-generated), `monitor_state.json` (byte offsets), `hook_events.jsonl` (hook event log read by the bot), `control/` (request/result files of `ccbot new`).

## Hook Configuration

Auto-install: `ccbot hook --install` (adds the five hooks above; idempotent; never modifies other tools' hooks; re-run after upgrading ccbot)

Installed events: `SessionStart`, `SessionEnd`, `Notification`, `Stop`, `StopFailure` (the last three are `async`).

Or manually in `~/.claude/settings.json`:
```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5 }]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5 }]
      }
    ],
    "Notification": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5, "async": true }]
      }
    ],
    "Stop": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5, "async": true }]
      }
    ],
    "StopFailure": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5, "async": true }]
      }
    ]
  }
}
```

## Architecture Details

See @.claude/rules/architecture.md for full system diagram and module inventory.
See @.claude/rules/topic-architecture.md for topic→window→session mapping details.
See @.claude/rules/message-handling.md for message queue, merging, and rate limiting.
