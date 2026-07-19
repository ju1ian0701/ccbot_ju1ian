import json

root = r"D:\CCbot_tmux\ccbot\ccbot_ju1ian"
gpath = f"{root}\\.understand-anything\\intermediate\\assembled-graph.json"

with open(gpath, encoding="utf-8") as f:
    graph = json.load(f)

all_ids = {n["id"] for n in graph["nodes"]}

# Remove duplicate assignments: remove from "other" if already assigned elsewhere
assigned = set()
for layer in graph["layers"]:
    if layer["id"] == "layer:other":
        continue
    for nid in layer.get("nodeIds", []):
        assigned.add(nid)

# Filter "other" layer to only have unassigned nodes
for layer in graph["layers"]:
    if layer["id"] == "layer:other":
        layer["nodeIds"] = [nid for nid in layer.get("nodeIds", []) if nid not in assigned]

# Remove layers with no nodeIds
graph["layers"] = [layer for layer in graph["layers"] if layer.get("nodeIds")]

with open(gpath, "w", encoding="utf-8") as f:
    json.dump(graph, f, indent=2, ensure_ascii=False)

print("Removed duplicate layer assignments")
