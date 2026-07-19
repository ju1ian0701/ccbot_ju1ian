"""Rank backlog tasks using priority weights + graph path scores."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import find_repo_root, load_config, load_json, out_dir, write_json, write_text


TERMINAL_STATUS = frozenset({"done", "cancelled", "wontfix"})
ACTIVE_STATUS = frozenset({"ready", "planned", "in_progress", "blocked"})


def _task_file_paths(task: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for f in task.get("files") or []:
        p = str(f).replace("\\", "/")
        if p.endswith("/"):
            paths.append(p.rstrip("/"))
        else:
            paths.append(p)
    return paths


def _deps_satisfied(task: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    for dep in task.get("depends_on") or []:
        other = by_id.get(dep)
        if not other:
            return False
        if other.get("status") != "done":
            return False
    return True


def score_task(
    task: dict[str, Any],
    config: dict[str, Any],
    path_scores: dict[str, int],
    by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    weights = config["prioritization"]["priority_weights"]
    priority = str(task.get("priority") or "low").lower()
    base = int(weights.get(priority, weights.get("low", 15)))
    status = str(task.get("status") or "planned").lower()

    graph_bonus = 0
    matched_paths: list[str] = []
    for fpath in _task_file_paths(task):
        # exact + prefix match for directories
        if fpath in path_scores:
            graph_bonus += path_scores[fpath]
            matched_paths.append(fpath)
        else:
            for p, sc in path_scores.items():
                if p.startswith(fpath.rstrip("/") + "/") or fpath.startswith(p):
                    graph_bonus += sc
                    matched_paths.append(p)
                    break

    # Related graph nodes
    for nid in task.get("related_nodes") or []:
        # node id file:src/... → path
        if isinstance(nid, str) and nid.startswith("file:"):
            p = nid[len("file:") :]
            if p in path_scores:
                graph_bonus += path_scores[p]
                matched_paths.append(p)

    # Cap graph bonus to avoid drowning priority
    graph_bonus = min(graph_bonus, 40)

    status_bonus = 0
    if status == "ready":
        status_bonus = 10
    elif status == "in_progress":
        status_bonus = 5
    elif status == "planned":
        status_bonus = 0
    elif status == "blocked":
        status_bonus = -50

    deps_ok = _deps_satisfied(task, by_id)
    dep_penalty = 0 if deps_ok else -100

    risk = str(task.get("estimated_risk") or "medium").lower()
    risk_adj = {"low": 3, "medium": 0, "high": -5}.get(risk, 0)

    total = base + graph_bonus + status_bonus + dep_penalty + risk_adj
    selectable = (
        status in {"ready", "planned", "in_progress"}
        and deps_ok
        and status != "blocked"
        and status not in TERMINAL_STATUS
    )

    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "priority": priority,
        "status": status,
        "score": total,
        "components": {
            "base_priority": base,
            "graph_bonus": graph_bonus,
            "status_bonus": status_bonus,
            "dep_penalty": dep_penalty,
            "risk_adj": risk_adj,
        },
        "deps_satisfied": deps_ok,
        "depends_on": list(task.get("depends_on") or []),
        "matched_hotspot_paths": sorted(set(matched_paths)),
        "selectable": selectable,
        "estimated_risk": risk,
        "categories": list(task.get("categories") or []),
        "files": list(task.get("files") or []),
    }


def build_plan(
    backlog: dict[str, Any],
    analysis: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    tasks = list(backlog.get("tasks") or [])
    by_id = {t["id"]: t for t in tasks if t.get("id")}
    path_scores = (analysis or {}).get("path_scores") or {}

    ranked = [score_task(t, config, path_scores, by_id) for t in tasks]
    ranked.sort(key=lambda r: (-r["score"], r.get("id") or ""))

    selectable = [r for r in ranked if r["selectable"]]
    selected = selectable[0] if selectable else None

    selected_full = None
    if selected:
        selected_full = dict(by_id[selected["id"]])
        selected_full["_ranking"] = selected

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "graph_commit": ((analysis or {}).get("project") or {}).get("gitCommitHash"),
        "ranked_tasks": ranked,
        "selected_task_id": selected["id"] if selected else None,
        "selected_task": selected_full,
        "blocked_or_waiting": [
            r
            for r in ranked
            if not r["selectable"] and r.get("status") not in TERMINAL_STATUS
        ],
        "recommendations": (analysis or {}).get("recommendations") or [],
    }


def render_plan_md(plan: dict[str, Any]) -> str:
    lines = [
        "# Agentic implementation plan",
        "",
        f"- Generated: `{plan.get('generated_at')}`",
        f"- Graph commit: `{plan.get('graph_commit')}`",
        f"- Selected: **{plan.get('selected_task_id') or 'none'}**",
        "",
        "## Ranking",
        "",
        "| Rank | ID | Score | Priority | Status | Deps OK | Title |",
        "|-----:|----|------:|----------|--------|---------|-------|",
    ]
    for i, r in enumerate(plan.get("ranked_tasks") or [], 1):
        lines.append(
            f"| {i} | `{r.get('id')}` | {r.get('score')} | {r.get('priority')} | "
            f"{r.get('status')} | {r.get('deps_satisfied')} | {r.get('title')} |"
        )
    sel = plan.get("selected_task")
    if sel:
        lines += [
            "",
            f"## Selected task `{sel.get('id')}`",
            "",
            f"**{sel.get('title')}**",
            "",
            f"- Priority: {sel.get('priority')}",
            f"- Risk: {sel.get('estimated_risk')}",
            f"- Files: {', '.join(f'`{f}`' for f in (sel.get('files') or []))}",
            "",
            "### Problem",
            "",
            str(sel.get("problem") or ""),
            "",
            "### Solution",
            "",
            str(sel.get("solution") or ""),
            "",
            "### Acceptance criteria",
            "",
        ]
        for c in sel.get("acceptance_criteria") or []:
            lines.append(f"- [ ] {c}")
    lines.append("")
    return "\n".join(lines)


def run(
    repo_root: Path | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    root = repo_root or find_repo_root()
    config = load_config(root)
    backlog = load_json(root / config["backlog"]["path"])
    out = out_dir(root, config)
    analysis_path = out / config["outputs"]["analysis_report"]
    analysis = load_json(analysis_path) if analysis_path.is_file() else None

    plan = build_plan(backlog, analysis, config)

    if task_id:
        by_id = {t["id"]: t for t in backlog.get("tasks") or []}
        if task_id not in by_id:
            raise KeyError(f"Unknown task id: {task_id}")
        # Re-score forced task; mark selected even if not top
        path_scores = (analysis or {}).get("path_scores") or {}
        ranking = score_task(by_id[task_id], config, path_scores, by_id)
        full = dict(by_id[task_id])
        full["_ranking"] = ranking
        plan["selected_task_id"] = task_id
        plan["selected_task"] = full
        plan["forced_task_id"] = task_id

    write_json(out / config["outputs"]["plan"], plan)
    write_text(out / config["outputs"]["plan_markdown"], render_plan_md(plan))

    selected = plan.get("selected_task")
    if selected:
        write_json(out / config["outputs"]["selected_task"], selected)
    return plan


if __name__ == "__main__":
    p = run()
    print(f"Selected: {p.get('selected_task_id')}")
