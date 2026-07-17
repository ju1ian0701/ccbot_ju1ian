# scripts/agentic

Stdlib Python toolkit (3.12+) for the agentic pipeline. No extra package deps.

| Module | Role |
|--------|------|
| `cli.py` | CLI: analyze / plan / select / validate / sync-issues / run |
| `analyze_graph.py` | Hotspots + path scores from knowledge graph |
| `prioritize.py` | Rank backlog tasks, pick next |
| `render_prompt.py` | Build implement prompt + PR body |
| `validate_changes.py` | Path guardrails + ruff/pyright/pytest |
| `sync_issues.py` | Optional GitHub issue sync via `gh` |
| `update_backlog_status.py` | Mark tasks done/blocked/… |
| `paths.py` | Repo root + JSON helpers |

```bash
# from repo root
python scripts/agentic/cli.py analyze
python scripts/agentic/cli.py plan
python scripts/agentic/cli.py select --task REF-001
python scripts/agentic/cli.py validate --skip-quality
python scripts/agentic/cli.py run --skip-quality
python scripts/agentic/update_backlog_status.py REF-001 in_progress
```

See [../../.agentic/README.md](../../.agentic/README.md) for the full pipeline.
