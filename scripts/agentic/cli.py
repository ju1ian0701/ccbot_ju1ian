#!/usr/bin/env python3
"""CLI entrypoint for the ccbot agentic pipeline.

Usage:
  python scripts/agentic/cli.py analyze
  python scripts/agentic/cli.py plan [--task REF-001]
  python scripts/agentic/cli.py select [--task REF-001]
  python scripts/agentic/cli.py validate [--base-ref origin/main] [--skip-quality]
  python scripts/agentic/cli.py sync-issues [--apply]
  python scripts/agentic/cli.py run [--task REF-001] [--skip-quality]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as script: python scripts/agentic/cli.py
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from analyze_graph import run as run_analyze  # noqa: E402
from build_context_pack import run as run_context  # noqa: E402
from paths import find_repo_root  # noqa: E402
from prioritize import run as run_plan  # noqa: E402
from render_prompt import run as run_render  # noqa: E402
from service import list_tasks as svc_list_tasks  # noqa: E402
from service import pipeline_status as svc_pipeline_status  # noqa: E402
from sync_issues import sync as run_sync  # noqa: E402
from validate_changes import run as run_validate  # noqa: E402


def cmd_analyze(_args: argparse.Namespace) -> int:
    report = run_analyze()
    stats = report.get("stats") or {}
    print(
        f"analyze_ok nodes={stats.get('nodes')} edges={stats.get('edges')} "
        f"hotspots={len(report.get('hotspots') or [])}"
    )
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    plan = run_plan(task_id=args.task)
    print(f"plan_ok selected={plan.get('selected_task_id')}")
    for r in (plan.get("ranked_tasks") or [])[:5]:
        print(f"  {r.get('score'):4}  {r.get('id')}  {r.get('status')}  {r.get('title')}")
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    plan = run_plan(task_id=args.task)
    if not plan.get("selected_task_id"):
        print("select_failed: no selectable task", file=sys.stderr)
        return 1
    paths = run_render()
    print(f"select_ok task={plan.get('selected_task_id')}")
    for k, v in paths.items():
        print(f"  {k}={v}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    report = run_validate(base_ref=args.base_ref, skip_quality=args.skip_quality)
    print(json.dumps({"ok": report.get("ok"), "guardrails": report.get("guardrails")}, indent=2))
    for q in report.get("quality") or []:
        status = "OK" if q.get("ok") else "FAIL"
        print(f"[{status}] {' '.join(q.get('cmd') or [])}")
    return 0 if report.get("ok") else 1


def cmd_sync(args: argparse.Namespace) -> int:
    result = run_sync(dry_run=not args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Full local pipeline without LLM implement step."""
    print("== ANALYZE ==")
    run_analyze()
    print("== PLAN ==")
    plan = run_plan(task_id=args.task)
    print(f"selected={plan.get('selected_task_id')}")
    if not plan.get("selected_task_id"):
        print("No task selected; stop before implement.")
        return 1
    print("== RENDER PROMPTS ==")
    paths = run_render()
    for k, v in paths.items():
        print(f"  {k}={v}")
    print("== CONTEXT PACK ==")
    pack = run_context(task_id=plan.get("selected_task_id"))
    print(f"  nodes={pack.get('stats', {}).get('nodes')} edges={pack.get('stats', {}).get('edges')}")
    print("== VALIDATE (workspace) ==")
    report = run_validate(base_ref=args.base_ref, skip_quality=args.skip_quality)
    print(f"validate_ok={report.get('ok')}")
    print()
    print("Next: feed .agentic/out/implement-prompt.md to Claude Code / agent,")
    print("or use MCP tools: ccbot-agentic__get_implement_prompt")
    print("then re-run: python scripts/agentic/cli.py validate")
    return 0 if report.get("ok") or args.skip_quality else 0


def cmd_context(args: argparse.Namespace) -> int:
    pack = run_context(task_id=args.task, hops=args.hops)
    print(
        f"context_ok task={pack.get('task_id')} "
        f"nodes={pack['stats']['nodes']} edges={pack['stats']['edges']}"
    )
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    print(json.dumps(svc_pipeline_status(), indent=2, ensure_ascii=False))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    print(json.dumps(svc_list_tasks(status=args.status), indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentic", description="ccbot agentic pipeline CLI")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("analyze", help="Analyze knowledge graph → .agentic/out/")

    p_plan = sub.add_parser("plan", help="Rank backlog and select next task")
    p_plan.add_argument("--task", help="Force task id (e.g. REF-001)")

    p_sel = sub.add_parser("select", help="Plan + render implement prompt")
    p_sel.add_argument("--task", help="Force task id")

    p_ctx = sub.add_parser("context", help="Build knowledge-graph context pack for a task")
    p_ctx.add_argument("--task", default=None, help="Task id (default: selected)")
    p_ctx.add_argument("--hops", type=int, default=1)

    sub.add_parser("status", help="Pipeline / artifact health check")

    p_list = sub.add_parser("list", help="List backlog tasks")
    p_list.add_argument("--status", default=None, help="Filter e.g. ready,planned")

    p_val = sub.add_parser("validate", help="Guardrails + ruff/pyright/pytest")
    p_val.add_argument("--base-ref", default=None)
    p_val.add_argument("--skip-quality", action="store_true")

    p_sync = sub.add_parser("sync-issues", help="Create GitHub issues from backlog")
    p_sync.add_argument("--apply", action="store_true")

    p_run = sub.add_parser("run", help="analyze → plan → select (no auto-edit)")
    p_run.add_argument("--task", help="Force task id")
    p_run.add_argument("--base-ref", default=None)
    p_run.add_argument("--skip-quality", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    # Ensure cwd-friendly root detection
    find_repo_root()
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "analyze": cmd_analyze,
        "plan": cmd_plan,
        "select": cmd_select,
        "context": cmd_context,
        "status": cmd_status,
        "list": cmd_list,
        "validate": cmd_validate,
        "sync-issues": cmd_sync,
        "run": cmd_run,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
