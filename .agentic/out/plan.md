# Agentic implementation plan

- Generated: `2026-07-17T11:04:57.931984+00:00`
- Graph commit: `9d7ab9721f09d748db242c22d04aa494cbebc72d`
- Selected: **REF-002**

## Ranking

| Rank | ID | Score | Priority | Status | Deps OK | Title |
|-----:|----|------:|----------|--------|---------|-------|
| 1 | `REF-002` | 115 | high | ready | True | Split bot.py god module into handlers |
| 2 | `REF-001` | 110 | high | done | True | Extract shared auth + session resolution helper |
| 3 | `REF-003` | 103 | high | ready | True | Replace broad except Exception with specific errors |
| 4 | `REF-007` | 93 | medium | ready | True | Rename ambiguous variables (wid/tid/skey) |
| 5 | `REF-004` | 75 | high | ready | True | Simplify session migration and stale ID logic |
| 6 | `REF-005` | 75 | high | ready | True | Encapsulate global mutable state |
| 7 | `REF-009` | 53 | medium | ready | True | Fill test gaps for bot/tmux/queue hotspots |
| 8 | `REF-006` | -23 | medium | ready | False | Migrate callback_data parsing to dataclasses |
| 9 | `REF-008` | -50 | medium | ready | False | Unify Markdown/plain fallback and RetryAfter handling |

## Selected task `REF-002`

**Split bot.py god module into handlers**

- Priority: high
- Risk: high
- Files: `src/ccbot/bot.py`

### Problem



### Solution



### Acceptance criteria

- [ ] bot.py contains only registration and lifecycle
