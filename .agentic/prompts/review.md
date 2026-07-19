# Stage: REVIEW (agentic PR)

Review this pull request as a senior Python engineer familiar with Telegram bots, asyncio, and tmux session bridging.

## Focus

1. Correctness vs the claimed task id and acceptance criteria
2. Architecture constraints from AGENTS.md / CLAUDE.md
3. Concurrency and error-handling regressions
4. Test adequacy
5. Scope creep beyond the single task

## Output format

- **Summary** (2–4 sentences)
- **Must-fix** (blocking)
- **Should-fix** (non-blocking)
- **Nits**
- **Verdict**: `approve` | `request_changes` | `comment`

## Do not

- Rewrite the PR yourself in review-only mode
- Approve if quality gates fail or auth/topic constraints are violated
