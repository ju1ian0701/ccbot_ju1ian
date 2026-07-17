#!/usr/bin/env python
"""Fix layer node IDs to match actual graph node IDs."""
import json

root = r"D:\CCbot_tmux\ccbot\ccbot_ju1ian"
gpath = f"{root}\\.understand-anything\\intermediate\\assembled-graph.json"

with open(gpath, encoding="utf-8") as f:
    graph = json.load(f)

# Build a map from filePath to node ID
path_to_id = {}
for n in graph["nodes"]:
    if "filePath" in n and n["filePath"]:
        path_to_id[n["filePath"]] = n["id"]

# Also map from id-basename patterns
id_map = {}
for n in graph["nodes"]:
    id_map[n["id"]] = n["id"]
    # Also map shortened forms
    if ":" in n["id"]:
        short = n["id"].split(":")[-1]
        id_map[short] = n["id"]
    # Map by filePath basename
    if "filePath" in n and n["filePath"]:
        import os
        base = os.path.basename(n["filePath"])
        stem = os.path.splitext(base)[0]
        for key in [base, stem, base.lower(), stem.lower()]:
            id_map[key] = n["id"]
            id_map[key.replace(".", "_").replace("-", "_").lower()] = n["id"]

# Normalize layer nodeIds
file_types = {"file", "config", "document", "pipeline", "script", "service", "table", "schema", "resource", "endpoint"}

all_node_ids = {n["id"] for n in graph["nodes"]}

for layer in graph["layers"]:
    resolved = []
    for nid in layer.get("nodeIds", []):
        if nid in all_node_ids:
            resolved.append(nid)
            continue
        # Try by filePath
        if nid in path_to_id:
            resolved.append(path_to_id[nid])
            continue
        # Try short name matching
        nid_short = nid.split(":")[-1] if ":" in nid else nid
        for actual_id in all_node_ids:
            if nid_short in actual_id or actual_id.endswith(nid_short):
                resolved.append(actual_id)
                break
        else:
            # Keep original anyway
            resolved.append(nid)
    layer["nodeIds"] = resolved

# Now assign unassigned file-level nodes to layers
assigned = set()
for layer in graph["layers"]:
    for nid in layer.get("nodeIds", []):
        assigned.add(nid)

# Create catch-all "other" layer for unassigned
unassigned = [n["id"] for n in graph["nodes"] if n.get("type") in file_types and n["id"] not in assigned]
if unassigned:
    graph["layers"].append({
        "id": "layer:other",
        "name": "Other Files",
        "description": "Files not assigned to a specific architectural layer",
        "nodeIds": unassigned
    })

with open(gpath, "w", encoding="utf-8") as f:
    json.dump(graph, f, indent=2, ensure_ascii=False)

print(f"Reconciled layers. Assigned {len(unassigned)} previously unassigned nodes.")
