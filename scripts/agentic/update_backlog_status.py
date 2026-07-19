"""Update task status in .agentic/backlog/tasks.json (local helper)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from paths import find_repo_root, load_config, load_json, write_json  # noqa: E402

VALID = frozenset({"ready", "planned", "in_progress", "done", "blocked", "cancelled"})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Update agentic backlog task status")
    p.add_argument("task_id", help="e.g. REF-001")
    p.add_argument("status", choices=sorted(VALID))
    p.add_argument("--note", default="", help="Optional note stored on task")
    args = p.parse_args(argv)

    root = find_repo_root()
    config = load_config(root)
    path = root / config["backlog"]["path"]
    data = load_json(path)
    found = False
    for task in data.get("tasks") or []:
        if task.get("id") == args.task_id:
            task["status"] = args.status
            task["status_updated_at"] = datetime.now(timezone.utc).isoformat()
            if args.note:
                task["status_note"] = args.note
            found = True
            break
    if not found:
        print(f"Unknown task: {args.task_id}", file=sys.stderr)
        return 1
    data["updated_at"] = datetime.now(timezone.utc).date().isoformat()
    write_json(path, data)
    print(f"updated {args.task_id} -> {args.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
