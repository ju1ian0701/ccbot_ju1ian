#!/usr/bin/env python
"""Auto-fix common graph validation issues."""
import json

root = r"D:\CCbot_tmux\ccbot\ccbot_ju1ian"
path = f"{root}\\.understand-anything\\intermediate\\assembled-graph.json"

with open(path, encoding="utf-8") as f:
    graph = json.load(f)

fixes = 0
for i, n in enumerate(graph["nodes"]):
    if not n.get("name"):
        # Derive name from id
        parts = n["id"].split(":")
        if len(parts) >= 2:
            n["name"] = parts[-1].rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("_", " ").title()
        else:
            n["name"] = parts[0]
        fixes += 1
    if not n.get("summary"):
        n["summary"] = f"{n.get('type', 'node')} node: {n.get('name', n['id'])}"
        fixes += 1
    if not n.get("tags") or len(n["tags"]) == 0:
        n["tags"] = [n.get("type", "untagged")]
        fixes += 1
    if not n.get("complexity"):
        n["complexity"] = "moderate"
        fixes += 1

# Drop dangling edges
valid_ids = {n["id"] for n in graph["nodes"]}
clean_edges = []
dropped = 0
for e in graph["edges"]:
    src = e.get("source", "")
    tgt = e.get("target", "")
    if src in valid_ids and tgt in valid_ids:
        clean_edges.append(e)
    else:
        dropped += 1

graph["edges"] = clean_edges

with open(path, "w", encoding="utf-8") as f:
    json.dump(graph, f, indent=2, ensure_ascii=False)

print(f"Fixed {fixes} node fields, dropped {dropped} dangling edges")
