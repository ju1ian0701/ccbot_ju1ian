#!/usr/bin/env python
"""Final fix: reconcile all refs in layers and tour with actual node IDs."""
import json, os

root = r"D:\CCbot_tmux\ccbot\ccbot_ju1ian"
gpath = f"{root}\\.understand-anything\\intermediate\\assembled-graph.json"

with open(gpath, encoding="utf-8") as f:
    graph = json.load(f)

all_ids = {n["id"] for n in graph["nodes"]}

# Build comprehensive lookup: key -> [matching node IDs]
# Keys include: filePath, basename, stem, id-short, and common aliases
lookup = {}
for n in graph["nodes"]:
    nid = n["id"]
    # id itself
    lookup[nid] = lookup.get(nid, []) + [nid]
    # short form (after last / or :)
    short = nid.split(":")[-1] if ":" in nid else nid
    lookup[short] = lookup.get(short, []) + [nid]
    # by filePath
    if "filePath" in n and n["filePath"]:
        fp = n["filePath"]
        lookup[fp] = lookup.get(fp, []) + [nid]
        base = os.path.basename(fp)
        stem = os.path.splitext(base)[0]
        lookup[base] = lookup.get(base, []) + [nid]
        lookup[stem] = lookup.get(stem, []) + [nid]
        # Common aliases
        lookup[base.replace("-", "_")] = lookup.get(base.replace("-", "_"), []) + [nid]
        lookup[stem.replace("-", "_")] = lookup.get(stem.replace("-", "_"), []) + [nid]

def resolve(ref):
    if ref in all_ids:
        return ref
    # Try lookup
    matches = lookup.get(ref, [])
    if matches:
        return matches[0]
    # Try prefix-agnostic: ref without prefix
    if ":" in ref:
        no_prefix = ref.split(":", 1)[1]
        matches = lookup.get(no_prefix, [])
        if matches:
            return matches[0]
    # Try with file: prefix
    prefixed = f"file:{ref}"
    if prefixed in all_ids:
        return prefixed
    # Try with config: prefix
    prefixed = f"config:{ref}"
    if prefixed in all_ids:
        return prefixed
    # Try with document: prefix
    prefixed = f"document:{ref}"
    if prefixed in all_ids:
        return prefixed
    # Fuzzy: find any node where ref is in the id
    for aid in all_ids:
        if ref in aid or ref.replace("_", "-") in aid or ref.replace("-", "_") in aid:
            return aid
    return ref  # keep as-is

# Fix layers
for layer in graph["layers"]:
    new_ids = []
    for nid in layer.get("nodeIds", []):
        resolved = resolve(nid)
        new_ids.append(resolved)
    layer["nodeIds"] = new_ids

# Fix tour
for step in graph["tour"]:
    new_ids = []
    for nid in step.get("nodeIds", []):
        resolved = resolve(nid)
        new_ids.append(resolved)
    step["nodeIds"] = new_ids

with open(gpath, "w", encoding="utf-8") as f:
    json.dump(graph, f, indent=2, ensure_ascii=False)

print("Done reconciling layers and tour node references.")
