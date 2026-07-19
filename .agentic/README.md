# Agentic pipeline for ccbot_ju1ian

End-to-end automation from **knowledge graph + refactoring backlog** to **validated draft PRs**.

## Stages

```text
┌─────────────┐    ┌──────────┐    ┌─────────────┐    ┌────────────┐
│  ANALYZE    │ →  │  PLAN    │ →  │  IMPLEMENT  │ →  │  VALIDATE  │
│  KG+hotspot │    │ rank/pick│    │ Claude/local│    │ ruff/py/…  │
└─────────────┘    └──────────┘    └─────────────┘    └─────┬──────┘
                                                            ↓
                                                     draft PR + review
```

| Stage | Local command | GitHub workflow |
|-------|---------------|-----------------|
| Analyze | `uv run python scripts/agentic/cli.py analyze` | `agentic-analyze.yml` |
| Plan | `uv run python scripts/agentic/cli.py plan` | `agentic-plan.yml` |
| Select + prompt | `uv run python scripts/agentic/cli.py select` | (part of orchestrator) |
| Implement | agent + `implement-prompt.md` | `agentic-implement.yml` |
| Validate | `uv run python scripts/agentic/cli.py validate` | called after implement |
| Sync issues | `uv run python scripts/agentic/cli.py sync-issues --dry-run` | optional in plan |
| Full run | `uv run python scripts/agentic/cli.py run` | `agentic-orchestrator.yml` |

## Layout

```text
.agentic/
  config.json          # weights, guardrails, output paths
  policies.json        # hard must / must_not for agents
  backlog/tasks.json   # structured refactoring backlog
  prompts/             # stage prompts + PR body template
  mcp/README.md        # custom MCP server docs
  out/                 # generated reports (gitignored artifacts OK)
  README.md

scripts/agentic/       # pure-stdlib Python toolkit (3.12+)
  mcp_server.py        # MCP stdio server (ccbot-agentic)
  service.py           # shared CLI + MCP logic
  build_context_pack.py

.grok/config.toml      # project-scoped MCP registration for Grok
.github/workflows/
  agentic-analyze.yml
  agentic-plan.yml
  agentic-implement.yml
  agentic-orchestrator.yml
```

## MCP (Grok)

Server name: **`ccbot-agentic`** (tools: `ccbot-agentic__list_tasks`, …).

```powershell
# trust project folder once, then:
# /mcps  → enable ccbot-agentic
python scripts/agentic/mcp_smoke_test.py
```

See [mcp/README.md](mcp/README.md).

## Secrets / permissions

| Secret | Used by |
|--------|---------|
| `CLAUDE_CODE_OAUTH_TOKEN` | implement + review (existing Claude Code Action) |
| `GITHUB_TOKEN` | issues/PRs (default Actions token; grant `contents`/`pull-requests`/`issues`) |

Optional: `AGENTIC_AUTO_IMPLEMENT=true` repository variable to allow orchestrator to chain into implement without manual `workflow_dispatch` confirmation.

## Safety model

- Implement stage opens **draft PRs** on `agentic/<task-id>-…` branches — never commits to `main`.
- Path allow/deny lists in `config.json` → `guardrails`.
- Quality gates must pass (`ruff`, `pyright`, `pytest`) before the run is considered successful.
- Max concurrent agentic PRs controlled by `prioritization.max_open_agent_prs`.

## Typical human loop

1. Weekly analyze workflow uploads hotspot report.
2. Plan workflow refreshes ranking / optional GitHub issues.
3. You (or orchestrator) dispatch implement for `REF-00N`.
4. Claude Code Action applies the change, pushes branch, opens draft PR.
5. `check.yml` + `claude-code-review.yml` run on the PR.
6. Human merges after review.

## Updating the backlog

Edit `.agentic/backlog/tasks.json` (status: `ready` | `planned` | `in_progress` | `done` | `blocked`).  
Re-run `analyze` + `plan` after knowledge-graph refresh (`.understand-anything/`).
