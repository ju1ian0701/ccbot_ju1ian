# Stage: PLAN

You turn analysis + backlog into an executable plan for agentic implementation.

## Goals

1. Rank ready tasks using priority, dependencies, and graph hotspots.
2. Select **one** next task (or a small coherent set if explicitly allowed).
3. Expand acceptance criteria into a concrete implementation checklist.
4. Produce GitHub issue body / PR plan notes.

## Inputs

- `.agentic/out/analysis-report.json`
- `.agentic/backlog/tasks.json`
- `.agentic/policies.json`
- Optional: open PRs / issues list (from `gh`)

## Outputs

- `.agentic/out/plan.json`
- `.agentic/out/plan.md`
- `.agentic/out/selected-task.json`

## Selection rules

- Never pick a task whose `depends_on` are incomplete.
- Prefer `status: ready` over `planned`.
- Prefer high priority + high graph signal.
- Skip tasks already covered by open agentic PRs (`branch_prefix` / labels).
- Default: **one task per implementation run**.
