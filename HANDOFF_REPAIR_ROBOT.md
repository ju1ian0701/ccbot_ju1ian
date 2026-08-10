# Handoff: Repair Robot (ccbot_ju1ian)

**Дата снимка:** 2026-08-10  
**Репозиторий:** https://github.com/ju1ian0701/ccbot_ju1ian  
**Локальный клон:** `D:\CCbot_tmux\ccbot\ccbot_ju1ian\`  
**Инструкция:** `REPAIR_ROBOT_INSTRUCTION.md`  
**Backlog:** `.agentic/backlog/tasks.json`

**Инвариант продукта:** topic-only — `1 topic = 1 window = 1 session`.

---

## Последний завершённый этап

Последний завершённый этап — **ISS-005 4a: extract `BindingStore` (PR #20, merged `d6bc24a`)**.

- Новый `binding_store.py` (125 LOC): владение `thread_bindings` + `group_chat_ids`; 6 операций (bind/unbind/get/iter/set_group_chat_id/resolve_chat_id) + `to_state`/`load_state`/`reset`; persist и логирование остались в фасаде, store чистый;
- `session.py` (857→853 LOC): поля ушли из dataclass в `_bindings` + property-делегаты (тесты не тронуты); `bind_thread` пишет `window_display_names` в фасаде — осознанная кросс-store координация до 4b;
- `state.json` байт-в-байт (порядок ключей сохранён); паритет-матрица **17/17** в stub-окружении + edge cases (round-trip, corrupt state); suite **383 passed**;
- Gap `user_window_offsets` — residual у фасада, решение на старте 4b.

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

**Status: CLOSED (2026-08-06)**

| Task | Status | Evidence | PR |
|------|--------|----------|-----|
| ISS-003 | DONE | encapsulate `_queues`/`_locks` | #13 (merged) |
| ISS-008 | DONE | explicit `message_thread_id` typed contract | #14 (merged `1fff3af`) |
| ISS-010 | DONE | extract `bash_capture_tasks` → `CaptureTaskRegistry` | #16 (merged `fc861f4`) |

Phase 2: **CLOSED**

- ISS-003 (инкапсуляция MessageQueueManager) — **DONE** (PR #13);
- ISS-008 (typed `message_thread_id` contract) — **DONE** (PR #14);
- ISS-010 (extract `bash_capture_tasks`) — **DONE** (PR #16);
- Следующая фаза: **Phase 3** — ISS-004 (callback_router spaghetti → table-driven dispatch), затем ISS-011 (split sub-handlers). WIP-слот свободен.

### Детали Phase 2

| ID | Что | Артефакты |
|----|-----|-----------|
| **ISS-003** | Public accessors `get_queue` / `get_lock`; `_message_queue_worker` больше не трогает `queue_manager._queues` / `._locks` | local apply 2026-07-29; PR #13; evidence greps ниже; tests `test_message_queue_*` green |
| **ISS-008** | Explicit `message_thread_id` на send path; drop `_send_kwargs` + `type: ignore` | proposal `ISS-008_20260806T094220Z`; PR #14 `1fff3af`; env gate deviation → PR #15 |
| **ISS-010** | `CaptureTaskRegistry` + `capture_tasks`; clear-on-topic cancel; dual dicts removed | proposal `ISS-010_20260806T152347Z`; PR #16 `fc861f4`; tests `test_capture_registry.py` |

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

### Evidence greps (ISS-010)

```text
rg "bash_capture_tasks\s*=" src/ccbot  → 0 (live dict)
rg "bash_capture_tasks" src/ccbot  → docstring-only residual OK
rg "CaptureTaskRegistry|capture_tasks" src/ccbot/handlers
```

### PRs (Phase 2)

| PR | Title | State |
|----|-------|-------|
| [#13](https://github.com/ju1ian0701/ccbot_ju1ian/pull/13) | ISS-003 encapsulate MessageQueueManager | **MERGED** |
| [#14](https://github.com/ju1ian0701/ccbot_ju1ian/pull/14) | ISS-008 explicit message_thread_id | **MERGED** (`1fff3af`) |
| [#15](https://github.com/ju1ian0701/ccbot_ju1ian/pull/15) | chore: pytest Windows basetemp (WinError 5) | **MERGED** (`fe022b6`) |
| [#16](https://github.com/ju1ian0701/ccbot_ju1ian/pull/16) | ISS-010 CaptureTaskRegistry | **MERGED** (`fc861f4`) |

---

## Phase 3 — callback_router: table-driven dispatch + split sub-handlers

**Status: CLOSED (2026-08-09)**

| Task | Status | Evidence | PR |
|------|--------|----------|-----|
| ISS-004 | DONE | table-driven dispatch; single topic-guard | #17 (merged `1773c43`) |
| ISS-011 | DONE | split into 6 submodules; entry 141 LOC | #18 (merged `4894eac`) |

### Детали Phase 3

| ID | Что | Артефакты |
|----|-----|-----------|
| **ISS-004** | Registry `_EXACT_ROUTES`/`_PREFIX_ROUTES`/`_match_route`; единый guard `_check_same_topic` + `_clear_pending_thread` (10 copy-paste → 1 helper); `_ASK_ACTIONS` (9 aq-веток → 1 handler); wire format unchanged; parity matrix 43 кейса (поймала порядок cleanup→answer в `db:confirm`) | proposal `ISS-004_20260808T102953Z`; PR #17 `1773c43` |
| **ISS-011** | Entry 768→141 LOC (auth+capture+dispatch only); 6 submodules (guard/history/directory/pickers/screenshot/interactive); guard вынесен в `callback_topic_guard` (иначе циклический импорт); тела 1:1; parity 60/60 | PR #18 `4894eac` |

### Evidence greps (Phase 3 exit)

```text
rg "_pending_thread_id" src/ccbot/handlers        → helpers + docstring only
rg -n "def _handle_" .../callback_router.py       → только _handle_noop
rg -c "_check_same_topic" src/ccbot/handlers      → 1 def + 11 call sites
  # нюанс: rg -c считает и import-строки submodules
