# HANDOFF: CCBot Agentic Refactoring Pipeline

**Дата:** 2026-07-18
**Ветка:** `agentic/REF-006-data-to-dataclasses`
**Коммит:** `8c3051b`
**Репозиторий:** https://github.com/ju1ian0701/ccbot_ju1ian

## 1. Цель проекта
Автоматизация рефакторинга Telegram-бота для управления Claude Code (`ccbot`) через agentic pipeline.
**Глобальная задача:** Устранить 9 архитектурных проблем (5 HIGH, 4 MEDIUM), выявленных в `ccbot-refactoring-backlog.md`, используя AI-агентов (Grok/Claude) под контролем человека.
**Текущая цель:** Последовательное выполнение задач REF-001…REF-009 с соблюдением quality gates и сохранением обратной совместимости.

## 2. Что уже сделано

### Инфраструктура пайплайна
- ✅ Создан и замержен в `main` каркас agentic pipeline (скрипты, конфиги, workflows).
- ✅ Настроен локальный цикл: `cli.py select/context` → Grok Build → Quality Gates → PR.
- ✅ Решены проблемы аутентификации Git/GitHub (Classic token + `gh auth setup-git`).
- ✅ Исправлены кодировки JSON (UTF-8 без BOM) для кроссплатформенной работы.

### Выполненные задачи рефакторинга
| ID | Задача | Статус | Результат |
|---|---|---|---|
| REF-001 | Extract auth helper | ✅ Done | `require_session()` + `SessionContext`, устранено дублирование в 15+ хендлерах |
| REF-006 | Callback data → dataclasses | 🔄 In Progress | Ветка `agentic/REF-006-data-to-dataclasses` запушена, ожидает review/merge |

### Ключевые достижения REF-006
- Заменён строковый парсинг `data.split(":")` на typed frozen dataclasses.
- Добавлена валидация (`__post_init__`), round-trip сериализация, проверка лимита 64 байта.
- Сохранена модульная архитектура роутера (не создавался единый диспетчер).
- Написаны тесты миграции, иммутабельности и safe serialization.

## 3. Текущее состояние
- **Активная ветка:** `agentic/REF-006-data-to-dataclasses` (запушена, draft PR создан).
- **Следующая задача:** После merge REF-006 → переход к REF-007 (переименование переменных).
- **Блокировки:** Нет. Все quality gates проходят локально.
- **Knowledge Graph:** Актуален после REF-001, требует обновления после merge REF-006.

## 4. Что осталось сделать

### Ближайшие шаги (приоритет)
1.  **Замержить REF-006** после human review.
2.  **Обновить knowledge graph:** `/understand full` в чате с AI-агентом.
3.  **Выполнить REF-007** (Rename wid/tid/skey):
    ```powershell
    git checkout main && git pull
    git checkout -b agentic/REF-007-renaming
    python scripts/agentic/cli.py select --task REF-007
    ```
4.  **Продолжить по плану:** REF-002 → REF-003 → REF-008 → REF-009 → REF-004 → REF-005.

### Незавершённые элементы пайплайна
- ⬜ Grok-интеграция в CI (`agentic-grok-review.yml`).
- ⬜ Atomic task packs (разбиение эпиков на подзадачи в `tasks.json`).
- ⬜ Context packer из knowledge graph (`build_context_pack.py`).
- ⬜ Секреты `XAI_API_KEY` в GitHub Secrets.

## 5. Ключевые решения и договорённости

### Архитектурные
- **Topic-only режим:** Никогда не переключать бота в non-topic mode.
- **Авторизация:** Не ослаблять `is_user_allowed()`. Все проверки через `require_session()`.
- **Маршрутизация окон:** Только по `@id`, никогда по `window_name`.
- **Callback Data:** Использовать frozen dataclasses + `safe_callback_data()` с проверкой 64 байта.
- **Модульность роутера:** Не объединять парсеры в единую функцию; каждый `parse_*` возвращает свой dataclass.

### Процессные
- **1 PR = 1 REF-***: Не смешивать задачи в одном PR.
- **Human Merge:** AI никогда не мерджит в `main`. Только draft PR.
- **Quality Gates:** Ruff + Pyright + Pytest обязательны перед каждым коммитом.
- **Файл `tasks.json`:** Всегда под версионным контролем. Обновлять статус через `update_backlog_status.py`.

### Технические
- **Кодировка JSON:** Всегда UTF-8 без BOM (использовать Python для записи, не PowerShell).
- **Git Auth:** Использовать `gh auth setup-git` + Classic token с `workflow` scope.
- **`.gitignore`:** `.agentic/out/` игнорируется; `.agentic/backlog/`, `scripts/agentic/`, `.grok/` — в репо.

## 6. Артефакты и файлы

### Конфигурация пайплайна
| Файл | Назначение |
|---|---|
| `.agentic/backlog/tasks.json` | База данных задач REF-001…009 со статусами |
| `.agentic/config.json` | Weights, guardrails, paths |
| `.agentic/policies.json` | Must/Must Not правила для агентов |
| `scripts/agentic/cli.py` | CLI toolkit (analyze/select/context/validate) |
| `.github/workflows/agentic-*.yml` | CI workflows (analyze/plan/implement/orchestrator) |

### Продуктовый код (изменения REF-006)
| Файл | Изменения |
|---|---|
| `src/ccbot/handlers/callback_data.py` | Frozen dataclasses, parsers, safe serialization |
| `src/ccbot/handlers/callback_router.py` | Updated unpacking to use typed callbacks |
| `tests/ccbot/handlers/test_callback_data.py` | Migration, round-trip, immutability, validation tests |

### Документация
| Файл | Назначение |
|---|---|
| `ccbot-refactoring-backlog.md` | Исходный анализ проблем |
| `ccbot-agentic-pipeline-plan.md` | Полный план автоматизации |
| `.agentic/HANDOFF.md` | Этот документ |
| `.agentic/README.md` | Инструкция по использованию пайплайна |

## 7. Контекст для нового агента

### Стек
- **Python 3.12+**, `uv` для управления зависимостями
- **Telegram Bot API** (python-telegram-bot v22.x)
- **tmux** как backend для управления сессиями
- **Grok / Claude** как AI-агенты для кодинга и ревью
- **GitHub Actions** для CI/CD

### Ограничения
- ❌ Не менять `.github/workflows/check.yml`
- ❌ Не модифицировать `.env` и секретные файлы
- ❌ Не использовать `window_name` для маршрутизации
- ❌ Не удалять проверки авторизации
- ❌ Не делать `git add .` (рисковать закоммитить `.agentic/out/`)

### Стиль кода
- Type hints везде (Python 3.10+ синтаксис: `X | Y`, не `Union[X, Y]`)
- Frozen dataclasses для immutable структур
- Specific exceptions вместо `except Exception`
- Structured logging вместо `print`
- Тесты: pytest + parametrize + property-based где применимо

### Как продолжить работу
1. Прочитать `ccbot-agentic-pipeline-plan.md` (§8 Пошаговый план).
2. Проверить статусы в `.agentic/backlog/tasks.json`.
3. Выбрать следующую задачу с `status: ready` и `depends_on: []` (или выполненными зависимостями).
4. Запустить локальный цикл:
   ```powershell
   python scripts/agentic/cli.py select --task REF-XXX
   python scripts/agentic/cli.py context --task REF-XXX
   # Передать implement-prompt.md + context-pack.json агенту