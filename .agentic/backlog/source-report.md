# CCBot Refactoring Backlog

**Репозиторий:** https://github.com/six-ddc/ccbot  
**Путь к коду:** `D:\CCbot_tmux\ccbot`  
**Дата анализа:** 2026-07-11  
**Основа:** `.understand-anything/knowledge-graph.json` + прямой анализ исходного кода  

## Цель
Сформировать бэклог задач рефакторинга в формате GitHub Issues для разработчика, работающего с кодом напрямую.

---

### [HIGH] Массовое дублирование логики авторизации и разрешения окна (DRY + SRP)

**Файл(ы):** `src/ccbot/bot.py:182,200,219,253,283,308,410,450,491,552,569,643,813,1159` (и ~15+ других мест)

**Критерий:** 2. Принцип DRY; 1. Ясность и читабельность; 3. SOLID (SRP)

**Проблема:**
```python
user = update.effective_user
if not user or not is_user_allowed(user.id):
    if update.message:
        await safe_reply(update.message, "You are not authorized...")
    return

thread_id = _get_thread_id(update)
wid = session_manager.resolve_window_for_thread(user.id, thread_id)
if not wid:
    await safe_reply(...)
    return

w = await tmux_manager.find_window_by_id(wid)
if not w:
    ...
    return
```
Один и тот же паттерн (проверка пользователя + разрешение thread → wid + проверка существования окна) повторяется в `start_command`, `history_command`, `screenshot_command`, `esc_command`, `forward_command_handler`, `photo_handler`, `voice_handler`, `text_handler`, `callback_handler` и обработчиках закрытия.

**Предлагаемое решение:** Вынести в декоратор/dependency (например, `require_session(update) -> tuple[user, wid]` или middleware) + отдельный `SessionResolver`. Это сократит код bot.py на сотни строк и устранит расхождения в обработке ошибок.

**Метки:** priority:high, category:dr y, category:solid

---

### [HIGH] bot.py — монолит (god module), нарушает SRP

**Файл(ы):** `src/ccbot/bot.py:1-1730` (весь файл)

**Критерий:** 3. SOLID (Single Responsibility Principle); 7. Масштабируемость; 1. Ясность

**Проблема:**  
Один файл содержит: все command handler'ы, callback routing (с кучей `if data.startswith(...)`), state machine для directory/window/session picker'ов (context.user_data), `_bash_capture_tasks`, обработку фото/voice/text, создание окон, скриншоты, forward команд, topic closed/edited. 

Функции `text_handler` и `callback_handler` — огромные. Знание о Telegram, tmux, session и UI смешано.

Знание-граф показывает отдельные узлы для каждого handler'а (`handlers/message_queue.py`, `directory_browser.py` и т.д.), но orchestrating-логика сконцентрирована здесь.

**Предлагаемое решение:** Разбить на `command_handlers.py`, `callback_router.py`, `message_handlers.py` + `BotApplicationBuilder`. Оставить в bot.py только регистрацию и lifecycle (`create_bot`, `post_init`).

**Метки:** priority:high, category:solid, category:maintainability

---

### [HIGH] Широкое использование `except Exception` (проглатывание ошибок)

**Файл(ы):** 
- `src/ccbot/bot.py:793-802,1729`
- `src/ccbot/handlers/message_queue.py:370,385,452,468,524,537,574,582`
- `src/ccbot/handlers/message_sender.py:75,140,161,190`
- `src/ccbot/tmux_manager.py:67,98,139,210,269...`
- `src/ccbot/handlers/interactive_ui.py:247,272`
- `src/ccbot/utils.py:44` (BaseException)

**Критерий:** 4. Обработка ошибок; 6. Управление ресурсами

**Проблема:**
```python
except Exception:
    try:
        await bot.edit_message_text(..., text=plain...)
    except Exception:
        pass   # bot.py:801
```
Многие критические пути (редактирование сообщений, отправка, захват pane, статусы) просто логируют на debug или молча игнорируют. RetryAfter правильно перебрасывается, но остальные ошибки теряются. Это приводит к "тихим" потерям сообщений и сложной отладке.

**Предлагаемое решение:** Ввести конкретные except'ы + `TelegramError`, `TmuxError`. Добавить structured logging + метрики падений. Для edit/send использовать более умный retry с exponential backoff.

**Метки:** priority:high, category:error-handling

---

### [HIGH] Сложная, хрупкая логика миграции и re-resolution stale ID (техдолг)

**Файл(ы):** `src/ccbot/session.py:199-390` (`resolve_stale_ids`, `_migrate_old_format_map`, `_cleanup_stale_session_map_entries`, `override_session_map_entry`, `_mutate_session_map_locked` и др.)

**Критерий:** 3. SOLID; 7. Масштабируемость; 14. Архитектурные решения (расхождение с knowledge-graph)

**Проблема:**  
Несмотря на "topic-only" архитектуру в `.claude/rules/topic-architecture.md` и knowledge-graph, код содержит сотни строк для миграции старого формата (window_name вместо `@id`), re-resolve после рестарта tmux, flock'ов на `session_map.json`, override'ов при `--resume`. 

Много источников истины (`window_states`, `session_map.json`, `thread_bindings`, `monitor_state.json`). Любая ошибка в миграции приводит к потерянным сообщениям или неправильным привязкам.

