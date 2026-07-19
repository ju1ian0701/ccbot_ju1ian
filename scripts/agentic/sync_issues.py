"""Create or update GitHub issues from backlog tasks (via gh CLI)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from paths import find_repo_root, load_config, load_json


def _gh(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _issue_body(task: dict[str, Any]) -> str:
    ac = "\n".join(f"- [ ] {c}" for c in (task.get("acceptance_criteria") or []))
    files = ", ".join(f"`{f}`" for f in (task.get("files") or []))
    return f"""## Agentic backlog task `{task.get("id")}`

**Priority:** {task.get("priority")}  
**Status:** {task.get("status")}  
**Risk:** {task.get("estimated_risk")}  
**Categories:** {", ".join(task.get("categories") or [])}

### Problem

{task.get("problem")}

### Proposed solution

{task.get("solution")}

### Files

{files}

### Acceptance criteria

{ac}

### Metadata

```json
{json.dumps({"id": task.get("id"), "depends_on": task.get("depends_on") or [], "labels": task.get("labels") or []}, indent=2)}
```

<!-- agentic-task-id:{task.get("id")} -->
"""


def list_existing_task_issues(root: Path) -> dict[str, int]:
    """Map task id → issue number for open issues with agentic marker."""
    proc = _gh(
        [
            "issue",
            "list",
            "--state",
            "open",
            "--label",
            "agentic",
            "--limit",
            "100",
            "--json",
            "number,body,title",
        ],
        root,
    )
    if proc.returncode != 0:
        return {}
    try:
        items = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    found: dict[str, int] = {}
    for it in items:
        body = it.get("body") or ""
        for line in body.splitlines():
            if "agentic-task-id:" in line:
                tid = line.split("agentic-task-id:", 1)[1].strip().rstrip(" -->")
                found[tid] = int(it["number"])
    return found


def sync(
    repo_root: Path | None = None,
    dry_run: bool = True,
    statuses: set[str] | None = None,
) -> dict[str, Any]:
    root = repo_root or find_repo_root()
    config = load_config(root)
    backlog = load_json(root / config["backlog"]["path"])
    want = statuses or {"ready", "planned", "in_progress"}
    existing = {} if dry_run else list_existing_task_issues(root)

    actions: list[dict[str, Any]] = []
    for task in backlog.get("tasks") or []:
        if task.get("status") not in want:
            continue
        tid = task.get("id")
        title = f"[agentic] {tid}: {task.get('title')}"
        body = _issue_body(task)
        labels = list(task.get("labels") or [])
        if "agentic" not in labels:
            labels.append("agentic")

        if tid in existing:
            actions.append({"action": "exists", "task_id": tid, "issue": existing[tid]})
            continue

        entry: dict[str, Any] = {
            "action": "create",
            "task_id": tid,
            "title": title,
            "labels": labels,
            "dry_run": dry_run,
        }
        if dry_run:
            actions.append(entry)
            continue

        cmd = [
            "issue",
            "create",
            "--title",
            title,
            "--body",
            body,
        ]
        for lab in labels:
            cmd.extend(["--label", lab])
        proc = _gh(cmd, root)
        entry["returncode"] = proc.returncode
        entry["stdout"] = (proc.stdout or "").strip()
        entry["stderr"] = (proc.stderr or "").strip()
        actions.append(entry)

    return {"dry_run": dry_run, "actions": actions, "count": len(actions)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Sync backlog tasks to GitHub issues")
    p.add_argument("--apply", action="store_true", help="Actually create issues (needs gh)")
    p.add_argument(
        "--statuses",
        default="ready,planned,in_progress",
        help="Comma-separated statuses to sync",
    )
    args = p.parse_args(argv)
    statuses = {s.strip() for s in args.statuses.split(",") if s.strip()}
    result = sync(dry_run=not args.apply, statuses=statuses)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.apply and any(
        a.get("action") == "create" and a.get("returncode") not in (0, None)
        for a in result["actions"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
