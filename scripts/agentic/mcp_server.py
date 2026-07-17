#!/usr/bin/env python3
"""Stdio MCP server: ccbot-agentic.

Exposes the agentic pipeline (knowledge graph + backlog + validate) as MCP tools
for Grok / Claude / any MCP host.

Transport: JSON-RPC 2.0 over stdin/stdout (Model Context Protocol subset).
Dependencies: Python 3.12+ stdlib only.

Configure (project scope)::

    # .grok/config.toml
    [mcp_servers.ccbot-agentic]
    command = "python"
    args = ["scripts/agentic/mcp_server.py"]
    env = { CCBOT_REPO_ROOT = "D:\\\\CCbot_tmux\\\\ccbot\\\\ccbot_ju1ian" }
    enabled = true

Or with absolute script path so cwd does not matter.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

# Ensure scripts/agentic is on path when launched as a script
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

# Resolve repo root early (env override for MCP hosts that set cwd elsewhere)
_REPO_ENV = os.environ.get("CCBOT_REPO_ROOT") or os.environ.get("AGENTIC_REPO_ROOT")
if _REPO_ENV:
    os.chdir(_REPO_ENV)

from paths import find_repo_root  # noqa: E402
from service import (  # noqa: E402
    dumps_result,
    get_analysis,
    get_context_pack,
    get_implement_prompt,
    get_task,
    list_tasks,
    mark_task_status,
    pipeline_status,
    select_next_task,
    validate,
)


SERVER_NAME = "ccbot-agentic"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"


def _root() -> Path:
    env = os.environ.get("CCBOT_REPO_ROOT") or os.environ.get("AGENTIC_REPO_ROOT")
    if env:
        return Path(env).resolve()
    return find_repo_root(Path.cwd())


def _ok_text(data: Any) -> dict[str, Any]:
    text = data if isinstance(data, str) else dumps_result(data)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _err_text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def tool_list_tasks(args: dict[str, Any]) -> dict[str, Any]:
    return _ok_text(
        list_tasks(
            status=args.get("status"),
            root=_root(),
            include_ranking=bool(args.get("include_ranking", True)),
        )
    )


def tool_get_task(args: dict[str, Any]) -> dict[str, Any]:
    task_id = args.get("task_id")
    if not task_id:
        return _err_text("task_id is required")
    return _ok_text(get_task(str(task_id), root=_root()))


def tool_select_next_task(args: dict[str, Any]) -> dict[str, Any]:
    return _ok_text(
        select_next_task(
            task_id=args.get("task_id") or None,
            root=_root(),
            skip_analyze=bool(args.get("skip_analyze", False)),
        )
    )


def tool_get_analysis(args: dict[str, Any]) -> dict[str, Any]:
    return _ok_text(
        get_analysis(root=_root(), refresh=bool(args.get("refresh", False)))
    )


def tool_get_context_pack(args: dict[str, Any]) -> dict[str, Any]:
    hops = int(args.get("hops") or 1)
    return _ok_text(
        get_context_pack(
            task_id=args.get("task_id") or None,
            hops=hops,
            root=_root(),
            refresh=bool(args.get("refresh", True)),
        )
    )


def tool_get_implement_prompt(args: dict[str, Any]) -> dict[str, Any]:
    # optional re-select
    if args.get("task_id"):
        select_next_task(task_id=str(args["task_id"]), root=_root())
    return _ok_text(get_implement_prompt(root=_root()))


def tool_mark_task_status(args: dict[str, Any]) -> dict[str, Any]:
    task_id = args.get("task_id")
    status = args.get("status")
    if not task_id or not status:
        return _err_text("task_id and status are required")
    return _ok_text(
        mark_task_status(
            str(task_id),
            str(status),
            note=str(args.get("note") or ""),
            root=_root(),
        )
    )


def tool_run_validate(args: dict[str, Any]) -> dict[str, Any]:
    return _ok_text(
        validate(
            base_ref=args.get("base_ref"),
            skip_quality=bool(args.get("skip_quality", True)),
            root=_root(),
        )
    )


def tool_pipeline_status(args: dict[str, Any]) -> dict[str, Any]:
    return _ok_text(pipeline_status(root=_root()))


def tool_run_analyze(args: dict[str, Any]) -> dict[str, Any]:
    from analyze_graph import run as run_analyze

    report = run_analyze(repo_root=_root())
    return _ok_text(
        {
            "ok": True,
            "stats": report.get("stats"),
            "hotspots_top": [
                {"path": h.get("path"), "score": h.get("score")}
                for h in (report.get("hotspots") or [])[:10]
            ],
            "recommendations": report.get("recommendations"),
        }
    )


TOOLS: dict[str, dict[str, Any]] = {
    "list_tasks": {
        "description": (
            "List agentic backlog tasks (REF-*) with optional status filter and ranking scores. "
            "Use to see what refactoring work is ready."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Comma-separated statuses: ready,planned,in_progress,done,blocked,cancelled. "
                        "Omit for all."
                    ),
                },
                "include_ranking": {
                    "type": "boolean",
                    "description": "Include score/selectable from plan (default true).",
                    "default": True,
                },
            },
        },
        "handler": tool_list_tasks,
    },
    "get_task": {
        "description": "Get full JSON for one backlog task by id (e.g. REF-001).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task id, e.g. REF-001",
                }
            },
            "required": ["task_id"],
        },
        "handler": tool_get_task,
    },
    "select_next_task": {
        "description": (
            "Analyze knowledge graph (unless skip_analyze), rank backlog, select next task, "
            "render implement-prompt.md and context-pack.json. Optionally force task_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Force this task id instead of auto-select.",
                },
                "skip_analyze": {
                    "type": "boolean",
                    "description": "Skip knowledge-graph analyze step (default false).",
                    "default": False,
                },
            },
        },
        "handler": tool_select_next_task,
    },
    "get_analysis": {
        "description": (
            "Return slim knowledge-graph analysis: hotspots, layers, recommendations. "
            "Set refresh=true to recompute from knowledge-graph.json."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "default": False,
                }
            },
        },
        "handler": tool_get_analysis,
    },
    "get_context_pack": {
        "description": (
            "Build or return a task-scoped subgraph slice from the knowledge graph "
            "(nodes/edges/layers around task files) for agent prompts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task id; default = currently selected task.",
                },
                "hops": {
                    "type": "integer",
                    "description": "Graph neighborhood hops (default 1).",
                    "default": 1,
                },
                "refresh": {
                    "type": "boolean",
                    "default": True,
                },
            },
        },
        "handler": tool_get_context_pack,
    },
    "get_implement_prompt": {
        "description": (
            "Return the full implement-prompt.md (+ pr body) for the selected task. "
            "Optionally pass task_id to re-select first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
            },
        },
        "handler": tool_get_implement_prompt,
    },
    "mark_task_status": {
        "description": "Update backlog task status in .agentic/backlog/tasks.json.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "enum": [
                        "ready",
                        "planned",
                        "in_progress",
                        "done",
                        "blocked",
                        "cancelled",
                    ],
                },
                "note": {"type": "string"},
            },
            "required": ["task_id", "status"],
        },
        "handler": tool_mark_task_status,
    },
    "run_validate": {
        "description": (
            "Run path guardrails (+ optional ruff/pyright/pytest) on the working tree. "
            "skip_quality=true (default) only checks path allow/deny lists."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_ref": {
                    "type": "string",
                    "description": "Git base ref for diff, e.g. origin/main",
                },
                "skip_quality": {
                    "type": "boolean",
                    "default": True,
                },
            },
        },
        "handler": tool_run_validate,
    },
    "run_analyze": {
        "description": "Recompute knowledge-graph hotspot analysis into .agentic/out/.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_run_analyze,
    },
    "pipeline_status": {
        "description": (
            "Health check: repo root, graph/backlog presence, generated artifacts, selected task."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_pipeline_status,
    },
}


def _tools_list_payload() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": meta["description"],
            "inputSchema": meta["inputSchema"],
        }
        for name, meta in TOOLS.items()
    ]


def _handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if name not in TOOLS:
        return _err_text(f"Unknown tool: {name}")
    handler: Callable[[dict[str, Any]], dict[str, Any]] = TOOLS[name]["handler"]
    try:
        return handler(arguments if isinstance(arguments, dict) else {})
    except Exception as exc:  # noqa: BLE001 — return to MCP host
        tb = traceback.format_exc(limit=8)
        return _err_text(f"{type(exc).__name__}: {exc}\n{tb}")


def _dispatch(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Return JSON-RPC response dict, or None for notifications."""
    method = msg.get("method")
    msg_id = msg.get("id", None)
    params = msg.get("params") or {}

    # Notifications (no id) — acknowledge silently
    if msg_id is None and method and not method.startswith("tools/"):
        if method == "notifications/initialized":
            return None
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": _tools_list_payload()},
        }

    if method == "tools/call":
        result = _handle_tools_call(params if isinstance(params, dict) else {})
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}

    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}

    # Unknown method
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _read_message() -> dict[str, Any] | None:
    """Read one JSON-RPC message (Content-Length framing or newline-delimited)."""
    # Prefer Content-Length framing (MCP standard)
    header_lines: list[str] = []
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        header_lines.append(line.decode("utf-8", errors="replace").rstrip("\r\n"))
        # Newline-delimited JSON fallback: first line is full JSON
        if not header_lines[0].lower().startswith("content-length:") and header_lines[
            0
        ].lstrip().startswith("{"):
            return json.loads(header_lines[0])

    headers = {}
    for h in header_lines:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(msg: dict[str, Any]) -> None:
    raw = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def main() -> int:
    # Log root to stderr only (stdout is protocol)
    try:
        root = _root()
        sys.stderr.write(f"[ccbot-agentic] repo_root={root}\n")
        sys.stderr.flush()
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[ccbot-agentic] failed to resolve root: {exc}\n")
        sys.stderr.flush()

    while True:
        try:
            msg = _read_message()
        except json.JSONDecodeError as exc:
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
            )
            continue
        if msg is None:
            break
        if not isinstance(msg, dict):
            continue
        # batch not supported
        resp = _dispatch(msg)
        if resp is not None:
            _write_message(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
