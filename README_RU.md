# CCBot

[English README](README.md)
[中文文档](README_CN.md)

Удалённое управление сессиями Claude Code через Telegram — мониторинг, интерактивное управление и работа с AI-сессиями в tmux.

https://github.com/user-attachments/assets/15ffb38e-5eb9-4720-93b9-412e4961dc93

## Зачем CCBot?

Claude Code работает в терминале. Когда вы отходите от компьютера — в дороге, дома или просто не за рабочим местом — сессия продолжает выполняться, но вы теряете видимость и контроль.

CCBot позволяет **бесшовно продолжать ту же самую сессию через Telegram**. Ключевая идея: он работает поверх **tmux**, а не через Claude Code SDK. Процесс Claude Code остаётся в tmux-окне на вашей машине, а CCBot только читает вывод и отправляет нажатия клавиш. Это означает:

- **Переключение с десктопа на телефон в середине работы** — Claude делает рефакторинг? Можно отойти и продолжать наблюдать/отвечать из Telegram.
- **Мгновенное возвращение к десктопу** — tmux-сессия не прерывается; `tmux attach` возвращает вас в тот же терминал с полной историей и контекстом.
- **Параллельная работа с несколькими сессиями** — каждый Telegram topic соответствует отдельному tmux-окну.

Большинство других Telegram-ботов для Claude Code используют отдельные API-сессии через SDK. Такие сессии изолированы и не продолжаются в вашем терминале. CCBot работает иначе: это тонкий слой управления над tmux, поэтому терминал остаётся источником истины, и вы всегда можете вернуться к нему.

## Возможности

- **Сессии по темам** — каждый Telegram topic 1:1 связан с tmux-окном и Claude-сессией
- **Уведомления в реальном времени** — ответы ассистента, thinking-контент, tool use/result, вывод локальных команд
- **Интерактивный UI** — управление AskUserQuestion, ExitPlanMode и Permission Prompt через inline-клавиатуру
- **Голосовые сообщения** — голосовые сообщения транскрибируются через OpenAI и пересылаются как текст
- **Отправка сообщений** — проброс текста в Claude Code через tmux
- **Проброс slash-команд** — любая `/command` уходит напрямую в Claude Code (например, `/clear`, `/compact`, `/cost`)
- **Создание новых сессий** — запуск Claude Code из Telegram через браузер директорий
- **Возобновление сессий** — выберите существующую Claude-сессию в директории, чтобы продолжить с того места, где остановились
- **Завершение сессий** — закрытие topic автоматически завершает связанное tmux-окно
- **История сообщений** — пагинация истории диалога (сначала новые)
- **Трекинг сессий через hook** — авто-связывание tmux-окон с Claude-сессиями через `SessionStart`
- **Персистентное состояние** — привязки topic/window и read-offset сохраняются после перезапуска

## Требования

- **tmux** — должен быть установлен и доступен в PATH
- **Claude Code** — CLI-инструмент `claude` должен быть установлен

## Установка

### Вариант 1: установка из GitHub (рекомендуется)

```bash
# Через uv (рекомендуется)
uv tool install git+https://github.com/six-ddc/ccmux.git

# Или через pipx
pipx install git+https://github.com/six-ddc/ccmux.git
```

### Вариант 2: установка из исходников

```bash
git clone https://github.com/six-ddc/ccmux.git
cd ccmux
uv sync
```

## Конфигурация

**1. Создайте Telegram-бота и включите Threaded Mode:**

