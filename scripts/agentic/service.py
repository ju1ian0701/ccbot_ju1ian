"""Shared service layer for CLI and MCP server (single source of truth)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analyze_graph import run as run_analyze
from build_context_pack import run as run_context_pack
from paths import find_repo_root, load_config, load_json, out_dir, write_json
from prioritize import run as run_plan
from render_prompt import run as run_render
from validate_changes import run as run_validate


def repo_root(cwd: str | None = None) -> Path:
    if cwd:
        return find_repo_root(Path(cwd))
    return find_repo_root()


def list_tasks(
    status: str | None = None,
    *,
    root: Path | None = None,
    include_ranking: bool = True,
) -> dict[str, Any]:
    root = root or repo_root()
    config = load_config(root)
    backlog = load_json(root / config["backlog"]["path"])
    tasks = list(backlog.get("tasks") or [])
    if status:
        wanted = {s.strip() for s in status.split(",") if s.strip()}
        tasks = [t for t in tasks if t.get("status") in wanted]

    ranking_by_id: dict[str, Any] = {}
    if include_ranking:
        out = out_dir(root, config)
        plan_path = out / config["outputs"]["plan"]
        if plan_path.is_file():
            plan = load_json(plan_path)
            for r in plan.get("ranked_tasks") or []:
                if r.get("id"):
                    ranking_by_id[r["id"]] = r
        else:
            # lightweight rank without requiring prior analyze file
            try:
                plan = run_plan(repo_root=root)
                for r in plan.get("ranked_tasks") or []:
                    if r.get("id"):
                        ranking_by_id[r["id"]] = r
            except Exception as exc:  # noqa: BLE001 — surface soft failure
                ranking_by_id = {"_error": str(exc)}

    items = []
    for t in tasks:
        item = {
            "id": t.get("id"),
            "title": t.get("title"),
            "priority": t.get("priority"),
            "status": t.get("status"),
            "estimated_risk": t.get("estimated_risk"),
            "depends_on": t.get("depends_on") or [],
            "files": t.get("files") or [],
            "categories": t.get("categories") or [],
        }
        rank = ranking_by_id.get(t.get("id") or "")
        if isinstance(rank, dict):
            item["score"] = rank.get("score")
            item["selectable"] = rank.get("selectable")
            item["deps_satisfied"] = rank.get("deps_satisfied")
        items.append(item)

    items.sort(key=lambda x: (-(x.get("score") or 0), x.get("id") or ""))
    return {
        "count": len(items),
        "tasks": items,
        "selected_task_id": (
            load_json(out_dir(root, config) / config["outputs"]["selected_task"]).get("id")
            if (out_dir(root, config) / config["outputs"]["selected_task"]).is_file()
            else None
        ),
    }


def get_task(task_id: str, *, root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    config = load_config(root)
    backlog = load_json(root / config["backlog"]["path"])
    for t in backlog.get("tasks") or []:
        if t.get("id") == task_id:
            return {"task": t}
    raise KeyError(f"Unknown task: {task_id}")


def select_next_task(
    task_id: str | None = None,
    *,
    root: Path | None = None,
    skip_analyze: bool = False,
) -> dict[str, Any]:
    root = root or repo_root()
    if not skip_analyze:
        try:
            run_analyze(repo_root=root)
        except FileNotFoundError as exc:
            # allow plan on ranking-only if graph missing
            analysis_error = str(exc)
        else:
            analysis_error = None
    else:
        analysis_error = None

    plan = run_plan(repo_root=root, task_id=task_id)
    if not plan.get("selected_task_id"):
        return {
            "ok": False,
            "error": "no selectable task",
            "plan_summary": {
                "ranked": [
                    {"id": r.get("id"), "score": r.get("score"), "selectable": r.get("selectable")}
                    for r in (plan.get("ranked_tasks") or [])[:10]
                ]
            },
            "analysis_error": analysis_error,
        }
    paths = run_render(repo_root=root)
    pack = None
    try:
        pack = run_context_pack(task_id=plan["selected_task_id"], repo_root=root)
    except Exception as exc:  # noqa: BLE001
        pack = {"error": str(exc)}

    return {
        "ok": True,
        "selected_task_id": plan.get("selected_task_id"),
        "selected_task": plan.get("selected_task"),
        "artifacts": paths,
        "context_pack_stats": (pack or {}).get("stats") if isinstance(pack, dict) else None,
        "analysis_error": analysis_error,
    }


def get_analysis(*, root: Path | None = None, refresh: bool = False) -> dict[str, Any]:
    root = root or repo_root()
    config = load_config(root)
    out = out_dir(root, config)
    path = out / config["outputs"]["analysis_report"]
    if refresh or not path.is_file():
        report = run_analyze(repo_root=root)
    else:
        report = load_json(path)
    # Truncate for MCP payload limits
    slim = {
        "generated_at": report.get("generated_at"),
        "project": report.get("project"),
        "stats": report.get("stats"),
        "layers": report.get("layers"),
        "hotspots": (report.get("hotspots") or [])[:15],
        "recommendations": report.get("recommendations"),
        "untested_src_files": (report.get("untested_src_files") or [])[:20],
        "path_scores_top": dict(
            sorted((report.get("path_scores") or {}).items(), key=lambda kv: -kv[1])[:20]
        ),
    }
    return slim


def get_context_pack(
    task_id: str | None = None,
    *,
    hops: int = 1,
    root: Path | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    root = root or repo_root()
    config = load_config(root)
    out = out_dir(root, config)
    path = out / "context-pack.json"
    if refresh or not path.is_file():
        return run_context_pack(task_id=task_id, repo_root=root, hops=hops)
    pack = load_json(path)
    if task_id and pack.get("task_id") != task_id:
        return run_context_pack(task_id=task_id, repo_root=root, hops=hops)
    return pack


def get_implement_prompt(*, root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    config = load_config(root)
    out = out_dir(root, config)
    prompt_path = out / config["outputs"]["implement_prompt"]
    selected_path = out / config["outputs"]["selected_task"]
    if not prompt_path.is_file():
        raise FileNotFoundError("implement-prompt.md missing; call select_next_task first")
    return {
        "selected_task_id": (
            load_json(selected_path).get("id") if selected_path.is_file() else None
        ),
        "implement_prompt_path": str(prompt_path),
        "implement_prompt": prompt_path.read_text(encoding="utf-8"),
        "pr_body_path": str(out / "pr-body.md"),
        "pr_body": (out / "pr-body.md").read_text(encoding="utf-8")
        if (out / "pr-body.md").is_file()
        else None,
    }


def mark_task_status(
    task_id: str,
    status: str,
    note: str = "",
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    valid = {"ready", "planned", "in_progress", "done", "blocked", "cancelled"}
    if status not in valid:
        raise ValueError(f"status must be one of {sorted(valid)}")
    root = root or repo_root()
    config = load_config(root)
    path = root / config["backlog"]["path"]
    data = load_json(path)
    found = False
    for task in data.get("tasks") or []:
        if task.get("id") == task_id:
            task["status"] = status
            task["status_updated_at"] = datetime.now(timezone.utc).isoformat()
            if note:
                task["status_note"] = note
            found = True
            break
    if not found:
        raise KeyError(f"Unknown task: {task_id}")
    data["updated_at"] = datetime.now(timezone.utc).date().isoformat()
    write_json(path, data)
    return {"ok": True, "task_id": task_id, "status": status, "note": note or None}


def validate(
    base_ref: str | None = None,
    skip_quality: bool = True,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or repo_root()
    report = run_validate(repo_root=root, base_ref=base_ref, skip_quality=skip_quality)
    # slim quality stdout for MCP
    quality = []
    for q in report.get("quality") or []:
        quality.append(
            {
                "cmd": q.get("cmd"),
                "ok": q.get("ok"),
                "returncode": q.get("returncode"),
                "stderr_tail": (q.get("stderr_tail") or "")[-500:],
            }
        )
    return {
        "ok": report.get("ok"),
        "guardrails": report.get("guardrails"),
        "quality": quality,
        "generated_at": report.get("generated_at"),
    }


def pipeline_status(*, root: Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    config = load_config(root)
    out = out_dir(root, config)
    graph_path = root / config["knowledge_graph"]["path"]
    backlog_path = root / config["backlog"]["path"]

    def _exists(name: str) -> bool:
        return (out / name).is_file()

    artifacts = {
        "analysis_report": _exists(config["outputs"]["analysis_report"]),
        "plan": _exists(config["outputs"]["plan"]),
        "selected_task": _exists(config["outputs"]["selected_task"]),
        "implement_prompt": _exists(config["outputs"]["implement_prompt"]),
        "context_pack": (out / "context-pack.json").is_file(),
        "validation": _exists(config["outputs"]["validation"]),
    }
    selected = None
    if artifacts["selected_task"]:
        selected = load_json(out / config["outputs"]["selected_task"]).get("id")

    meta = None
    meta_path = root / config["knowledge_graph"]["meta_path"]
    if meta_path.is_file():
        meta = load_json(meta_path)

    return {
        "repo_root": str(root),
        "graph_present": graph_path.is_file(),
        "backlog_present": backlog_path.is_file(),
        "graph_meta": meta,
        "artifacts": artifacts,
        "selected_task_id": selected,
        "out_dir": str(out),
    }


def dumps_result(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)