pytest tests/ccbot/handlers/test_callback_data.py → 27 passed
```

### PRs (Phase 3)

| PR | Title | State |
|----|-------|-------|
| [#17](https://github.com/ju1ian0701/ccbot_ju1ian/pull/17) | ISS-004 table-driven callback_router | **MERGED** (`1773c43`) |
| [#18](https://github.com/ju1ian0701/ccbot_ju1ian/pull/18) | ISS-011 split callback_router sub-handlers | **MERGED** (`4894eac`) |

### Уроки Phase 3

- Паритет-матрица на stub-окружении — обязательный evidence при рефакторинге роутеров; ловит и поведенческие (порядок side effects), и сборочные (пропущенный импорт) ошибки;
- `on_stale`-колбэк в guard — способ сохранить порядок «cleanup → answer» при выносе guard в helper;
- `gh pr merge --squash --delete-branch` сам делает checkout main + pull + удаление веток (шаги 21–22 автоматом);
- `analyze` генерирует untracked `.understand-anything/config.json` → `Remove-Item` после прогона; canonical KG (fingerprints/knowledge-graph/meta) — tracked, обновления коммитятся мета-коммитом `chore(kg)`;
- CRLF-диффы захватывать бинарно (universal newlines съедает `\r` → ложный `patch does not apply`);
- Мета-коммит шага 23 проверять фактом (`git log --oneline -3`) при возобновлении ветки, а не предполагать (ISS-004 закрыт задним числом).

---

## Следующий шаг

1. Phase 1 — **CLOSED** (ISS-002, ISS-006, ISS-001, ISS-007).  
2. Phase 2 — **CLOSED** (ISS-003, ISS-008, ISS-010).  
3. Phase 3 — **CLOSED** (ISS-004 #17, ISS-011 #18).  
4. Phase 4 — **IN PROGRESS**: **ISS-009 DONE** (PR #19 `65907e5`); **ISS-005 4a DONE** (PR #20 `d6bc24a`: `BindingStore` владеет `thread_bindings` + `group_chat_ids`, фасад — property-делегаты) → следующая **4b `WindowStateStore`** (window→session/cwd/name; на старте решить gap `user_window_offsets`: residual facade vs расширить 4b vs отдельный store — предложение агента: residual), затем 4c `SessionMapRepository` (session_map + flock RMW), 4d thin facade; `session_migration` — pure, не трогаем. Зона одна (Z4) → строго один structural PR за раз.  
5. Meta (backlog/handoff) — commit immediately; product — only via propose → approve → apply.  
6. Gate: не мержить product PR при красном validate; env-фиксы — отдельной веткой (как PR #15).

---

## HITL reminder

`propose → approve → apply → validate → commit → draft PR`  
No silent apply. Product done = call sites + evidence, not “file exists”.  
Meta changes (backlog, handoff, docs) — commit immediately, never mix with product diffs.
