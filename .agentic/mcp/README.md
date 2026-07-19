# MCP server: `ccbot-agentic`

Stdio MCP server that exposes the agentic pipeline (knowledge graph + refactoring backlog) to **Grok Build** and other MCP hosts.

## Tools

| Tool | Description |
|------|-------------|
| `pipeline_status` | Graph/backlog presence, artifacts, selected task |
| `list_tasks` | Backlog with ranking scores |
| `get_task` | Full task JSON (`REF-001` …) |
| `select_next_task` | Analyze → plan → select → render prompts + context pack |
| `get_analysis` | Slim hotspot analysis |
| `get_context_pack` | Task-scoped subgraph from `knowledge-graph.json` |
| `get_implement_prompt` | Full implement prompt for coding agent |
| `mark_task_status` | Update task status in `tasks.json` |
| `run_analyze` | Recompute analysis |
| `run_validate` | Guardrails (+ optional quality gates) |

Qualified names in Grok: `ccbot-agentic__list_tasks`, etc.

## Configuration

Project file (committed):

```toml
# .grok/config.toml
[mcp_servers.ccbot-agentic]
command = "python"
args = ["scripts/agentic/mcp_server.py"]
env = { CCBOT_REPO_ROOT = "D:\\CCbot_tmux\\ccbot\\ccbot_ju1ian" }
enabled = true
```

After cloning on another machine, update `CCBOT_REPO_ROOT` or run MCP from the repo root without that env (server uses `find_repo_root()`).

### Enable in Grok

1. Open the project folder (or set cwd to the clone).
2. Trust the folder: `/hooks-trust` or `grok --trust` (required for project MCP).
3. `/mcps` → confirm `ccbot-agentic` is enabled (refresh with `r` if needed).
4. Or: `grok mcp doctor ccbot-agentic`

### Manual smoke test

```powershell
cd D:\CCbot_tmux\ccbot\ccbot_ju1ian
$env:CCBOT_REPO_ROOT = (Get-Location).Path
python scripts/agentic/mcp_smoke_test.py
```

## Implementation

| File | Role |
|------|------|
| `scripts/agentic/mcp_server.py` | JSON-RPC MCP over stdio |
| `scripts/agentic/service.py` | Shared logic for CLI + MCP |
| `scripts/agentic/build_context_pack.py` | Subgraph slice |
| `.grok/config.toml` | Project MCP registration |

No third-party MCP SDK required (stdlib only).