**Предлагаемое решение:** 
- Постепенно удалить поддержку старых форматов (breaking change в major версии).
- Упростить модель: одна authoritative структура + чёткий процесс восстановления.
- Добавить property-based тесты на миграции.

**Метки:** priority:high, category:architecture, category:maintainability

---

### [HIGH] Глобальное мутабельное состояние + риски race condition

**Файл(ы):** 
- `src/ccbot/handlers/message_queue.py:70-85` (`_message_queues`, `_queue_workers`, `_tool_msg_ids`, `_status_msg_info`, `_flood_until`)
- `src/ccbot/bot.py:720` (`_bash_capture_tasks`)
- `src/ccbot/session.py` (много dict'ов + `_save_state`)

**Критерий:** 8. Конкурентность; 6. Управление ресурсами

**Проблема:**  
Модульные глобальные словари без строгой инкапсуляции. Worker'ы, polling (status_polling.py), capture tasks и handlers работают конкурентно. Есть `_queue_locks`, но не везде. `context.user_data` модифицируется напрямую в разных местах. При краше worker'а или отмене задачи состояние может остаться inconsistent (зависшие статусы, tool_msg_ids).

**Предлагаемое решение:** 
- Выделить классы `MessageQueueManager`, `StatusTracker`, `InteractiveState`.
- Использовать `asyncio.Lock` шире или `anyio` / контексты.
- Добавить graceful shutdown и тесты на гонки.

**Метки:** priority:high, category:concurrency

---

### [MEDIUM] Хрупкий парсинг callback_data через строки

**Файл(ы):** `src/ccbot/bot.py:1178-1190` (и дальше в `callback_handler`)

**Критерий:** 9. Валидация входных данных; 1. Ясность; 4. Обработка ошибок

**Проблема:**
```python
rest = data[prefix_len:]
parts = rest.split(":")
...
offset_str = parts[0]
window_id = ":".join(parts[1:-2])  # window_id может содержать ":"
```
Обработка нескольких форматов (старый/новый), ручной split, обрезка до 64 байт. Легко сломать при добавлении новых полей.

**Предлагаемое решение:** Перейти на `pydantic` / `dataclasses` + сериализацию (или короткие base64/JSON с checksum). Использовать `callback_data.py` константы + typed builder'ы.

**Метки:** priority:medium, category:validation, category:readability

---

### [MEDIUM] Неоднозначные и сокращённые имена переменных

**Файл(ы):** `src/ccbot/bot.py` (wid, tid, data, w, msg повсеместно), `src/ccbot/handlers/message_queue.py:340` (wid, tid, skey, _tkey), `src/ccbot/session.py` (ws, info, data)

**Критерий:** 1. Ясность и читабельность

**Проблема:**  
`wid = session_manager.resolve...`, `data = query.data`, `tid`, `skey` и т.п. затрудняют чтение без контекста. В одном файле `data` означает callback data, JSON-данные, dict и т.д.

**Предлагаемое решение:** Переименовать в `window_id`, `thread_id`, `callback_data`, `window_state`. Ввести type aliases / `NewType`.

**Метки:** priority:medium, category:readability

---

### [MEDIUM] Повторяющийся boilerplate разрешения окна + обработки "окно исчезло"

**Файл(ы):** `src/ccbot/bot.py` (19+ вызовов `resolve_window_for_thread` + `find_window_by_id`, grep показал ~19 упоминаний в одном файле)

**Критерий:** 2. DRY; 3. SOLID

**Проблема:**  
Одинаковый блок "получить wid → найти окно → если нет — unbind + ошибка" в десятках мест. При изменении логики (например, поведение при stale) нужно править везде.

**Предлагаемое решение:** Метод `ensure_active_window(user_id, thread_id) -> Window | Error` в SessionManager/TmuxManager. Централизованная обработка.

**Метки:** priority:medium, category:dr y

---

### [MEDIUM] Дублирование fallback-логики Markdown/plain и обработки RetryAfter

**Файл(ы):** `src/ccbot/handlers/message_sender.py:60-80,130-200` (send_with_fallback, safe_reply, safe_edit, safe_send) + аналогичная в `message_queue.py`

**Критерий:** 2. DRY; 4. Обработка ошибок

**Проблема:**  
Одинаковый паттерн `try: formatted except: strip_sentinels + plain` повторяется в 4+ функциях + в очереди.

**Предлагаемое решение:** Единый `MessageFormatter` или контекст-менеджер/декоратор для отправки.

**Метки:** priority:medium, category:dr y, category:error-handling

---

## Сводная таблица

| Приоритет | Кол-во | Основные категории                          |
|-----------|--------|---------------------------------------------|
| **Critical** | 0     | —                                           |
| **High**     | 5     | DRY, SOLID (SRP), Error handling, Concurrency, Architecture |
| **Medium**   | 4     | Readability, Validation, DRY, Maintainability |
| **Low**      | 0     | —                                           |

---

## Дополнительные наблюдения

- Хорошие практики: атомарные записи (`atomic_write_json`), scrubbing sensitive env vars, валидация UUID для resume.
- Соответствие структуре из knowledge-graph: handlers разделены хорошо.
- Рекомендации для дальнейшего улучшения:
  - Централизованная обработка ошибок Telegram/tmux
  - Property-based тесты для миграций состояний
  - Уменьшить использование `context.user_data` как state machine
  - Добавить типизацию callback data

**Файл сгенерирован автоматически на основе глубокого анализа кода и архитектурного графа.**
