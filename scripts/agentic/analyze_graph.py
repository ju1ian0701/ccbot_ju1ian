"""Analyze knowledge-graph.json and produce hotspot / debt signals."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import find_repo_root, load_config, load_json, out_dir, write_json, write_text


FILE_LEVEL_TYPES = frozenset(
    {
        "file",
        "config",
        "document",
        "service",
        "pipeline",
        "table",
        "schema",
        "resource",
        "endpoint",
        "module",
    }
)


def _edge_type(edge: dict[str, Any]) -> str:
    return edge.get("type") or edge.get("label") or "related"


def _normalize_path(path: str | None) -> str | None:
    if not path:
        return None
    return path.replace("\\", "/").lstrip("./")


def analyze_graph(graph: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = graph.get("nodes") or []
    edges: list[dict[str, Any]] = graph.get("edges") or []
    layers: list[dict[str, Any]] = graph.get("layers") or []
    tour: list[dict[str, Any]] = graph.get("tour") or []

    node_by_id = {n["id"]: n for n in nodes if n.get("id")}
    fan_in: dict[str, int] = defaultdict(int)
    fan_out: dict[str, int] = defaultdict(int)
    edge_types: Counter[str] = Counter()
    tested_targets: set[str] = set()

    for e in edges:
        et = _edge_type(e)
        edge_types[et] += 1
        src, tgt = e.get("source"), e.get("target")
        if src:
            fan_out[src] += 1
        if tgt:
            fan_in[tgt] += 1
        if et == "tested_by":
            # production → test in merged graphs; either endpoint may be production
            if src:
                tested_targets.add(src)
            if tgt:
                tested_targets.add(tgt)

    threshold = int(config["prioritization"]["graph_signals"]["hotspot_threshold_edges"])
    signals = config["prioritization"]["graph_signals"]

    file_nodes = [
        n
        for n in nodes
        if n.get("type") in FILE_LEVEL_TYPES
        or (n.get("filePath") and str(n.get("type", "")).lower() in {"file", "filenameNode".lower()})
    ]
    # Prefer explicit type==file for hotspot scoring of product code
    code_files = [n for n in nodes if n.get("type") == "file"]

    hotspots: list[dict[str, Any]] = []
    untested: list[dict[str, Any]] = []
    complex_files: list[dict[str, Any]] = []

    for n in code_files:
        nid = n["id"]
        fpath = _normalize_path(n.get("filePath") or n.get("name"))
        degree = fan_in[nid] + fan_out[nid]
        tags = {t.lower() for t in (n.get("tags") or [])}
        is_tested = "tested" in tags or nid in tested_targets
        complexity = (n.get("complexity") or "").lower()
        score = 0
        reasons: list[str] = []

        if complexity in {"complex", "high"}:
            score += int(signals["complex_file_bonus"])
            reasons.append(f"complexity={complexity}")
            complex_files.append({"id": nid, "path": fpath, "complexity": complexity})

        if not is_tested and fpath and fpath.startswith("src/"):
            score += int(signals["untested_file_bonus"])
            reasons.append("no_tested_tag_or_edge")
            untested.append({"id": nid, "path": fpath})

        if fan_in[nid] >= threshold // 2:
            score += int(signals["high_fan_in_bonus"])
            reasons.append(f"fan_in={fan_in[nid]}")
        if fan_out[nid] >= threshold // 2:
            score += int(signals["high_fan_out_bonus"])
            reasons.append(f"fan_out={fan_out[nid]}")
        if degree >= threshold:
            reasons.append(f"degree={degree}")

        if score > 0 or degree >= threshold:
            hotspots.append(
                {
                    "id": nid,
                    "path": fpath,
                    "score": score,
                    "fan_in": fan_in[nid],
                    "fan_out": fan_out[nid],
                    "complexity": complexity or None,
                    "tested": is_tested,
                    "reasons": reasons,
                    "summary": (n.get("summary") or "")[:240],
                }
            )

    hotspots.sort(key=lambda h: (-h["score"], -(h["fan_in"] + h["fan_out"]), h.get("path") or ""))

    layer_summary = [
        {
            "id": layer.get("id"),
            "name": layer.get("name"),
            "node_count": len(layer.get("nodeIds") or []),
            "description": layer.get("description"),
        }
        for layer in layers
    ]

    node_types = Counter(n.get("type") or "unknown" for n in nodes)
    orphans = [
        {"id": n["id"], "type": n.get("type"), "name": n.get("name")}
        for n in nodes
        if n.get("id") and fan_in[n["id"]] == 0 and fan_out[n["id"]] == 0
    ]

    # Map file path → hotspot score for backlog boosting
    path_scores: dict[str, int] = {}
    for h in hotspots:
        if h.get("path"):
            path_scores[h["path"]] = max(path_scores.get(h["path"], 0), int(h["score"]))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": graph.get("project") or {},
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "layers": len(layers),
            "tour_steps": len(tour),
            "code_files": len(code_files),
            "file_level_nodes": len(file_nodes),
            "node_types": dict(node_types),
            "edge_types": dict(edge_types),
            "orphan_nodes": len(orphans),
        },
        "layers": layer_summary,
        "hotspots": hotspots[:30],
        "complex_files": complex_files,
        "untested_src_files": untested,
        "orphan_sample": orphans[:20],
        "path_scores": path_scores,
        "recommendations": _recommendations(hotspots, untested, complex_files),
    }


def _recommendations(
    hotspots: list[dict[str, Any]],
    untested: list[dict[str, Any]],
    complex_files: list[dict[str, Any]],
) -> list[str]:
    recs: list[str] = []
    top = hotspots[:5]
    if top:
        paths = ", ".join(h.get("path") or h["id"] for h in top)
        recs.append(f"Prioritize refactors touching hotspots: {paths}")
    bot = next((h for h in hotspots if (h.get("path") or "").endswith("bot.py")), None)
    if bot:
        recs.append(
            "bot.py remains a structural hotspot — prefer extract-helpers (REF-001) before full split (REF-002)."
        )
    if len(untested) >= 3:
        recs.append(
            f"{len(untested)} src file nodes lack tested signals — schedule REF-009 coverage for top hotspots."
        )
    if len(complex_files) >= 2:
        recs.append(
            "Multiple complex files detected — avoid multi-file god refactors in a single agent PR."
        )
    if not recs:
        recs.append("Graph looks balanced; proceed with highest-priority ready backlog tasks.")
    return recs


def render_markdown(report: dict[str, Any]) -> str:
    project = report.get("project") or {}
    stats = report.get("stats") or {}
    lines = [
        f"# Knowledge graph analysis — {project.get('name', 'project')}",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Graph commit: `{project.get('gitCommitHash', 'unknown')}`",
        f"- Nodes: **{stats.get('nodes')}** · Edges: **{stats.get('edges')}** · Layers: **{stats.get('layers')}**",
        "",
        "## Layers",
        "",
    ]
    for layer in report.get("layers") or []:
        lines.append(
            f"- **{layer.get('name')}** (`{layer.get('id')}`): {layer.get('node_count')} nodes"
        )
    lines += ["", "## Top hotspots", ""]
    lines.append("| Path | Score | Fan-in | Fan-out | Complexity | Tested | Reasons |")
    lines.append("|------|------:|-------:|--------:|------------|--------|---------|")
    for h in (report.get("hotspots") or [])[:15]:
        lines.append(
            f"| `{h.get('path')}` | {h.get('score')} | {h.get('fan_in')} | {h.get('fan_out')} | "
            f"{h.get('complexity') or '-'} | {h.get('tested')} | {', '.join(h.get('reasons') or [])} |"
        )
    lines += ["", "## Recommendations", ""]
    for r in report.get("recommendations") or []:
        lines.append(f"- {r}")
    lines += ["", "## Untested src files (sample)", ""]
    for u in (report.get("untested_src_files") or [])[:20]:
        lines.append(f"- `{u.get('path')}`")
    lines.append("")
    return "\n".join(lines)


def run(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or find_repo_root()
    config = load_config(root)
    graph_path = root / config["knowledge_graph"]["path"]
    if not graph_path.is_file():
        raise FileNotFoundError(
            f"Knowledge graph not found: {graph_path}. Run /understand first."
        )
    graph = load_json(graph_path)
    report = analyze_graph(graph, config)

    meta_path = root / config["knowledge_graph"]["meta_path"]
    if meta_path.is_file():
        report["meta"] = load_json(meta_path)

    out = out_dir(root, config)
    write_json(out / config["outputs"]["analysis_report"], report)
    write_text(out / config["outputs"]["analysis_markdown"], render_markdown(report))
    return report


if __name__ == "__main__":
    result = run()
    print(f"Hotspots: {len(result.get('hotspots') or [])}")
    print(f"Wrote analysis to .agentic/out/")
