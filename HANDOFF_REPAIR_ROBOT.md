# Handoff: Repair Robot (ccbot_ju1ian)

**Дата снимка:** 2026-07-28  
**Репозиторий:** https://github.com/ju1ian0701/ccbot_ju1ian  
**Локальный клон:** `D:\CCbot_tmux\ccbot\ccbot_ju1ian\`  
**Инструкция:** `REPAIR_ROBOT_INSTRUCTION.md`  
**Backlog:** `.agentic/backlog/tasks.json`

**Инвариант продукта:** topic-only — `1 topic = 1 window = 1 session`.

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

## Следующий шаг

1. Phase 1 — **CLOSED** (ISS-002, ISS-006, ISS-001, ISS-007).  
2. Next structural: **ISS-003** (MessageQueueManager encapsulation) or backlog order from `REPAIR_ROBOT_INSTRUCTION.md`.  
3. WIP limit: max 1 structural issue in IN_PROGRESS.  
4. Meta (backlog/handoff) — commit immediately; product — only via propose → approve → apply.

---

## HITL reminder

`propose → approve → apply → validate → commit → draft PR`  
No silent apply. Product done = call sites + evidence, not “file exists”.  
Meta changes (backlog, handoff, docs) — commit immediately, never mix with product diffs.
