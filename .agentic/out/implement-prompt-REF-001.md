# ЗАДАНИЕ ДЛЯ РОБОТА-РЕФАКТОРЩИКА (Implement-Agent)

**Task ID:** `REF-001`  
**Title:** Extract shared auth + session resolution helper  
**Priority:** HIGH  
**Risk:** MEDIUM  
**Status:** ready  
**Date:** 2026-07-16  
**Repo root:** local clone (work only under this tree)  
**Branch suggestion:** `agentic/REF-001-require-session`

---

## 1. Задача одной фразой

Убрать копипасту auth + thread→window resolve + live-window check из `src/ccbot/bot.py`, введя **`require_session` / `SessionContext`**, и перевести на неё **≥8 handlers**, **без изменения внешнего поведения**.

---

## 2. Проблема (as-is)

В `src/ccbot/bot.py` один и тот же каркас повторяется во многих handlers:

```python
user = update.effective_user
if not user or not is_user_allowed(user.id):
    if update.message:
        await safe_reply(update.message, "You are not authorized to use this bot.")
    return

thread_id = _get_thread_id(update)
wid = session_manager.resolve_window_for_thread(user.id, thread_id)
if not wid:
    await safe_reply(update.message, "❌ No session bound to this topic.")
    return

w = await tmux_manager.find_window_by_id(wid)
if not w:
    display = session_manager.get_display_name(wid)
    await safe_reply(update.message, f"❌ Window '{display}' no longer exists.")
    return
```

Фактические вхождения `is_user_allowed` / resolve (ориентиры по строкам, могут сдвинуться):

| Handler | ~line | Auth reply? | Needs window resolve? | Needs live `TmuxWindow`? | Notes |
|---------|------:|:-----------:|:---------------------:|:------------------------:|-------|
| `start_command` | 180 | yes | no | no | только auth + welcome |
| `history_command` | 197 | silent | yes | **no** (только `wid`) | silent unauthorized |
| `screenshot_command` | 214 | silent | yes | yes | |
| `unbind_command` | 250 | silent | partial | no | topic required; `get_window_for_thread` |
| `esc_command` | 280 | silent | yes | yes | |
| `usage_command` | 305 | silent | yes | yes | тексты ошибок **без** ❌ в части мест |
| `topic_closed_handler` | 405 | silent | special | optional | cleanup; **не** reply user |
| `topic_edited_handler` | 445 | silent | special | no | rename; no user reply |
| `forward_command_handler` | 486 | silent | yes | yes | + group_chat_id capture |
| `unsupported_content_handler` | 544 | silent | no | no | auth only-ish |
| `photo_handler` | 566 | yes | yes | yes | |
| `voice_handler` | 640 | yes | yes | yes | |
| `text_handler` | 811 | yes | later | later | unbound → directory browser; **не** early-return «no session» |
| `callback_handler` | 1153 | `query.answer` | later | later | auth: `"Not authorized"` |

**Важно:** нельзя слепо обернуть *все* handlers в «полный» `require_session` — сломается `text_handler` (unbound topic должен открыть browser) и `start_command` (не требует window).

---

## 3. Целевой API (to-be)

### 3.1. Где размещать код

**Предпочтительно (избегает circular imports):** новый модуль:

- `src/ccbot/session_guard.py` — `SessionContext`, `require_user`, `require_session`

`session.py` уже тянет tmux/state; Telegram `Update` + `safe_reply` логичнее держать рядом с bot/handlers.

Допустимо: функции в `bot.py` (хуже для тестируемости) **или** в `session.py`, если импорты чистые (без циклов `session ↔ bot`).

**Не трогать:** `session.py` блок migration / stale-id (`~199–390`) — это REF-004.

### 3.2. Типы (согласовать с кодовой базой)

```python
from dataclasses import dataclass
from telegram import Update, User
from telegram.ext import ContextTypes  # only if needed

from ccbot.tmux_manager import TmuxWindow  # existing dataclass

@dataclass(frozen=True, slots=True)
class SessionContext:
    user: User
    thread_id: int | None          # NOT str — see _get_thread_id
    window_id: str                 # tmux id like "@12"
    window: TmuxWindow
```

### 3.3. Функции

Реализовать **два уровня** (минимум), чтобы не ломать поведение:

```python
async def require_user(
    update: Update,
    *,
    reply_unauthorized: bool = True,
    unauthorized_text: str = "You are not authorized to use this bot.",
) -> User | None:
    """Auth only. On failure: optionally reply / answer callback; return None."""

async def require_session(
    update: Update,
    *,
    reply_unauthorized: bool = True,
    require_message: bool = True,
    reply_on_errors: bool = True,
) -> SessionContext | None:
    """
    Auth + resolve window_id for topic + find live tmux window.

    On any failure: send the SAME user-facing messages as current handlers
    (preserve emoji/text variants carefully — see §5), return None.
    Caller must `if not ctx: return`.
    """
```

Опционально (если упрощает unbind / history):