1. Напишите [@BotFather](https://t.me/BotFather), создайте бота и получите токен
2. Откройте профиль @BotFather и нажмите **Open App**
3. Выберите вашего бота, затем **Settings** > **Bot Settings**
4. Включите **Threaded Mode**

**2. Настройте переменные окружения:**

Создайте `~/.ccbot/.env`:

```ini
TELEGRAM_BOT_TOKEN=your_bot_token_here
ALLOWED_USERS=your_telegram_user_id
```

**Обязательные:**

| Переменная | Описание |
| ---------- | -------- |
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather |
| `ALLOWED_USERS` | Список Telegram user ID через запятую |

**Опциональные:**

| Переменная | По умолчанию | Описание |
| ---------- | ------------ | -------- |
| `CCBOT_DIR` | `~/.ccbot` | Каталог конфигурации/состояния (`.env` грузится отсюда) |
| `TMUX_SESSION_NAME` | `ccbot` | Имя tmux-сессии |
| `CCBOT_GROUP_CHAT_ID` | _(не задано)_ | Группа форума для сессий, созданных через `ccbot new`; не задано = группа, в которую последний раз писал пользователь |
| `CLAUDE_COMMAND` | `claude` | Команда запуска в новых окнах |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Каталог конфигурации Claude Code; сессии читаются из его подкаталога `projects/` |
| `CCBOT_CLAUDE_PROJECTS_PATH` | _(не задано)_ | Явный путь к каталогу projects (приоритетнее `CLAUDE_CONFIG_DIR`) |
| `MONITOR_POLL_INTERVAL` | `2.0` | Интервал опроса в секундах |
| `CCBOT_SHOW_USER_MESSAGES` | `true` | Показывать сообщения пользователя (с префиксом 👤) в истории и уведомлениях |
| `CCBOT_SHOW_TOOL_CALLS` | `true` | Показывать уведомления tool_use/tool_result |
| `CCBOT_SHOW_HIDDEN_DIRS` | `false` | Показывать скрытые (dot) директории в браузере каталогов |
| `CCBOT_HTTP_POOL_SIZE` | `256` | Размер пула соединений основного HTTP-клиента Telegram |
| `CCBOT_HTTP_POOL_TIMEOUT` | `10.0` | Секунды ожидания свободного соединения в любом из пулов Telegram |
| `CCBOT_GET_UPDATES_POOL_SIZE` | `4` | Размер пула соединений long-poll клиента `getUpdates` |
| `CCBOT_POLL_STALL_SOFT` | `90.0` | Секунды без успешного `getUpdates`, после которых HTTP-клиент пересоздаётся |
| `CCBOT_POLL_STALL_HARD` | `300.0` | Секунды зависшего polling (при доступном Telegram), после которых процесс завершается |
| `OPENAI_API_KEY` | _(нет)_ | API-ключ OpenAI для транскрипции голосовых сообщений |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Базовый URL OpenAI API (для прокси или совместимых API) |
| `CCBOT_WHISPER_BACKEND` | `openai` | Бэкенд транскрипции голоса: `openai`, `local` (faster-whisper) или `off` |
| `CCBOT_WHISPER_MODEL` | `small` | Модель Whisper для бэкенда `local` |
| `CCBOT_WHISPER_LANGUAGE` | `auto` | Язык транскрипции: `auto`, `en` или `ru` |

Сообщения конвертируются из Markdown в Telegram MarkdownV2 (`markdown_v2.py`, `telegramify-markdown`). Если Telegram отклоняет форматирование, сообщение отправляется как простой текст.

> Если бот запущен на VPS без интерактивного терминала для подтверждений, можно использовать:
>
> ```
> CLAUDE_COMMAND=IS_SANDBOX=1 claude --dangerously-skip-permissions
> ```

## Настройка Hook (рекомендуется)

Авто-установка через CLI:

```bash
ccbot hook --install
```

Команда добавляет в `~/.claude/settings.json` пять hook-ов: `SessionStart`, `SessionEnd`, `Notification`, `Stop` и `StopFailure`. Установка идемпотентна: события, для которых уже есть hook ccbot, пропускаются, hook-и других инструментов не изменяются. После обновления ccbot запустите её снова.

Или вручную добавьте в `~/.claude/settings.json`:

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

`SessionStart` записывает отображение window-session в `$CCBOT_DIR/session_map.json` (по умолчанию `~/.ccbot/`), чтобы бот автоматически отслеживал, какая Claude-сессия работает в каждом tmux-окне — даже после `/clear` или рестарта сессии. Остальные четыре события добавляются в `$CCBOT_DIR/hook_events.jsonl`, и бот реагирует на них без разбора терминала: `Notification`, требующий ввода, показывается как «Claude ждёт вас», даже если парсер терминала не узнал запрос; `Stop` убирает устаревшее сообщение статуса по окончании хода; `StopFailure` сообщает об ошибках API; `SessionEnd` сообщает, что сессия завершилась.

## Использование

```bash
# Если установлено через uv tool / pipx
ccbot

# Если запуск из исходников
uv run ccbot
```

### Команды

**Команды бота:**

| Команда | Описание |
| ------- | -------- |
| `/start` | Показать приветственное сообщение |
| `/new` | Начать новую сессию Claude (см. ниже) |
| `/menu` | Кнопки управления сессией: Screenshot, Esc, New session, End session |
| `/history` | История сообщений для текущего topic |
| `/screenshot` | Снимок терминала с управляющими клавишами |
| `/esc` | Отправить Escape для прерывания Claude |
| `/unbind` | Отвязать topic от сессии (окно tmux продолжает работать) |
| `/usage` | Показать остаток использования Claude Code |
| `/kill` | Завершить сессию (закрывает её окно tmux) после подтверждения |
| `/health` | Диагностика бота и парсера для этого topic |
| `/login` | Войти в Claude Code заново |

**Команды Claude Code (пробрасываются через tmux):**

| Команда | Описание |
| ------- | -------- |
| `/clear` | Очистить историю диалога |
| `/compact` | Уплотнить контекст диалога |
| `/cost` | Показать статистику токенов/стоимости |
| `/help` | Справка Claude Code |
| `/memory` | Редактировать CLAUDE.md |
| `/model` | Сменить модель ИИ |

Любая неизвестная `/command` также пробрасывается в Claude Code как есть (например, `/review`, `/doctor`, `/init`).

**Команды сессии подробнее:**

- `/new` — в General topic бот создаёт новый topic и открывает там браузер директорий. В topic, где уже есть сессия, предлагает **Restart here** (завершить сессию и начать новую в той же директории и том же topic) или **New topic**. В пустом topic запускается обычный сценарий привязки.
- `/kill` — запрашивает подтверждение и завершает сессию (окно tmux закрывается). В подтверждении также есть **End and close topic**.
- `/menu` — показывает панель сессии (Screenshot, Esc, New session, End session). Та же панель прикреплена к сообщению «session created».
- `/health` — показывает аптайм бота, состояние polling Telegram, установленную версию Claude Code и проверенную версию, какие hook-и установлены и состояние парсера для окна этого topic.
- `/login` — отправляет `/login` в Claude Code. Выбор способа входа появляется в Telegram с кнопками навигации. Когда Claude Code показывает URL для входа, бот публикует кнопку **Open sign-in page**. Отправьте код сообщением: бот передаст его в Claude Code и удалит ваше сообщение.

Создание, закрытие и удаление topic требует, чтобы бот был администратором группы с правом **Manage Topics**.

### Workflow по topic

**1 topic = 1 window = 1 session.** Бот работает в режиме Telegram Forum Topics.

**Создание новой сессии:**

1. Создайте новый topic в Telegram-группе
2. Отправьте любое сообщение в topic
3. Появится браузер директорий — выберите каталог проекта
4. Если в каталоге есть существующие Claude-сессии, появится выбор сессий — возобновите существующую или начните новую
5. Будет создано tmux-окно, запустится `claude` (с `--resume` при возобновлении), и ваше отложенное сообщение отправится в сессию

**Отправка сообщений:**

После привязки topic к сессии отправляйте текст или голосовые сообщения в topic — текст уходит в Claude Code через tmux, голосовые сообщения автоматически транскрибируются и пересылаются как текст.

**Завершение сессии:**

Закройте (или удалите) topic в Telegram. Связанное tmux-окно будет автоматически завершено, привязка удалена.

### История сообщений

Навигация через inline-кнопки:

```
📋 [project-name] Messages (42 total)

───── 14:32 ─────

👤 fix the login bug

───── 14:33 ─────

I'll look into the login bug...

[◀ Older]    [2/9]    [Newer ▶]
```

### Уведомления

Монитор опрашивает session JSONL-файлы каждые 2 секунды и отправляет уведомления о:

- **Ответах ассистента** — текстовые ответы Claude
- **Thinking-контенте** — отображается как раскрываемые цитаты
- **Tool use/result** — краткие сводки (например, `Read 42 lines`, `Found 5 matches`)
- **Выводе локальных команд** — stdout команд вроде `git status`, префикс `❯ command_name`

Уведомления отправляются в topic, привязанный к окну сессии.

Примечание по форматированию:
- Сообщения конвертируются из Markdown в Telegram MarkdownV2 модулем `markdown_v2.py` (`telegramify-markdown`), при ошибке разбора отправляются как простой текст
- Сообщения длиннее лимита Telegram в 4096 символов делятся функцией `telegram_sender.split_message`

## Команды в терминале

Помимо самого бота, команда `ccbot` имеет такие подкоманды:

| Команда | Описание |
| ------- | -------- |
| `ccbot` | Запустить Telegram-бота |
| `ccbot hook --install` | Установить hook-и Claude Code в `~/.claude/settings.json` (см. «Настройка Hook») |
| `ccbot new [DIR]` | Создать сессию Claude и привязанный topic Telegram через работающего бота |
| `ccbot selfcheck` | Проверить парсер терминала на установленном Claude Code |

Флаги `ccbot new [DIR]` (по умолчанию `DIR` — текущий каталог):

| Флаг | Описание |
| ---- | -------- |
| `--name NAME` | Имя topic и окна |
| `--resume SESSION_ID` | Возобновить существующую сессию Claude |
| `--user-id ID` | Telegram user id (нужен, если в `ALLOWED_USERS` несколько пользователей) |
| `--attach` | Подключиться к новому окну tmux (внутри tmux — `switch-client`) |
| `--timeout SECONDS` | Сколько секунд ждать ответ бота (по умолчанию `20`) |

`ccbot new` сам окно не создаёт. Он записывает файл запроса в `<CCBOT_DIR>/control/` и ждёт, пока работающий бот создаст окно tmux и привязанный topic Telegram. Бот должен быть запущен, иначе команда завершится ошибкой по таймауту. Группа форума выбирается так: `CCBOT_GROUP_CHAT_ID`, если он задан, иначе группа, в которую последний раз писал пользователь.

`ccbot selfcheck` запускает настоящий Claude Code в отдельном сервере tmux (со своим сокетом, никогда не сервер бота) и проверяет, какие шаблоны парсера всё ещё совпадают с его экранами. Флаги:

| Флаг | Описание |
| ---- | -------- |
| `--command CMD` | Команда запуска Claude (по умолчанию `CLAUDE_COMMAND`, иначе `claude`) |
| `--save-dir DIR` | Записать каждый снятый pane как `<check>.txt` |
| `--with-prompt` | Отправить также один промпт (тратит токены модели) для проверки строки статуса |
| `--timeout SECONDS` | Сколько секунд ждать каждый ожидаемый экран (по умолчанию `30`) |
| `--keep` | Не удалять сервер tmux и временные каталоги (для отладки) |

Проверки: `startup`, `usage`, `model` и `status` (последняя только с `--with-prompt`). Команда завершается с кодом 1, если какая-либо проверка не прошла.

## Надёжность и обновления

- **Watchdog polling Telegram** — если `getUpdates` зависает, HTTP-клиент пересоздаётся. Процесс завершается, чтобы supervisor перезапустил его, только когда Telegram доступен, а polling всё ещё завис (upstream ccbot issue #79). Запускайте бота под supervisor, например systemd с `Restart=always`.
- **Вложенные сессии Claude** — `claude -p`, запущенный изнутри сессии (например, из её Bash-инструмента), больше не перехватывает сессию окна (upstream ccbot issue #72).
- **Shell guard** — если Claude завершился и в окне показан приглашение shell, сообщения не вводятся в shell. Бот предлагает **Restart Claude** или **End session**.
- **Неизвестные экраны** — если Claude ждёт ввода на экране, который парсер не знает, бот отправляет скриншот с управляющими клавишами.
- **После обновления Claude Code** — запустите `ccbot selfcheck`. При ошибке обновите `UI_PATTERNS` и `STATUS_SPINNERS` в `terminal_parser.py`, добавьте снятый pane (`--save-dir`) как тестовую фикстуру и поднимите `TESTED_CLAUDE_CODE`. Чтобы обновления происходили осознанно, отключите автообновление Claude Code: `DISABLE_AUTOUPDATER=1`.

## Запуск Claude Code в tmux

### Вариант 1: создать через Telegram (рекомендуется)

1. Создайте новый topic в Telegram-группе
2. Отправьте любое сообщение
3. Выберите каталог проекта в браузере

### Вариант 2: создать вручную

```bash
tmux attach -t ccbot
tmux new-window -n myproject -c ~/Code/myproject
# Затем запустите Claude Code в новом окне
claude
```

Окно должно находиться в tmux-сессии `ccbot` (настраивается через `TMUX_SESSION_NAME`). Hook автоматически зарегистрирует его в `session_map.json` при запуске Claude.

## Обзор архитектуры

```
┌─────────────┐      ┌─────────────┐      ┌─────────────┐
│  Topic ID   │ ───▶ │ Window ID   │ ───▶ │ Session ID  │
│ (Telegram)  │      │ (tmux @id)  │      │  (Claude)   │
└─────────────┘      └─────────────┘      └─────────────┘
   thread_bindings       session_map.json
   (state.json)          (записывается hook)
```

## Хранение данных

| Путь | Описание |
| ---- | -------- |
| `$CCBOT_DIR/state.json` | Привязки topic, состояния окон, display names и read-offset на пользователя |
| `$CCBOT_DIR/session_map.json` | Hook-таблица `{tmux_session:window_id: {session_id, cwd, window_name}}` |
| `$CCBOT_DIR/monitor_state.json` | Byte-offset монитора по сессиям (предотвращает дубли) |
| `$CCBOT_DIR/hook_events.jsonl` | Журнал hook-событий (Notification, Stop, StopFailure, SessionEnd), который читает бот |
| `$CCBOT_DIR/control/` | Файлы запрос/результат для `ccbot new` (пишет CLI, отвечает бот) |
| `~/.claude/projects/` | Данные сессий Claude Code (только чтение) |

## Структура файлов

```
src/ccbot/
├── __init__.py            # Точка входа пакета
├── main.py                # CLI-диспетчер (hook подкоманда + запуск бота)
├── hook.py                # Hook-подкоманда для трекинга сессий (+ --install)
├── config.py              # Конфигурация из переменных окружения
├── bot.py                 # Настройка Telegram-бота, обработчики команд, topic routing
├── session.py             # Управление сессиями, persist состояния, история сообщений
├── session_monitor.py     # Мониторинг JSONL-файлов (polling + обнаружение изменений)
├── monitor_state.py       # Persist состояния монитора (byte-offset)
├── transcript_parser.py   # Парсинг JSONL-транскриптов Claude Code
├── terminal_parser.py     # Парсинг terminal pane (interactive UI + status line)
├── markdown_v2.py         # Markdown -> Telegram MarkdownV2 (telegramify-markdown)
├── telegram_sender.py     # split_message: разбиение длинных сообщений по лимиту 4096 символов
├── screenshot.py          # Terminal text -> PNG с поддержкой ANSI-цветов
├── transcribe.py          # Транскрипция голоса в текст через OpenAI API
├── utils.py               # Общие утилиты (atomic JSON writes, JSONL helpers)
├── tmux_manager.py        # Управление tmux-окнами (list, create, send keys, kill)
├── hook_events.py         # Канал hook-событий (запись и чтение hook_events.jsonl)
├── health.py              # Watchdog polling, проверка версии Claude Code, отчёт /health
├── control.py             # Файлы запрос/результат между `ccbot new` и ботом
├── cli_new.py             # Терминальный клиент `ccbot new`
├── selfcheck.py           # `ccbot selfcheck`: проверка парсера на реальном Claude Code
├── fonts/                 # Встроенные шрифты для рендера скриншотов
└── handlers/
    ├── __init__.py        # Экспорты handler-модулей
    ├── callback_data.py   # Константы callback data (префиксы CB_*)
    ├── directory_browser.py # Inline UI браузера директорий
    ├── history.py         # Пагинация истории сообщений
    ├── interactive_ui.py  # Обработка interactive UI (AskUser, ExitPlan, Permissions)
    ├── message_queue.py   # Очередь сообщений на пользователя + worker (merge, rate limit)
    ├── message_sender.py  # safe_reply / safe_edit / safe_send helpers
    ├── response_builder.py # Сборка ответных сообщений (tool_use, thinking и т.д.)
    ├── session_lifecycle.py # Завершение/перезапуск сессии, shell guard, панели сессии
    ├── callback_lifecycle.py # Callback-и sl:* (завершение, перезапуск, панель /new)
    ├── bind_flow.py       # Привязка непривязанных topic и автосоздаваемых topic
    ├── hook_reactions.py  # Реакции на hook-события (запросы, Stop, StopFailure, SessionEnd)
    ├── control_requests.py # Исполнение запросов от `ccbot new`
    └── status_polling.py  # Polling terminal status line
```

## Участники

Спасибо всем, кто вносит вклад! Мы поощряем использование Claude Code для совместной разработки.

<a href="https://github.com/six-ddc/ccmux/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=six-ddc/ccmux" />
</a>
