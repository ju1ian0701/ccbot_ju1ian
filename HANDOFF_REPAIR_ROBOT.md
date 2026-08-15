# Handoff: Repair Robot (ccbot_ju1ian)

**Дата снимка:** 2026-08-14  
**Репозиторий:** https://github.com/ju1ian0701/ccbot_ju1ian  
**Локальный клон:** `D:\CCbot_tmux\ccbot\ccbot_ju1ian\`  
**Инструкция:** `REPAIR_ROBOT_INSTRUCTION.md`  
**Backlog:** `.agentic/backlog/tasks.json`

**Инвариант продукта:** topic-only — `1 topic = 1 window = 1 session`.

---

## Последний завершённый этап

Последний завершённый этап — **ISS-022 DONE: port upstream PR #53 local whisper backend + CCBOT_WHISPER_LANGUAGE (auto|en|ru) (PR #35 `236b13c`)**.

- `transcribe.py`: unified `transcribe()`; local faster-whisper + openai; `CCBOT_WHISPER_LANGUAGE` auto|en|ru (без хардкода `language="en"`);
- `voice_handler`: файловое скачивание + `finally: unlink`; `TranscriptionDisabled` / `TranscriptionError` / generic; сначала 🎤, потом forward;
- Suite 436; THERMO 7/7.

Предыдущий этап — **ISS-021 DONE: port upstream PR #89 documents handler (require_session rewrite) (PR #34 `8120171`)**.

- `handlers/document.py`: `require_user` + `require_session`; 20 MB cap; caption + `(file attached: <path>)`; suite 419.

Предыдущий этап — **ISS-020 DONE: port upstream PR #86 allowed_updates + edit-guards (PR #33 `3e06992`)**.

- `main.py`: `allowed_updates` 2 → 8; `bot.py` TEXT/PHOTO/VOICE/catch-all edit-guards; suite 407.

Предыдущий этап — **ISS-019 DONE: identity-checked `CaptureTaskRegistry.discard` (PR #32 `f511920`)**.

- Identity-checked discard: `discard(user_id, thread_id, task=None)` — при `task is None` безусловный pop (legacy); при `task` — pop только если `self._tasks.get(key) is task` (stale finally не выкидывает новую регистрацию);
- Call site: `capture_bash_output` finally → `capture_tasks.discard(user_id, thread_id, asyncio.current_task())`;
- +2 regression tests (stale-evict, matching-remove) в `tests/ccbot/test_capture_registry.py` (существующие 4 без правок); suite capture 6/6, ruff/pyright green.

Предыдущий этап — **ISS-018 DONE: ruff I001 import sort, 12 файлов (PR #31 `5374113`)**.

- Машинный `ruff check --select I001 --fix` (4 scripts/agentic + 6 src/ccbot + 2 tests) + одна ручная правка: восстановлен `# noqa: E402` в cli.py (фиксер расщепил `from propose import (...)`, новый блок потерял noqa — предусмотрено acceptance);
- Гейты: I001 = 0 findings, `ruff check` clean, `ruff format --check` = **113/113**, pyright 0, suite 405, validate `ok: true`, guardrails 12/12.

