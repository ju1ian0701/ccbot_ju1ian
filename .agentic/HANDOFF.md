# Handoff: ccbot_ju1ian — agentic pipeline + refactoring

**Дата:** 2026-07-15  
**Назначение документа:** передаточный контекст для LLM-агента (GPT / Claude / Grok / др.). Прочитай этот файл целиком — дальше работай без уточнений, если путь/репо доступны.  
**Репозиторий (форк):** https://github.com/ju1ian0701/ccbot_ju1ian  
**Локальный клон:** `D:\CCbot_tmux\ccbot\ccbot_ju1ian\`  
**Upstream (оригинал):** https://github.com/six-ddc/ccbot (fork tracking)  
**Рабочая копия workspace (отчёты/план):** `D:\GROK\`

---

## 1. Цель проекта

### 1.1. Продукт

**ccbot** — Telegram-бот, который связывает **forum topics** с **tmux-окнами**, в каждом из которых крутится Claude Code / Codex-сессия. Архитектура **topic-only**: 1 topic = 1 window (`@N`) = 1 session. Стек: Python ≥3.12, `python-telegram-bot`, libtmux, `uv`.

### 1.2. Задача этой работы (два слоя)

| Слой | Зачем |
|------|--------|
| **A. Agentic pipeline** | Полный цикл: knowledge graph + refactoring backlog → ранжирование задач → implement-prompt → (CI/MCP) → draft PR, с guardrails и quality gates |
| **B. Рефакторинг кода** | Выполнить backlog `REF-001`…`REF-009` в локальном клоне: DRY auth, split bot.py, error handling, state managers, migration isolation, callback_data types, renames, send fallback, tests |

**Исходные входы анализа:**
- Knowledge graph: `D:\CCbot_tmux\ccbot\ccbot_ju1ian\.understand-anything\knowledge-graph.json` (~530 nodes / 555 edges, commit `9d7ab97…`)
- Human refactoring report: `D:\GROK\reports\ccbot-refactoring-backlog.md` (5 HIGH + 4 MEDIUM)

---

## 2. Что уже сделано

### 2.1. Agentic infrastructure (pipeline)

| Компонент | Результат |
|-----------|-----------|
| Structured backlog | `.agentic/backlog/tasks.json` — `REF-001`…`REF-009` с AC, files, depends_on, atomic_steps |
| Config / policies | `.agentic/config.json`, `.agentic/policies.json` (allow/deny paths, must/must_not) |
| Stage prompts | `.agentic/prompts/{analyze,plan,implement,review,pr-body}.md` |
| Python toolkit | `scripts/agentic/` — analyze graph, prioritize, render prompt, validate, sync issues, CLI |
| Context packer | `build_context_pack.py` — 1-hop subgraph slice → `.agentic/out/context-pack.json` |
| GitHub Actions | `agentic-analyze.yml`, `agentic-plan.yml`, `agentic-implement.yml`, `agentic-orchestrator.yml` (+ existing `check.yml`, `claude.yml`, `claude-code-review.yml`) |
| Custom MCP | `ccbot-agentic` — `scripts/agentic/mcp_server.py` + `.grok/config.toml` (10 tools) |
| Plan docs | `D:\GROK\reports\ccbot-agentic-pipeline-plan.md`, `.agentic/IMPLEMENTATION_PLAN.md` |
| Implement prompt REF-001 | `.agentic/out/implement-prompt-REF-001.md` |

**CLI (из корня клона):**
```bash
python scripts/agentic/cli.py analyze|plan|select|context|status|list|validate|run|sync-issues
python scripts/agentic/mcp_smoke_test.py
python scripts/agentic/update_backlog_status.py REF-00N done --note "..."
```

**MCP tools (qualified names):**  
`ccbot-agentic__pipeline_status`, `__list_tasks`, `__get_task`, `__select_next_task`, `__get_analysis`, `__get_context_pack`, `__get_implement_prompt`, `__mark_task_status`, `__run_analyze`, `__run_validate`.

### 2.2. Рефакторинг REF-001 … REF-009 (все `status: done`)

| ID | Что сделано | Ключевые артефакты |
|----|-------------|-------------------|
| **REF-001** | Auth + session resolve helper | `src/ccbot/session_guard.py` — `SessionContext`, `require_user`, `require_bound_window_id`, `require_session`; handlers мигрированы; `tests/ccbot/test_session_guard.py` |
| **REF-002** | Split god-module `bot.py` (~1868 → ~190 lines) | `handlers/command_handlers.py`, `message_handlers.py`, `callback_router.py`, `notifications.py`, `window_bind.py`, `screenshot_controls.py`; `bot.py` = lifecycle + registration + re-exports |
| **REF-003** | Typed error handling на hot paths | `src/ccbot/errors.py`; `TelegramError`/`RetryAfter` в sender/queue/tmux/interactive/guard; `tests/ccbot/test_message_sender_errors.py` |
| **REF-004** | Isolate session migration | `src/ccbot/session_migration.py` (pure + documented authoritative model); `SessionManager.resolve_stale_ids` thin orchestrator; `tests/ccbot/test_session_migration.py` (table-driven 6+ cases) |
| **REF-005** | Encapsulate mutable globals | `MessageQueueManager` + `StatusTracker` + singleton `queue_manager`; `InteractiveState`; `BashCaptureTracker`; public wrappers preserved |
| **REF-006** | Typed callback_data | `encode_*` / `parse_*` in `callback_data.py`; producers + `callback_router` use them; 64-byte clip; `tests/ccbot/handlers/test_callback_data.py` |
| **REF-007** | Rename cryptic ids | `wid`→`window_id`, `tid`→`thread_id`, `stored_wid`/`selected_wid`/`created_wid` → descriptive; no public API break |
| **REF-008** | Unify markdown fallback | `_run_markdown_fallback` + `edit_with_fallback`; queue/bash use shared path; `safe_send` → `send_with_fallback` |
| **REF-009** | Coverage on hotspots | `test_tmux_manager.py`, `test_message_queue_enqueue.py`, `test_command_handlers.py`; coverage lift on tmux/queue/commands |

### 2.3. Побочные fixes по ходу

- `tests/conftest.py`: fcntl stub on Windows (import `session.py` without POSIX).
- `enqueue_status_update`: regression after REF-007 fixed — use `thread_key = thread_id or 0` for status map; keep `MessageTask.thread_id` optional (not forced to `0`).
- Guardrails allowlist: `.grok/**`, agentic workflows, etc.

### 2.4. Тесты (последний известный прогон)

```text
uv run pytest tests/ --ignore=tests/ccbot/test_session_monitor.py -q
→ 360 passed (approx; may grow)
```

**Известный flaky/pre-existing на Windows:**  
`tests/ccbot/test_session_monitor.py` — offset off-by-one (CRLF vs LF). На Linux CI обычно OK. Не связано с REF-*.

---

## 3. Текущее состояние

### 3.1. Где остановились

- **Весь refactoring backlog REF-001…009 — status `done` в** `.agentic/backlog/tasks.json`.
- **Код изменён только в локальном клоне** — **подтверждено: не закоммичен / не запушен**.
- `git status`: `main...origin/main` с множеством modified + untracked (`.agentic/`, `scripts/agentic/`, agentic workflows, `session_guard.py`, split handlers, tests, …).
- Agentic CI workflows и MCP зарегистрированы локально; **remote secrets** и push — не делались.
- Grok enrich client (`grok_client.py`) и workflow `agentic-grok-review.yml` описаны в плане, **не реализованы** (optional).

### 3.2. Git (снимок handoff)

```text
Remote: origin = https://github.com/ju1ian0701/ccbot_ju1ian.git
Branch: main...origin/main  (DIRTY — local only)
HEAD:   9d7ab97 fix: harden Telegram delivery and window↔session mapping

Modified (examples): bot.py, session.py, message_queue.py, message_sender.py,
  callback_data.py, interactive_ui.py, history.py, directory_browser.py,
  status_polling.py, tmux_manager.py, .gitignore, tests/conftest.py, ...

Untracked (examples): .agentic/, .grok/, scripts/agentic/, agentic-*.yml,
  session_guard.py, session_migration.py, errors.py,
  handlers/{command_handlers,message_handlers,callback_router,notifications,
  window_bind,screenshot_controls}.py, tests/agentic/, many new test files,
  .understand-anything/, coverage.json (do NOT commit coverage.json)
```

**Действие для нового агента:** `git status` + `git diff --stat`; не коммитить `coverage.json`, `.env`, `.understand-anything/` (по желанию — graph можно ignore).

### 3.3. Архитектура кода сейчас (post-refactor)

```text
src/ccbot/
  bot.py                 # create_bot, post_init/shutdown, re-exports only
  session_guard.py       # require_user / require_session
  session_migration.py   # pure startup migrate
  session.py             # SessionManager (state + tmux orchestration)
  errors.py              # TelegramError helpers + log_exception
  handlers/
    command_handlers.py  # /start /history /esc /unbind /usage /forward /topic*
    message_handlers.py  # text/photo/voice + bash capture
    callback_router.py   # all inline keyboard routing
    message_queue.py     # MessageQueueManager + StatusTracker
    message_sender.py    # _run_markdown_fallback, send/edit/safe_*
    callback_data.py     # encode_*/parse_*
    notifications.py     # handle_new_message
    window_bind.py       # create_and_bind_window
    screenshot_controls.py
    interactive_ui.py    # InteractiveState
    ... (history, directory_browser, status_polling, cleanup, response_builder)
