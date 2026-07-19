"""Telegram bot handlers package — modular handler organization.

This package contains the Telegram bot handlers split by functionality:
  - auth: is_user_allowed, get_thread_id
  - callback_data: CB_* prefixes, typed payloads, encode_*/parse_*
  - callback_router: Central inline-keyboard dispatch
  - command_handlers: Slash commands and topic lifecycle
  - message_handlers: text / photo / voice + bash capture
  - message_queue: Per-user message queue management
  - message_sender: Safe message sending helpers with MarkdownV2 fallback
  - history: Message history pagination
  - directory_browser: Directory selection UI
  - interactive_ui: Interactive UI (AskUserQuestion, Permission Prompt, etc.)
  - notifications: SessionMonitor → Telegram delivery
  - status_polling: Terminal status line polling
  - response_builder: Build paginated response messages
  - screenshot_controls: Screenshot keyboard + key maps
  - window_bind: Create+bind tmux window to topic
  - cleanup: Topic state cleanup
"""
