# Stage: IMPLEMENT

You implement **exactly one** refactoring task for the ccbot repository.

## Mandatory context

Read first:

1. `.agentic/out/selected-task.json` (or the task JSON embedded below)
2. `.agentic/policies.json`
3. `AGENTS.md` and `CLAUDE.md`
4. Files listed in the task `files` array

## Task

{{TASK_JSON}}

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
6. **Commit** on branch `agentic/{{TASK_ID}}-short-slug` with message:
   `refactor(agentic): {{TASK_ID}} <summary>`
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