```python
async def require_bound_window_id(update: Update, ...) -> tuple[User, int | None, str] | None:
    """Auth + resolve window_id, WITHOUT requiring live tmux window."""
```

### 3.4. Канонические user-facing messages (сохранить)

Для **полного** `require_session` (как у screenshot/esc/photo/voice):

| Case | Message (message handlers) |
|------|----------------------------|
| unauthorized | `You are not authorized to use this bot.` (где сейчас reply есть) **или silent return** (где сейчас silent) |
| no binding | `❌ No session bound to this topic.` |
| missing window | `❌ Window '{display}' no longer exists.` где `display = session_manager.get_display_name(wid)` |

Для **callback** unauthorized:

- `await query.answer("Not authorized")` — **не** `safe_reply`.

Параметр `reply_unauthorized` / mode должен покрыть silent vs reply.

`usage_command` сейчас местами без ❌ — **не «улучшать» тексты** в этом PR, либо выровнять только если тест/дифф явно документирован как intentional (предпочтение: **byte-stable messages** на мигрированных handlers, которые уже с ❌).

---

## 4. Пошаговый план

### Шаг REF-001.a — helper + unit tests

**Files:**

- `src/ccbot/session_guard.py` (new) **или** эквивалент
- `tests/ccbot/test_session_guard.py` (new) **или** `tests/ccbot/test_session.py`

**Do:**

1. Реализовать `SessionContext`, `require_user`, `require_session` (+ optional bound-id helper).
2. Использовать существующие:
   - auth: `config.is_user_allowed` / тот же смысл, что `bot.is_user_allowed`
   - thread: логика `_get_thread_id` (tid is None or `== 1` → None)
   - `session_manager.resolve_window_for_thread(user.id, thread_id)`
   - `tmux_manager.find_window_by_id(wid)`
   - replies: `safe_reply` from `handlers.message_sender`
3. Unit-тесты с моками (без реального tmux/Telegram network):

| # | Scenario | Expected |
|---|----------|----------|
| 1 | authorized + bound + live window | returns `SessionContext` |
| 2 | unauthorized | `None` + unauthorized reply **if** enabled |
| 3 | authorized + unbound topic | `None` + «No session bound…» |
| 4 | authorized + bound + window gone | `None` + «no longer exists» |

Моки: `AsyncMock` на `safe_reply`, stub session_manager / tmux_manager.

**Done when:** тесты 1–4 зелёные; helper импортируется без circular import.

---

### Шаг REF-001.b — migrate command handlers (≥4)

**File:** `src/ccbot/bot.py`

Мигрировать **с полным `require_session`** (нужен live window):

1. `screenshot_command`
2. `esc_command`
3. `usage_command` (сохранить текущие тексты ошибок, если отличаются)
4. `forward_command_handler` (сохранить capture `set_group_chat_id` **после** auth)

Также можно:

- `history_command` → `require_user` + bound window_id **без** live window (как сейчас)
- `start_command` → только `require_user(..., reply_unauthorized=True)`

**Before → After (пример screenshot):**

```python
# BEFORE
user = update.effective_user
if not user or not is_user_allowed(user.id):
    return
...
wid = session_manager.resolve_window_for_thread(...)
w = await tmux_manager.find_window_by_id(wid)
...

# AFTER
ctx = await require_session(update, reply_unauthorized=False)  # match prior silent auth
if not ctx:
    return
# use ctx.user, ctx.window_id, ctx.window, ctx.thread_id
```

**Done when:** ≥4 command-path handlers используют helper; дублирующий boilerplate удалён в них.

---

### Шаг REF-001.c — migrate message / other handlers (total ≥8)

**Migrate with full `require_session` (reply_unauthorized=True where today replies):**

- `photo_handler`
- `voice_handler`

**Migrate carefully:**

- `text_handler` — **только** `require_user` в начале; **не** вызывать полный `require_session` до directory-browser / bind logic. Window resolve остаётся ниже по коду как сейчас, *либо* вызывать `require_session` **только** на ветке «already bound and sending to claude» (не на unbound).
- `callback_handler` — auth через helper, совместимый с `query.answer("Not authorized")`; остальной routing не упрощать в этом PR.

**Дополнительно для счётчика ≥8** (если ещё не хватает): `forward_command_handler`, `esc`, `screenshot`, `usage`, `photo`, `voice`, + 2 из history/unbind с partial helpers.

**Не ломать:**

- group `set_group_chat_id` comments («Do NOT remove»)
- topic-only: `_get_thread_id` semantics (`tid == 1` → None)
- routing by window **@id**, not name

**Done when:**

- [ ] ≥8 handlers call the shared helper(s)
- [ ] no remaining *full* auth+resolve+find triple-copy on migrated paths
- [ ] existing tests still pass

---

## 5. Acceptance criteria (жёсткие)

