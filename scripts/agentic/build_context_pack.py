"""Build a task-scoped subgraph slice from knowledge-graph.json."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from paths import find_repo_root, load_config, load_json, out_dir, write_json


def _edge_type(edge: dict[str, Any]) -> str:
    return edge.get("type") or edge.get("label") or "related"


def _node_path(node: dict[str, Any]) -> str | None:
    fp = node.get("filePath") or node.get("path")
    if fp:
        return str(fp).replace("\\", "/")
    nid = node.get("id") or ""
    if isinstance(nid, str) and nid.startswith("file:"):
        return nid[len("file:") :]
    return None


def _seed_ids(task: dict[str, Any], node_by_id: dict[str, dict[str, Any]]) -> set[str]:
    seeds: set[str] = set()
    for nid in task.get("related_nodes") or []:
        if nid in node_by_id:
            seeds.add(nid)
    for fpath in task.get("files") or []:
        p = str(fpath).replace("\\", "/").rstrip("/")
        # exact file node
        candidate = f"file:{p}"
        if candidate in node_by_id:
            seeds.add(candidate)
        # directory prefix match
        for nid, node in node_by_id.items():
            np = _node_path(node)
            if not np:
                continue
            if np == p or np.startswith(p + "/") or p.startswith(np):
                if node.get("type") in {
                    "file",
                    "config",
                    "module",
                    "document",
                    "pipeline",
                }:
                    seeds.add(nid)
    return seeds


def build_context_pack(
    graph: dict[str, Any],
    task: dict[str, Any],
    *,
    hops: int = 1,
    max_nodes: int = 80,
    max_edges: int = 120,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = graph.get("nodes") or []
    edges: list[dict[str, Any]] = graph.get("edges") or []
    node_by_id = {n["id"]: n for n in nodes if n.get("id")}

    seeds = _seed_ids(task, node_by_id)
    selected: set[str] = set(seeds)

    # adjacency undirected for hop expansion
    adj: dict[str, set[str]] = {nid: set() for nid in node_by_id}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in adj and t in adj:
            adj[s].add(t)
            adj[t].add(s)

    frontier = set(seeds)
    for _ in range(max(0, hops)):
        nxt: set[str] = set()
        for nid in frontier:
            for nb in adj.get(nid, ()):
                if nb not in selected:
                    nxt.add(nb)
        selected |= nxt
        frontier = nxt
        if len(selected) >= max_nodes:
            break

    # Prefer keeping seeds if we overflow
    if len(selected) > max_nodes:
        rest = [n for n in selected if n not in seeds]
        selected = set(seeds) | set(rest[: max(0, max_nodes - len(seeds))])

    pack_nodes: list[dict[str, Any]] = []
    for nid in sorted(selected):
        n = node_by_id.get(nid)
        if not n:
            continue
        pack_nodes.append(
            {
                "id": n.get("id"),
                "type": n.get("type"),
                "name": n.get("name"),
                "filePath": _node_path(n),
                "summary": (n.get("summary") or "")[:400],
                "tags": n.get("tags") or [],
                "complexity": n.get("complexity"),
            }
        )

    pack_edges: list[dict[str, Any]] = []
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in selected and t in selected:
            pack_edges.append(
                {
                    "source": s,
                    "target": t,
                    "type": _edge_type(e),
                }
            )
            if len(pack_edges) >= max_edges:
                break

    layers = []
    for layer in graph.get("layers") or []:
        ids = [i for i in (layer.get("nodeIds") or []) if i in selected]
        if ids:
            layers.append(
                {
                    "id": layer.get("id"),
                    "name": layer.get("name"),
                    "nodeIds": ids,
                }
            )

    return {
        "task_id": task.get("id"),
        "title": task.get("title"),
        "hops": hops,
        "seed_ids": sorted(seeds),
        "stats": {
            "nodes": len(pack_nodes),
            "edges": len(pack_edges),
            "seeds": len(seeds),
        },
        "project": {
            "name": (graph.get("project") or {}).get("name"),
            "gitCommitHash": (graph.get("project") or {}).get("gitCommitHash"),
        },
        "nodes": pack_nodes,
        "edges": pack_edges,
        "layers": layers,
        "task": {
            "id": task.get("id"),
            "priority": task.get("priority"),
            "status": task.get("status"),
            "files": task.get("files") or [],
            "problem": task.get("problem"),
            "solution": task.get("solution"),
            "acceptance_criteria": task.get("acceptance_criteria") or [],
            "depends_on": task.get("depends_on") or [],
            "estimated_risk": task.get("estimated_risk"),
            "atomic_steps": task.get("atomic_steps") or [],
        },
    }


def run(
    task_id: str | None = None,
    repo_root: Path | None = None,
    hops: int = 1,
) -> dict[str, Any]:
    root = repo_root or find_repo_root()
    config = load_config(root)
    graph = load_json(root / config["knowledge_graph"]["path"])
    backlog = load_json(root / config["backlog"]["path"])
    tasks = {t["id"]: t for t in backlog.get("tasks") or [] if t.get("id")}

    if not task_id:
        selected_path = out_dir(root, config) / config["outputs"]["selected_task"]
        if selected_path.is_file():
            task_id = load_json(selected_path).get("id")
    if not task_id:
        raise ValueError("task_id required (or run select first)")
    if task_id not in tasks:
        raise KeyError(f"Unknown task: {task_id}")

    pack = build_context_pack(graph, tasks[task_id], hops=hops)
    out = out_dir(root, config)
    write_json(out / "context-pack.json", pack)
    return pack


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser()
    p.add_argument("--task", default=None)
    p.add_argument("--hops", type=int, default=1)
    args = p.parse_args()
    result = run(task_id=args.task, hops=args.hops)
    print(json.dumps(result["stats"], indent=2))
