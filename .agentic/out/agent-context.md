# Agent context bundle

## Policies

```json

{
  "description": "Hard guardrails for agentic implementers (Claude Code Action / local agents).",
  "must": [
    "Respect AGENTS.md and CLAUDE.md core design constraints (topic-only, 1 topic = 1 window = 1 session).",
    "Keep public behavior stable unless the task explicitly allows a breaking change.",
    "Run ruff check, ruff format --check, pyright, and pytest before finishing.",
    "Add or update unit tests for non-trivial logic changes.",
    "Prefer small, reviewable diffs over large multi-concern rewrites.",
    "Use module-level docstrings on every new .py file.",
    "Preserve atomic_write_json and sensitive-env scrubbing patterns.",
    "Never commit secrets, tokens, or real ~/.ccbot state files."
  ],
  "must_not": [
    "Reintroduce non-topic / General-topic routing modes.",
    "Key internal routing by window name instead of tmux window id (@N).",
    "Truncate messages at parse layer (splitting only at send layer).",
    "Disable or weaken auth checks (is_user_allowed).",
    "Force-push to main or rewrite protected history.",
    "Modify CI quality gates to skip failing checks.",
    "Install arbitrary network-fetched scripts.",
    "Change bot token handling or env scrubbing in a less safe direction."
  ],
  "scope_rules": {
    "single_task": true,
    "one_pr_per_task": true,
    "split_if_exceeds_max_files": true,
    "prefer_extract_over_rewrite": true
  },
  "review_checklist": [
    "Does the change match the selected task id and acceptance criteria?",
    "Are new helpers pure / testable where practical?",
    "Are exception handlers specific (not bare except Exception: pass)?",
    "Do imports stay within allowed architectural layers?",
    "Is MarkdownV2 still sent only via safe_* helpers for user-facing paths?"
  ]
}


```

## Selected task

```json

{
  "id": "REF-002",
  "title": "Split bot.py god module into handlers",
  "priority": "high",
  "status": "ready",
  "categories": [
    "solid",
    "maintainability"
  ],
  "files": [
    "src/ccbot/bot.py"
  ],
  "related_nodes": [
    "file:src/ccbot/bot.py"
  ],
  "depends_on": [
    "REF-001"
  ],
  "estimated_risk": "high",
  "acceptance_criteria": [
    "bot.py contains only registration and lifecycle"
  ],
  "_ranking": {
    "id": "REF-002",
    "title": "Split bot.py god module into handlers",
    "priority": "high",
    "status": "ready",
    "score": 115,
    "components": {
      "base_priority": 70,
      "graph_bonus": 40,
      "status_bonus": 10,
      "dep_penalty": 0,
      "risk_adj": -5
    },
    "deps_satisfied": true,
    "depends_on": [
      "REF-001"
    ],
    "matched_hotspot_paths": [
      "src/ccbot/bot.py"
    ],
    "selectable": true,
    "estimated_risk": "high",
    "categories": [
      "solid",
      "maintainability"
    ],
    "files": [
      "src/ccbot/bot.py"
    ]
  }
}

```

## Implementation prompt

# Stage: IMPLEMENT

You implement **exactly one** refactoring task for the ccbot repository.

## Mandatory context

Read first:

1. `.agentic/out/selected-task.json` (or the task JSON embedded below)
2. `.agentic/policies.json`
3. `AGENTS.md` and `CLAUDE.md`
4. Files listed in the task `files` array

## Task

{
  "id": "REF-002",
  "title": "Split bot.py god module into handlers",
  "priority": "high",
  "status": "ready",
  "categories": [
    "solid",
    "maintainability"
  ],
  "files": [
    "src/ccbot/bot.py"
  ],
  "related_nodes": [
    "file:src/ccbot/bot.py"
  ],
  "depends_on": [
    "REF-001"
  ],
  "estimated_risk": "high",
  "acceptance_criteria": [
    "bot.py contains only registration and lifecycle"
  ],
  "_ranking": {
    "id": "REF-002",
    "title": "Split bot.py god module into handlers",
    "priority": "high",
    "status": "ready",
    "score": 115,
    "components": {
      "base_priority": 70,
      "graph_bonus": 40,
      "status_bonus": 10,
      "dep_penalty": 0,
      "risk_adj": -5
    },
    "deps_satisfied": true,
    "depends_on": [
      "REF-001"
    ],
    "matched_hotspot_paths": [
      "src/ccbot/bot.py"
    ],
    "selectable": true,
    "estimated_risk": "high",
    "categories": [
      "solid",
      "maintainability"
    ],
    "files": [
      "src/ccbot/bot.py"
    ]
  }
}

## Implementation protocol

1. **Explore** — confirm the problem still exists; note partial fixes.
2. **Design minimally** — smallest change that meets acceptance criteria.
3. **Implement** — only within allowed path globs from `.agentic/config.json`.
4. **Test** — add/update tests; run:
   ```bash
   uv sync --all-extras
   uv run ruff check src/ tests/
   uv run ruff format src/ tests/
   uv run pyright src/ccbot/
   uv run pytest --tb=short -q
   ```
5. **Self-review** against policies `review_checklist`.
6. **Commit** on branch `agentic/REF-002-short-slug` with message:
   `refactor(agentic): REF-002 <summary>`
7. **Open draft PR** using the project PR template prompt if `gh` is available.

## Hard constraints

- Do **not** implement multiple backlog tasks in one PR.
- Do **not** force-push or alter `main` directly.
- Do **not** weaken auth, topic-only architecture, or CI gates.
- If blocked, write `.agentic/out/blocked.md` explaining why and stop.

## Done definition

- Acceptance criteria checked off in the PR body
- All quality gates green
- Diff limited to the task scope (split if too large)
