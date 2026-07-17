#!/usr/bin/env python3
"""Smoke-test ccbot-agentic MCP server over stdio (Content-Length framing)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "scripts" / "agentic" / "mcp_server.py"


def _frame(obj: dict) -> bytes:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii") + raw


def _read_one(proc: subprocess.Popen[bytes]) -> dict:
    assert proc.stdout is not None
    headers: dict[str, str] = {}
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("EOF from server")
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if ":" in text:
            k, v = text.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    body = proc.stdout.read(n)
    return json.loads(body.decode("utf-8"))


def main() -> int:
    env = os.environ.copy()
    env["CCBOT_REPO_ROOT"] = str(ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env=env,
    )
    assert proc.stdin and proc.stdout

    failures = 0

    def call(method: str, params: dict | None = None, msg_id: int = 1) -> dict:
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            msg["params"] = params
        proc.stdin.write(_frame(msg))
        proc.stdin.flush()
        return _read_one(proc)

    try:
        init = call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp_smoke_test", "version": "1.0"},
            },
            msg_id=1,
        )
        assert "result" in init, init
        print("OK initialize", init["result"]["serverInfo"])

        # notification — no response expected; send and continue
        proc.stdin.write(
            _frame({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        proc.stdin.flush()

        tools = call("tools/list", {}, msg_id=2)
        names = [t["name"] for t in tools["result"]["tools"]]
        print("OK tools/list", len(names), "tools:", ", ".join(names))
        required = {
            "list_tasks",
            "get_task",
            "select_next_task",
            "get_analysis",
            "get_context_pack",
            "get_implement_prompt",
            "mark_task_status",
            "run_validate",
            "run_analyze",
            "pipeline_status",
        }
        missing = required - set(names)
        if missing:
            print("FAIL missing tools", missing)
            failures += 1

        status = call(
            "tools/call",
            {"name": "pipeline_status", "arguments": {}},
            msg_id=3,
        )
        body = status["result"]["content"][0]["text"]
        data = json.loads(body)
        print("OK pipeline_status graph_present=", data.get("graph_present"))

        listed = call(
            "tools/call",
            {"name": "list_tasks", "arguments": {"status": "ready"}},
            msg_id=4,
        )
        listed_data = json.loads(listed["result"]["content"][0]["text"])
        print("OK list_tasks count=", listed_data.get("count"))

        pack = call(
            "tools/call",
            {
                "name": "get_context_pack",
                "arguments": {"task_id": "REF-001", "hops": 1},
            },
            msg_id=5,
        )
        pack_data = json.loads(pack["result"]["content"][0]["text"])
        if pack["result"].get("isError"):
            print("FAIL get_context_pack", pack_data)
            failures += 1
        else:
            print(
                "OK get_context_pack",
                pack_data.get("stats"),
                "task=",
                pack_data.get("task_id"),
            )

    except Exception as exc:  # noqa: BLE001
        print("FAIL", type(exc).__name__, exc)
        err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
        if err:
            print("--- stderr ---")
            print(err[-3000:])
        failures += 1
    finally:
        proc.kill()
        proc.wait(timeout=5)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
