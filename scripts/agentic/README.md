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

## Structural task evidence

Tasks with `"kind": "structural"` must carry an `evidence` block in
`.agentic/backlog/tasks.json`; tasks with `evidence` get proof-checks even
without the kind. Supported keys (all optional):

```json
"kind": "structural",
"evidence": {
  "deleted_paths": ["src/ccbot/old_module.py"],
  "call_sites": ["SessionMapRepository\("],
  "grep": [{"pattern": "_save_state", "path": "src/ccbot/handlers", "expect": "absent"}],
  "tests": ["tests/ccbot/test_session.py"]
}
```

- `deleted_paths`: each path must NOT exist (old path removed).
- `call_sites`: each regex must match at least once under `src/`
  (production call-site proof; matches in `tests/` do not count).
- `grep`: `{pattern, path, expect: present|absent}` regex checks scoped to
  a file or directory (recursive over `*.py`).
- `tests`: each test file must exist.

Enforcement:

- `python scripts/agentic/cli.py validate --task ISS-XXX` runs the evidence
  checks and fails validation when any check fails.
- `update_backlog_status.py <TASK> done` is blocked for structural/evidence
  tasks until the evidence passes (false-done guard).

See [../../.agentic/README.md](../../.agentic/README.md) for the full pipeline.