Предыдущий этап — **ISS-017 DONE: trailing newline propose.py (PR #30 `654d042`)**. Hygiene-дуга ISS-015/016/017 закрыта: `ruff format --check` = **113/113**, backlog пуст.

- Единственный hunk: EOF trailing newline в propose.py (`\ No newline` маркер); сырой `git diff HEAD` прошёл propose без пересборки — dogfood binary staging из ISS-016 доказан делом;
- Suite 405, pyright 0, ruff clean.

Предыдущий этап — **ISS-016 DONE: binary-safe staging в propose.py (PR #29 `8a7b4bf`)**.

- Байтовый путь diff: `_write_temp_diff` = `bytes` + `NamedTemporaryFile("wb")` (staged == input, без дозаписи `\n`); `check_diff_applies`/`diff_numstat`/`validate_proposal_diff` принимают `bytes`, decode только для парсинга; `create_proposal_artifacts(diff_bytes=...)`, `proposal.diff` пишется через `write_bytes` (apply читает артефакт напрямую → байт-безопасно end-to-end); все чтения diff → `read_bytes`;
- Новый `tests/agentic/test_binary_staging.py` — 8 тестов (staged == input: LF / CRLF / git-заголовки / EOF-маркер / patch без финального `\n`); suite **397 → 405**;
- Trailing newline propose.py осознанно не тронут — уехал в ISS-017 (теперь staging переживёт EOF-marker hunk).

Предыдущий этап — **ISS-015 DONE: ruff-format hygiene (PR #28 `562c687`)**.

- Машинный diff `ruff format` через полный HITL-цикл: 12 файлов вне src/tests (`.agentic/IMPLEMENTATION_PLAN.md` + 11 в `scripts/agentic/`), +139/−42, ноль ручных правок;
- Residual (задокументирован): trailing newline в конце `scripts/agentic/propose.py` — hunk не переживает staging (см. уроки ниже); `ruff format --check` = 111 formatted + 1 residual;
- Validate green (guardrails + ruff + pyright + pytest 397); suite не пострадал.

### Уроки ISS-015 (line-endings и staging propose)

- При `core.autocrlf=true` `git diff HEAD` выдаёт **LF-нормализованный** diff — не применяется к CRLF-дереву; `git -c core.autocrlf=false diff` даёт whole-file diff (блобы LF, дерево CRLF) — обе формы непригодны;
- **Staging propose переписывает любой вход в all-CRLF** (`read_text` срезает `\r`, `NamedTemporaryFile("w")` на Windows переводит `\n`→`\r\n`): git-заголовки (`diff --git`/`index`) с `\r` ломают парсер git 2.45.1.windows → рабочая форма diff для propose — **difflib-стиль без git-заголовков** (как REF-007);
- Hunk с `\ No newline at end of file` в staged-форме не применяется (минус-строка получает `\r`, которого нет на диске) → такие hunk'и вырезать, residual фиксировать в debt;
- Проверки propose — **ИЛИ** (первый rc=0 из `--check` / `--cached --check` / `--reverse --check`), `--cached` против LF-блобов с `\r`-контентом падает всегда — это штатно;
- Debt: перевести staging propose на binary (`read_bytes`/`write_bytes`) — после этого trailing newline propose.py доедет маленькой задачей.

- R7a: `wid` → `window_id` — 67 строк в 11 файлах + 3 переноса под line-length 88;
- R7b: `tid` → `thread_id` — 28 замен в 3 файлах; 2 семантические ловушки (`tid`-локалка при живом параметре `thread_id` в `enqueue_status_update` и `set_group_chat_id`) — инлайн `thread_id or 0`, не реассайн параметра;
- `skey` = 0 hits — no-op (зафиксировано в resolution);
- REF-007 — первый structural tenant механизма ISS-013: `validate --task REF-007` после R7a давал штатный FAIL по `tid`, после R7b — full green (оба absent); done-gate разблокирован;
- Suite **397 passed** на всём протяжении; pyright 0, ruff clean.

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


## Phase 4 — SessionManager: split into stores + thin facade (зона Z4)

**Status: CLOSED (2026-08-10)**

| Task | Status | Evidence | PR |
|------|--------|----------|-----|
| ISS-009 | DONE | external `_save_state` = 0; TTL/backup сохранены | #19 (merged `65907e5`) |
| ISS-005 | DONE | split 4a–4d; `session.py` 857→649 LOC | #20–#23 (merged `b5638d6`) |

### Детали Phase 4

| ID | Что | Артефакты |
|----|-----|-----------|
| **ISS-009** | `state.json` пишется только через `SessionManager._save_state`; внешних писателей нет | rg `_save_state` = 1 def + internal calls |
| **ISS-005 4a** | `BindingStore`: `thread_bindings` + `group_chat_ids` | `binding_store.py` (125 LOC); паритет **17/17** |
| **ISS-005 4b** | `WindowStateStore`: `window_states` + `window_display_names`; `WindowState` re-export | `window_state_store.py` (118 LOC); паритет **14/14** |
| **ISS-005 4c** | `SessionMapRepository`: `read`/`write`/`exists` + `mutate_locked` (flock RMW дословно) | `session_map_repository.py` (88 LOC); паритет **12/12** |
| **ISS-005 4d** | `TranscriptReader` (JSONL-семейство) + thin facade; `ClaudeSession` re-export | `transcript_reader.py` (195 LOC); паритет **12/12** |

### Evidence greps (Phase 4 exit)

```text
rg "class BindingStore|class WindowStateStore|class SessionMapRepository|class TranscriptReader" src/ccbot  → 4 store-модуля
rg "def (thread_bindings|group_chat_ids|window_states|window_display_names)" src/ccbot/session.py          → property-делегаты с сеттерами
rg "_save_state" src/ccbot                                                                                 → 1 def + internal calls (external = 0)
pytest  → 383 passed;  pyright  → 0 errors;  ruff  → clean (1 pre-existing I001)
```

### PRs (Phase 4)

| PR | Title | State |
|----|-------|-------|
| [#19](https://github.com/ju1ian0701/ccbot_ju1ian/pull/19) | ISS-009 single-writer state.json | **MERGED** (`65907e5`) |
| [#20](https://github.com/ju1ian0701/ccbot_ju1ian/pull/20) | ISS-005 4a extract BindingStore | **MERGED** (`d6bc24a`) |
| [#21](https://github.com/ju1ian0701/ccbot_ju1ian/pull/21) | ISS-005 4b extract WindowStateStore | **MERGED** (`4b43cef`) |
| [#22](https://github.com/ju1ian0701/ccbot_ju1ian/pull/22) | ISS-005 4c extract SessionMapRepository | **MERGED** (`6164989`) |
| [#23](https://github.com/ju1ian0701/ccbot_ju1ian/pull/23) | ISS-005 4d TranscriptReader + thin facade | **MERGED** (`b5638d6`) |

### Уроки Phase 4

- Store-модули — чистые (без persist/логирования); кросс-store логика (`bind_thread`/`load_session_map`/`resolve_stale_ids`) остаётся в фасаде;
- Тесты пишут `mgr.thread_bindings = {...}` напрямую → property-**сеттеры** обязательны, не только геттеры;
- Dynamic config lookup (property, не захват в `__init__`) — тестовые фикстуры патчат `config.session_map_file`/`claude_projects_path`;
- Gap-анализ до propose: `user_window_offsets` не вошёл ни в один store → осознанный residual у фасада (решение фиксировать в дизайне итерации);
- Validate/apply запускать строго по артефактам (`approval.json`, наличие файла в tree), не по памяти — инцидент 4b (validate до apply);
- Мета-правки коммитить **до** старта цикла: dirty tree (M HANDOFF) блокирует propose (recurrence ISS-009 → 4a);
- Свои F401 отслеживать `ruff check` в песочнице до сборки diff (инцидент 4c: `flock`/`LOCK_EX`/`LOCK_UN`);
- `/tmp` в песочнице неперсистентен — diff-артефакты и патчи дублировать в output.

### Уроки (операционные, накопились к ISS-022)

- **(а)** Meta-шаг завершён только при пустом `git log origin/main..main` (факт на remote, не локальный orphan-коммит).
- **(б)** Перед любым вызовом `agentic <cmd>` — сверка флагов через `-h`. Три инцидента: `--notes` обязателен у `request-changes` (×2), позиционный аргумент `approve`.
- **(в)** GH_TOKEN-разделение: `gh` / token-действия — человек; робот останавливается на `HANDOFF TO HUMAN`.

---

## Следующий шаг

1. Phase 1 — **CLOSED** (ISS-002, ISS-006, ISS-001, ISS-007).  
2. Phase 2 — **CLOSED** (ISS-003, ISS-008, ISS-010).  
3. Phase 3 — **CLOSED** (ISS-004 #17, ISS-011 #18).  
4. Phase 4 — **CLOSED** (ISS-009 #19 `65907e5`; ISS-005 #20–#23 `b5638d6`: stores + thin facade, `session.py` 857→649 LOC).  
5. **ISS-022 DONE** (PR #35 `236b13c`); **ISS-023 IN PROGRESS** — lazy init cache dirs (IMAGES_DIR/DOCS_DIR/_AUDIO_DIR, THERMO P2 debt), full HITL cycle.  
6. **Debt (только по явному решению, без отдельной ISS):**  
   - smoke-тесты Phase 1 (чек-лист в `.agentic/out/notes/2026-08-11-smoke-phase1.md`, отложены);  
   - **format-scope validate** — `validate`/`ruff format --check` scope сейчас завязан на full-tree / pre-existing baseline; при hygiene-задачах (ISS-015+) и точечных product-diff полезен scoped format-check (только changed files / allowlist), чтобы residual вне scope не маскировал fail и наоборот. Полноценная ISS — отдельное решение о приоритете; до того — debt-заметка здесь (как smoke Phase 1).  
   - ISS-020 residual: edit-guard для `forward_command_handler` (`filters.COMMAND`) — severity low;  
   - smoke ISS-020 — чек-лист `.agentic/out/notes/2026-08-14-iss-020-smoke-checklist.md`; blocked: требуется выделенный сервер с живой tmux-сессией;  
   - ISS-021 residual (THERMO): naming `DOCS_DIR` vs `_MAX_DOC_BYTES` (косметика);  
   - ISS-021 residual (THERMO): import-time `mkdir` каталогов → ленивая инициализация (кандидат; затрагивает и `IMAGES_DIR`);  
   - ISS-021 residual (THERMO): коллизионное окно `time.time()` в именах файлов → `file_unique_id` (low);  
   - smoke ISS-021 — те же серверные предусловия; PDF ≤20 MB (инъекция пути + caption) и >20 MB (отказ, без скачивания); чек-лист `.agentic/out/notes/2026-08-14-iss-020-smoke-checklist.md`.  
7. Meta (backlog/handoff) — commit immediately; product — only via propose → approve → apply.  
8. Gate: не мержить product PR при красном validate; env-фиксы — отдельной веткой (как PR #15).

---

## HITL reminder

`propose → approve → apply → validate → commit → draft PR`  
No silent apply. Product done = call sites + evidence, not “file exists”.  
Meta changes (backlog, handoff, docs) — commit immediately, never mix with product diffs.
