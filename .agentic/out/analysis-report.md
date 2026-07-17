# Knowledge graph analysis — ccbot

- Generated: `2026-07-17T14:01:40.141819+00:00`
- Graph commit: `2f85b4d1ed9e922f9a53c42f997310f231bf7b7a`
- Nodes: **269** · Edges: **375** · Layers: **9**

## Layers

- **Bot Application** (`layer:bot-application`): 6 nodes
- **Message Processing** (`layer:message-processing`): 8 nodes
- **Session & State** (`layer:session-and-state`): 4 nodes
- **Terminal & Tmux** (`layer:terminal-and-tmux`): 3 nodes
- **Handlers & Interactive UI** (`layer:handlers-and-ui`): 11 nodes
- **Transcription** (`layer:transcription`): 2 nodes
- **Testing** (`layer:testing`): 19 nodes
- **Documentation** (`layer:documentation`): 8 nodes
- **Configuration** (`layer:configuration`): 2 nodes

## Top hotspots

| Path | Score | Fan-in | Fan-out | Complexity | Tested | Reasons |
|------|------:|-------:|--------:|------------|--------|---------|
| `src/ccbot/handlers/message_queue.py` | 35 | 5 | 27 | complex | False | complexity=complex, no_tested_tag_or_edge, fan_out=27, degree=32 |
| `src/ccbot/handlers/command_handlers.py` | 35 | 1 | 19 | complex | False | complexity=complex, no_tested_tag_or_edge, fan_out=19, degree=20 |
| `src/ccbot/handlers/message_handlers.py` | 35 | 1 | 17 | complex | False | complexity=complex, no_tested_tag_or_edge, fan_out=17 |
| `src/ccbot/bot.py` | 35 | 0 | 14 | complex | False | complexity=complex, no_tested_tag_or_edge, fan_out=14 |
| `src/ccbot/handlers/callback_router.py` | 35 | 1 | 12 | complex | False | complexity=complex, no_tested_tag_or_edge, fan_out=12 |
| `src/ccbot/handlers/directory_browser.py` | 35 | 3 | 10 | complex | False | complexity=complex, no_tested_tag_or_edge, fan_out=10 |
| `src/ccbot/tmux_manager.py` | 27 | 9 | 3 | complex | False | complexity=complex, no_tested_tag_or_edge |
| `src/ccbot/screenshot.py` | 27 | 2 | 9 | complex | False | complexity=complex, no_tested_tag_or_edge |
| `src/ccbot/handlers/history.py` | 27 | 2 | 8 | complex | False | complexity=complex, no_tested_tag_or_edge |
| `src/ccbot/fonts/NotoSansMonoCJKsc-Regular.otf` | 27 | 0 | 0 | complex | False | complexity=complex, no_tested_tag_or_edge |
| `src/ccbot/session.py` | 25 | 11 | 8 | complex | True | complexity=complex, fan_in=11 |
| `src/ccbot/handlers/interactive_ui.py` | 23 | 5 | 13 | complex | True | complexity=complex, fan_out=13 |
| `src/ccbot/terminal_parser.py` | 23 | 4 | 12 | complex | True | complexity=complex, fan_out=12 |
| `tests/ccbot/test_session.py` | 23 | 1 | 10 | complex | True | complexity=complex, fan_out=10 |
| `src/ccbot/markdown_v2.py` | 15 | 4 | 8 | complex | True | complexity=complex |

## Recommendations

- Prioritize refactors touching hotspots: src/ccbot/handlers/message_queue.py, src/ccbot/handlers/command_handlers.py, src/ccbot/handlers/message_handlers.py, src/ccbot/bot.py, src/ccbot/handlers/callback_router.py
- bot.py remains a structural hotspot — prefer extract-helpers (REF-001) before full split (REF-002).
- 20 src file nodes lack tested signals — schedule REF-009 coverage for top hotspots.
- Multiple complex files detected — avoid multi-file god refactors in a single agent PR.

## Untested src files (sample)

- `src/ccbot/__init__.py`
- `src/ccbot/bot.py`
- `src/ccbot/fonts/NotoSansMonoCJKsc-Regular.otf`
- `src/ccbot/handlers/__init__.py`
- `src/ccbot/handlers/auth.py`
- `src/ccbot/handlers/callback_data.py`
- `src/ccbot/handlers/callback_router.py`
- `src/ccbot/handlers/cleanup.py`
- `src/ccbot/handlers/command_handlers.py`
- `src/ccbot/handlers/directory_browser.py`
- `src/ccbot/handlers/history.py`
- `src/ccbot/handlers/message_handlers.py`
- `src/ccbot/handlers/message_queue.py`
- `src/ccbot/handlers/message_sender.py`
- `src/ccbot/handlers/notifications.py`
- `src/ccbot/handlers/screenshot_controls.py`
- `src/ccbot/handlers/window_bind.py`
- `src/ccbot/main.py`
- `src/ccbot/screenshot.py`
- `src/ccbot/tmux_manager.py`
