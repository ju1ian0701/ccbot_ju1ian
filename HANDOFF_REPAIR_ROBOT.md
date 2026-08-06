# Handoff: Repair Robot (ccbot_ju1ian)

**Дата снимка:** 2026-08-06  
**Репозиторий:** https://github.com/ju1ian0701/ccbot_ju1ian  
**Локальный клон:** `D:\CCbot_tmux\ccbot\ccbot_ju1ian\`  
**Инструкция:** `REPAIR_ROBOT_INSTRUCTION.md`  
**Backlog:** `.agentic/backlog/tasks.json`

**Инвариант продукта:** topic-only — `1 topic = 1 window = 1 session`.

---

## Последний завершённый этап

Последний завершённый этап — **ISS-008: explicit `message_thread_id` typed contract (PR #14, merged)**.

Выполнено:

1. `_send_kwargs()` удалена из `message_queue.py`; три call site (`_send_task_images`, `_process_content_task`, `_do_send_status_message`) переведены на явный `message_thread_id=…`;
2. `send_with_fallback()` и `send_photo()` в `message_sender.py` получили typed-параметр `message_thread_id: int | None = None`, прокидываемый явно в `bot.send_message` / `bot.send_photo` / `bot.send_media_group`;
3. 0 `type: ignore` на send-path; `safe_send` прокидывает явный `message_thread_id` в `send_with_fallback`; ruff / pyright — green.

Зафиксированное отклонение процесса: PR #14 смержен до green validate — pytest падал с 59 ERROR (PermissionError WinError 5, pytest#7491 — env-проблема, не код ISS-008). Закрыто post-factum: env-фикс pytest оформлен **PR #15** (`chore/pytest-windows-tmp`, `fe022b6`) + полный green-прогон на main. Подробности: `.agentic/out/notes/2026-08-06-iss-008-validate-gate-deviation.md`.

---

## Phase 1 — Canonical auth + REF-001 done-for-real

**Status: CLOSED (2026-07-28)**

| Task | Status | Evidence | PR |
|------|--------|----------|-----|
| ISS-002 | DONE | canonical auth; 1 SoT | #10 (merged) |
| ISS-006 | DONE | compose require_session; tests green | #11 (merged ee8c0b2) |
| ISS-001 | DONE | migrate handlers to require_*; ≥8 call sites | #12 (merged a36b018) |
| ISS-007 | DONE | unified DEFAULT_* error strings; commands on require_session | #12 (with ISS-001) |

> Note: PR #11 merged with claude-review failures (2×). Merge owner decision logged.

### Детали Phase 1

| ID | Что | Артефакты |
|----|-----|-----------|
| **ISS-002** | Один SoT: `is_user_allowed` / `get_thread_id` в `session_guard`; `handlers/auth.py` — thin re-export | merged `2026-07-28T11:10:50Z`; proposal `ISS-002_20260727T175352Z`; PR #10 |
| **ISS-006** | `require_session` = `require_bound_window_id` + live window + optional unbind + `SessionContext` | proposal `ISS-006_20260728T141904Z`; merge commit `ee8c0b2`; PR #11 |
| **ISS-001** | Мигрировать handlers на `require_*` (≥8 call sites); residual topic_closed/kill via require_session | proposal `ISS-001_20260728T183408Z`; merge commit `a36b018`; PR #12 |
| **ISS-007** | Унифицировать error strings + ladder в screenshot/esc/usage | merged with ISS-001 (`a36b018`) |

### Evidence greps (Phase 1 exit)

```text
rg "is_user_allowed\(user.id\)" src/ccbot/handlers  → 0
rg "require_session|require_user|require_bound" src/ccbot/handlers  → ≥8
rg "require_bound_window_id" src/ccbot/session_guard.py
# require_session composes require_bound_window_id
```

### PRs

| PR | Title | State |
|----|-------|-------|
| [#10](https://github.com/ju1ian0701/ccbot_ju1ian/pull/10) | ISS-002 canonical auth | **MERGED** |
| [#11](https://github.com/ju1ian0701/ccbot_ju1ian/pull/11) | ISS-006 compose require_session | **MERGED** (`ee8c0b2`) |
| [#12](https://github.com/ju1ian0701/ccbot_ju1ian/pull/12) | ISS-001 migrate handlers + ISS-007 strings | **MERGED** (`a36b018`) |

---

## Phase 2 — Queue encapsulation + concurrency surface (REF-005 real)

**Status: IN PROGRESS (2026-08-06)**

| Task | Status | Evidence | PR |
|------|--------|----------|-----|
| ISS-003 | DONE | encapsulate `_queues`/`_locks` | #13 (merged) |
| ISS-008 | DONE | explicit `message_thread_id` typed contract | #14 (merged `1fff3af`) |
| ISS-010 | BACKLOG | extract `bash_capture_tasks` | — |

Phase 2: **IN PROGRESS**

- ISS-003 (инкапсуляция MessageQueueManager) — **DONE** (PR #13);
- ISS-008 (typed `message_thread_id` contract) — **DONE** (PR #14);
- Следующая: **ISS-010** (extract `bash_capture_tasks` из `message_handlers.py`). WIP-слот свободен.

### Детали Phase 2

| ID | Что | Артефакты |
|----|-----|-----------|
| **ISS-003** | Public accessors `get_queue` / `get_lock`; `_message_queue_worker` больше не трогает `queue_manager._queues` / `._locks` | local apply 2026-07-29; PR #13; evidence greps ниже; tests `test_message_queue_*` green |
| **ISS-008** | Explicit `message_thread_id` на send path; drop `_send_kwargs` + `type: ignore` | proposal `ISS-008_20260806T094220Z`; PR #14 `1fff3af`; env gate deviation → PR #15 |

### Evidence greps (ISS-003)

```text
rg "queue_manager\._queues|queue_manager\._locks" src  → 0
# self._queues / self._locks only inside MessageQueueManager
# worker uses get_queue() / get_lock()
```

### Evidence greps (ISS-008)

```text
rg "_send_kwargs" src/ccbot/handlers  → 0
rg "type: ignore" src/ccbot/handlers/message_queue.py  → 0 (send path)
rg "message_thread_id" src/ccbot/handlers/message_sender.py  → explicit params
```

### PRs (Phase 2)

| PR | Title | State |
|----|-------|-------|
| [#13](https://github.com/ju1ian0701/ccbot_ju1ian/pull/13) | ISS-003 encapsulate MessageQueueManager | **MERGED** |
| [#14](https://github.com/ju1ian0701/ccbot_ju1ian/pull/14) | ISS-008 explicit message_thread_id | **MERGED** (`1fff3af`) |
| [#15](https://github.com/ju1ian0701/ccbot_ju1ian/pull/15) | chore: pytest Windows basetemp (WinError 5) | **MERGED** (`fe022b6`) |

---

## Следующий шаг

1. Phase 1 — **CLOSED** (ISS-002, ISS-006, ISS-001, ISS-007).  
2. Phase 2 — **IN PROGRESS**: ISS-003 **DONE**, ISS-008 **DONE**; next **ISS-010** (`bash_capture_tasks` из `message_handlers.py`). WIP-слот свободен.  
3. WIP limit: max 1 structural issue in IN_PROGRESS.  
4. Meta (backlog/handoff) — commit immediately; product — only via propose → approve → apply.  
5. Gate: не мержить product PR при красном validate; env-фиксы — отдельной веткой (как PR #15).

---

## HITL reminder

`propose → approve → apply → validate → commit → draft PR`  
No silent apply. Product done = call sites + evidence, not “file exists”.  
Meta changes (backlog, handoff, docs) — commit immediately, never mix with product diffs.
