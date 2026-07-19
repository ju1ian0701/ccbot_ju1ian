# Stage: ANALYZE

You are analyzing the **ccbot** codebase using the knowledge graph and source tree.

## Goals

1. Summarize architecture layers and critical dependency paths.
2. Identify hotspots: high complexity, high fan-in/fan-out, untested files.
3. Cross-check `.agentic/backlog/tasks.json` against current code reality.
4. Emit a structured analysis report (do not modify product code in this stage).

## Inputs

- `.understand-anything/knowledge-graph.json`
- `.agentic/backlog/tasks.json`
- `.agentic/config.json`
- `AGENTS.md`, `CLAUDE.md`, `.claude/rules/*`

## Outputs

- `.agentic/out/analysis-report.json`
- `.agentic/out/analysis-report.md`

## Rules

- Read-only for `src/` and `tests/` unless fixing analysis tooling itself.
- Prefer evidence from the graph + file sizes over speculation.
- Flag backlog tasks that appear already partially done.