```

**Инварианты (не нарушать):**
1. Topic-only; route by **window @id**, never window name for internal keys.
2. `is_user_allowed` / auth strength.
3. `set_group_chat_id` + `resolve_chat_id` for supergroup forum topics.
4. No message truncation at parse layer; split only at send.
5. MarkdownV2 via `safe_*` / `send_with_fallback` / `edit_with_fallback`.
6. `RetryAfter` always re-raised from send paths.

---

## 4. Что осталось сделать

### 4.1. Обязательный next step (рекомендуемый)

1. **Review + commit** локальных изменений логичными commits (или 1 monorepo-style commit), например:
   - `feat(agentic): pipeline, MCP, workflows, backlog`
   - `refactor: REF-001…009 (session_guard, split bot, errors, …)`
2. **Push** на `origin` (ветка `main` или `feat/agentic-refactor` + PR).
3. **CI:** убедиться, что `check.yml` зелёный на Linux (ruff, pyright, pytest).
4. **Optional secrets:** `CLAUDE_CODE_OAUTH_TOKEN` (уже implied by existing claude workflows), `XAI_API_KEY` если нужен Grok review/enrich.

### 4.2. Не сделано (optional / future)

| Item | Примечание |
|------|------------|
| `scripts/agentic/grok_client.py` | xAI API enrich/review — описан в plan, не в коде |
| `.github/workflows/agentic-grok-review.yml` | PR review via Grok |
| Auto-implement on schedule | `AGENTIC_AUTO_IMPLEMENT` var — default off; keep off for high-risk |
| Custom MCP on other machines | update `CCBOT_REPO_ROOT` in `.grok/config.toml` |
| Full coverage | hotspots improved but not “full”; screenshot.py, session_monitor still thin |
| Upstream sync | not attempted; fork may diverge after local refactor |
| Property-based tests for migrations | table-driven done; hypothesis optional |

### 4.3. Открытые вопросы (не блокируют commit)

- Preferred implementer in CI: Claude Code Action only vs + Grok.
- Commit strategy: one PR vs stack.
- Whether to open GitHub Issues via `sync-issues --apply` (needs `gh` + labels).

### 4.4. Suggested verification after checkout

```powershell
cd D:\CCbot_tmux\ccbot\ccbot_ju1ian
uv sync --all-extras
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/ccbot/   # Windows may warn on fcntl types in session/hook — Linux CI is source of truth
uv run pytest tests/ --ignore=tests/ccbot/test_session_monitor.py -q
python scripts/agentic/cli.py status
python scripts/agentic/cli.py list --status ready
python scripts/agentic/mcp_smoke_test.py
```

---

## 5. Ключевые решения и договорённости

### 5.1. Product / architecture

| Решение | Почему |
|---------|--------|
| **1 topic = 1 window @id = 1 session** | Core AGENTS.md / CLAUDE.md invariant |
| Topic-only, no General-topic routing | Explicit product constraint |
| Window names only for display / re-resolve after tmux restart | Names unstable; @id is authority after migrate |
| Dual state: `state.json` (bindings) + `session_map.json` (hook) | Hook writes session map; bot owns topic bindings |

### 5.2. Refactor design

| Решение | Почему |
|---------|--------|
| `session_guard.py` not `session.py` for Telegram helpers | Avoid circular imports (session ↔ bot/telegram) |
| `require_user` + `require_session` (not one-size-fits-all) | `text_handler` unbound must open directory browser; `start` only needs auth; callbacks use `query.answer` |
| Split bot into handlers/* not packages | Matches existing handlers package style |
| Pure `session_migration.py` | Unit-testable without tmux/fcntl |
| Managers as module singletons (`queue_manager`, `interactive_state`) | Keep public function API stable for imports |
| Callback wire format unchanged | Telegram clients already have old payloads; only builders/parsers centralize |
| Markdown fallback centralized once | DRY + consistent RetryAfter / logging |

### 5.3. Agentic / safety

| Решение | Почему |
|---------|--------|
| Draft PR only; never push main from agent by default | Human merge gate |
| Path allow/deny in config | Blast radius control |
| 1 REF task = 1 PR / one implement run | Reviewability |
| High-risk tasks (split bot, session migrate, concurrency) already done carefully | Still prefer human review before merge |
| MCP stdlib JSON-RPC, no mcp SDK dep | Portable on Windows + CI |

### 5.4. Platform notes

| Note | Impact |
|------|--------|
| Windows has no `fcntl` | `tests/conftest.py` stubs it; production target is Linux/tmux |
| pyright on Windows may flag fcntl attributes | Trust Linux CI |
| session_monitor offset tests flaky on Windows CRLF | Ignore or run on Linux |

---

## 6. Артефакты и файлы

### 6.1. Отчёты (D:\GROK\reports\)

| Файл | Описание |
|------|----------|
| `ccbot-refactoring-backlog.md` | Исходный human/LLM analysis (findings) |
| `ccbot-agentic-pipeline-plan.md` | Полный plan: stages, Grok, workflows, MCP, phases |
| `ccbot-agentic-mcp.md` | (если скопирован) MCP README |
| `implement-prompt-REF-001.md` | (если есть копия) prompt for first implement |
| **`ccbot-handoff.md`** | **Этот документ** |

### 6.2. Agentic config (клон)

| Путь | Описание |
|------|----------|
| `.agentic/README.md` | Pipeline overview |
| `.agentic/IMPLEMENTATION_PLAN.md` | Copy of full plan |
| `.agentic/config.json` | Weights, outputs, guardrails |
| `.agentic/policies.json` | Agent must/must_not |
| `.agentic/backlog/tasks.json` | **Source of truth for REF status** |
| `.agentic/backlog/source-report.md` | Copy of refactoring report |
| `.agentic/prompts/*` | Stage prompts |
| `.agentic/out/*` | Generated (gitignored): analysis, plan, implement-prompt, context-pack |
| `.agentic/mcp/README.md` | MCP usage |
| `.grok/config.toml` | Project MCP registration (`CCBOT_REPO_ROOT` hard-coded to local path) |
| `.github/workflows/agentic-*.yml` | CI orchestration |
| `scripts/agentic/*` | Toolkit + MCP server |
| `tests/agentic/test_pipeline.py` | Pipeline unit tests |

### 6.3. Product code (new / major)

| Путь | Описание |
|------|----------|
| `src/ccbot/session_guard.py` | Auth + session resolve |
| `src/ccbot/session_migration.py` | Pure migrate / stale resolve |
| `src/ccbot/errors.py` | Typed Telegram logging helpers |
| `src/ccbot/bot.py` | Thin app factory |
| `src/ccbot/handlers/command_handlers.py` | Commands + topics |
| `src/ccbot/handlers/message_handlers.py` | text/photo/voice |
| `src/ccbot/handlers/callback_router.py` | Callbacks |
| `src/ccbot/handlers/message_queue.py` | Queue manager |
| `src/ccbot/handlers/message_sender.py` | Unified markdown fallback |
| `src/ccbot/handlers/callback_data.py` | encode/parse |
| `src/ccbot/handlers/notifications.py` | Outbound assistant messages |
| `src/ccbot/handlers/window_bind.py` | Create+bind window |
| `src/ccbot/handlers/screenshot_controls.py` | Screenshot keyboard |

### 6.4. New / important tests

| Путь | Покрытие |
|------|----------|
| `tests/ccbot/test_session_guard.py` | require_session scenarios |
| `tests/ccbot/test_session_migration.py` | migrate table cases |
| `tests/ccbot/test_message_sender_errors.py` | fallback + RetryAfter |
| `tests/ccbot/test_tmux_manager.py` | mocked tmux |
| `tests/ccbot/handlers/test_callback_data.py` | round-trip callbacks |
| `tests/ccbot/handlers/test_message_queue_state.py` | managers |
| `tests/ccbot/handlers/test_message_queue_enqueue.py` | enqueue/worker |
| `tests/ccbot/handlers/test_command_handlers.py` | start/esc/unbind |
| `tests/agentic/test_pipeline.py` | pipeline toolkit |

---

## 7. Контекст для нового агента

### 7.1. Стек и команды

```text
OS used in development: Windows (PowerShell)
Target runtime: Linux + tmux
Python: ≥3.12 (dev env may be 3.14)
Package manager: uv
Quality gates (must pass before claim done):
  uv sync --all-extras
  uv run ruff check src/ tests/
  uv run ruff format src/ tests/   # or --check in CI
  uv run pyright src/ccbot/
  uv run pytest --tb=short -q
```

### 7.2. Стиль кода (из AGENTS.md / CLAUDE.md)

- Module-level docstring on every new `.py` (purpose first line + responsibilities).
- Prefer existing patterns: `atomic_write_json`, env scrubbing, `safe_*` send helpers.
- Do not reintroduce non-topic mode or auth bypass.
- Prefer small reviewable diffs; one concern per PR when continuing.

### 7.3. Как продолжать работу (playbook)

1. `cd D:\CCbot_tmux\ccbot\ccbot_ju1ian` (or clone fresh and re-apply — but work is already there).
2. Read `AGENTS.md`, this handoff, `.agentic/backlog/tasks.json`.
3. `git status` / review diff.
4. If implementing new feature: update graph optional (`/understand`); else skip.
5. Use pipeline:  
   `python scripts/agentic/cli.py select --task <ID>` → follow implement prompt → validate.
6. Or use MCP `ccbot-agentic__*` if Grok/Claude has folder trust + MCP enabled.
7. Never force-push; never weaken `check.yml`.
8. After changes: full quality gates; update backlog status via `update_backlog_status.py`.

### 7.4. Что НЕ делать

- Не коммитить `.env`, secrets, `session_map.json` / live `~/.ccbot` state.
- Не менять quality gates ради green CI.
- Не возвращать General-topic / non-topic routing.
- Не ключить routing по window **name** (только @id после migrate).
- Не silent `except Exception: pass` на user-visible send paths (use typed + log).
- Не начинать «ещё один god-module» в bot.py — handlers уже разнесены.

### 7.5. Зависимости задач (исторические)

```text
REF-001 → REF-002, REF-007
REF-001 done → all others were independent or already completed
All REF-001…009: done
```

### 7.6. Контакты путей (absolute)

| Role | Path |
|------|------|
| Clone | `D:\CCbot_tmux\ccbot\ccbot_ju1ian\` |
| Reports | `D:\GROK\reports\` |
| Knowledge graph | `D:\CCbot_tmux\ccbot\ccbot_ju1ian\.understand-anything\knowledge-graph.json` |
| Backlog SoT | `D:\CCbot_tmux\ccbot\ccbot_ju1ian\.agentic\backlog\tasks.json` |
| This handoff | `D:\GROK\reports\ccbot-handoff.md` (+ copy under `.agentic/` if present) |

---

## 8. Минимальный «bootstrap prompt» для принимающего агента

Скопируй и дополни:

```text
You are continuing work on the ccbot_ju1ian fork at D:\CCbot_tmux\ccbot\ccbot_ju1ian.
Read D:\GROK\reports\ccbot-handoff.md as full context.
All REF-001..009 are implemented in the local tree but likely not committed/pushed.
Next priority: review git status, run quality gates, commit/push or open PR.
Respect AGENTS.md topic-only + window @id routing + auth invariants.
Do not weaken CI. Prefer small commits.
```

---

## 9. Definition of “project complete” vs “handoff complete”

| Criterion | Status |
|-----------|--------|
| Backlog REF-001…009 implemented in local code | **Yes** |
| Pipeline + MCP usable locally | **Yes** |
| All tests green (modulo Windows session_monitor) | **Yes** |
| Committed / pushed to GitHub | **No (verify)** |
| Production deploy / live bot restarted | **No / unknown** |
| Grok auto-review in CI | **No (optional)** |

**Handoff complete:** this document is sufficient for another agent to continue from git review → commit/PR → optional Grok CI → deploy.

---

*Generated as handoff artifact for multi-agent continuity. Prefer updating this file when major milestones (push, new REF-*, deploy) land.*