1. **≥8 handlers** use shared helper (`require_session` and/or `require_user` as appropriate).  
2. **Behavior-preserving:** unauthorized / unbound / missing-window outcomes match prior semantics (silent vs reply).  
3. **Unit tests** cover 4 scenarios (auth ok / unauthorized / unbound / missing window).  
4. **Quality gates all green:**

```bash
uv sync --all-extras
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run ruff format --check src/ tests/
uv run pyright src/ccbot/
uv run pytest --tb=short -q
python scripts/agentic/cli.py validate --skip-quality
# ideally also:
# python scripts/agentic/cli.py validate --base-ref origin/main
```

5. **No scope creep:** do not split `bot.py` (that is REF-002); do not refactor message_queue / session migration.

---

## 6. Policies

### MUST

- Topic-only architecture; 1 topic = 1 window = 1 session; route by **window @id**.
- Keep `is_user_allowed` strength (no bypass, no broader allow).
- Prefer `safe_reply` / existing send helpers for user-visible message paths.
- Module docstring on any new `.py` file.
- Small, reviewable diff; one task = one PR.

### MUST NOT

- Non-topic / General-topic mode.
- Force-push / edit `main` directly.
- Weaken CI (`check.yml`) or skip gates.
- Touch `.env`, secrets, `session_map.json` / live state files.
- Silent `except Exception: pass` on new code paths that send to users.
- Large drive-by renames (`wid`→`window_id` globally) — optional only inside new helper API.

---

## 7. Scope (allow-list)

**Allowed:**

- `src/ccbot/session_guard.py` (new, preferred)
- `src/ccbot/bot.py`
- `src/ccbot/session.py` — **only** if you must re-export; avoid heavy edits
- `tests/ccbot/test_session_guard.py` (new)
- `tests/ccbot/test_session.py` and/or `tests/ccbot/test_bot.py` if needed
- `tests/ccbot/conftest.py` only if shared fixtures required

**Forbidden:**

- `.github/workflows/check.yml`
- `.env*`, secrets
- unrelated handlers modules (queue, interactive_ui, …) unless import-only
- knowledge-graph / agentic config mass edits

---

## 8. Knowledge-graph context (compact)

- **Hotspot:** `file:src/ccbot/bot.py` (complex, high fan-out) — this task reduces duplication, not god-module split.
- **Related:** `file:src/ccbot/session.py` — use `session_manager` API; do not rewrite migration.
- **Context pack (optional):**  
  `python scripts/agentic/cli.py context --task REF-001`  
  → `.agentic/out/context-pack.json`  
  MCP: `ccbot-agentic__get_context_pack` with `task_id=REF-001`.

---

## 9. Definition of Done checklist

- [ ] New helper module/functions landed  
- [ ] Tests: authorized / unauthorized / unbound / missing-window  
- [ ] ≥8 handlers migrated  
- [ ] `text_handler` unbound flow still works (manual reasoning + tests if possible)  
- [ ] `callback_handler` unauthorized still answers callback  
- [ ] ruff + pyright + pytest green  
- [ ] Commit message: `refactor(agentic): REF-001 extract require_session helper`  
- [ ] PR body uses template below (draft PR if pushing)

---

## 10. PR body template

```markdown
## REF-001: Extract shared auth + session resolution helper

### What
- Added `SessionContext` + `require_user` / `require_session` (session_guard)
- Migrated 8+ handlers in `bot.py` off duplicated auth/resolve boilerplate
- Unit tests for authorized / unauthorized / unbound / missing-window

### Acceptance
- [x] ≥8 handlers use shared helper
- [x] Behavior-preserving (silent vs reply semantics kept)
- [x] Unit tests for 4 scenarios
- [x] ruff / pyright / pytest green

### Out of scope
- bot.py module split (REF-002)
- session map migration cleanup (REF-004)

### Risk
Medium — auth/routing path; review carefully.
```

---

## 11. Порядок работы агента (обязательный)

1. Read `AGENTS.md`, this prompt, current `bot.py` helpers `_get_thread_id` / `is_user_allowed`.  
2. Implement **REF-001.a** + tests → run pytest on new tests.  
3. Implement **REF-001.b** (≥4 commands).  
4. Implement **REF-001.c** (total ≥8; careful with text/callback).  
5. Run full quality gates (§5).  
6. If blocked: write `.agentic/out/blocked.md` with reason; do not partially leave broken auth.  
7. Stop after REF-001 only — do **not** start REF-002.

---

## 12. MCP helpers (optional)

If `ccbot-agentic` MCP is available:

```text
ccbot-agentic__get_task            { "task_id": "REF-001" }
ccbot-agentic__get_context_pack    { "task_id": "REF-001", "hops": 1 }
ccbot-agentic__get_implement_prompt
ccbot-agentic__run_validate        { "skip_quality": false }
ccbot-agentic__mark_task_status    { "task_id": "REF-001", "status": "in_progress" }
# after merge:
ccbot-agentic__mark_task_status    { "task_id": "REF-001", "status": "done" }
```

---

**START NOW with REF-001.a** — create the helper and the four unit tests.
