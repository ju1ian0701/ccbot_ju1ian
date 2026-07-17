#!/usr/bin/env python
"""Assemble the final KnowledgeGraph from parts."""
import json
from datetime import datetime, timezone

root = r"D:\CCbot_tmux\ccbot\ccbot_ju1ian"
intermediate = f"{root}\\.understand-anything\\intermediate"

# Read parts
with open(f"{intermediate}\\assembled-graph.json", encoding="utf-8") as f:
    assembled = json.load(f)
with open(f"{intermediate}\\layers.json", encoding="utf-8") as f:
    layers = json.load(f)
with open(f"{intermediate}\\tour.json", encoding="utf-8") as f:
    tour = json.load(f)

# Build final graph
graph = {
    "version": "1.0.0",
    "project": {
        "name": "ccbot",
        "languages": ["python", "shell", "markdown", "yaml", "toml"],
        "frameworks": ["python-telegram-bot", "tmux"],
        "description": "Telegram bot that bridges Telegram Forum topics to Claude Code sessions via tmux windows — control, monitor, and interact with AI coding sessions remotely.",
        "analyzedAt": datetime.now(timezone.utc).isoformat(),
        "gitCommitHash": "9d7ab9721f09d748db242c22d04aa494cbebc72d"
    },
    "nodes": assembled.get("nodes", []),
    "edges": assembled.get("edges", []),
    "layers": layers if isinstance(layers, list) else layers.get("layers", layers.get("Layers", [])),
    "tour": tour if isinstance(tour, list) else tour.get("steps", tour.get("tour", []))
}

# Normalize tour steps
tour_steps = graph["tour"]
for step in tour_steps:
    # Rename legacy fields
    if "nodesToInspect" in step and "nodeIds" not in step:
        step["nodeIds"] = step.pop("nodesToInspect")
    if "whyItMatters" in step and "description" not in step:
        step["description"] = step.pop("whyItMatters")
    # Convert bare paths to prefixed
    if "nodeIds" in step:
        converted = []
        prefixes = ("file:", "config:", "document:", "service:", "pipeline:", "table:", "schema:", "resource:", "endpoint:", "function:", "class:", "module:", "concept:")
        for nid in step["nodeIds"]:
            if isinstance(nid, str) and not nid.startswith(prefixes):
                if ":" in nid and not nid.startswith("file:"):
                    converted.append(nid)
                else:
                    converted.append(f"file:{nid}")
            else:
                converted.append(nid)
        step["nodeIds"] = converted

# Normalize layers
for layer in graph["layers"]:
    if "nodes" in layer and "nodeIds" not in layer:
        nodes = layer.pop("nodes")
        if isinstance(nodes, list):
            if nodes and isinstance(nodes[0], dict):
                layer["nodeIds"] = [n.get("id", "") for n in nodes if isinstance(n, dict)]
            else:
                layer["nodeIds"] = [str(n) for n in nodes]
        else:
            layer["nodeIds"] = []
    if "nodeIds" in layer:
        converted = []
        prefixes = ("file:", "config:", "document:", "service:", "pipeline:", "table:", "schema:", "resource:", "endpoint:", "function:", "class:", "module:", "concept:")
        for nid in layer["nodeIds"]:
            if isinstance(nid, str) and not nid.startswith(prefixes):
                if ":" in nid and not nid.startswith("file:"):
                    converted.append(nid)
                else:
                    converted.append(f"file:{nid}")
            else:
                converted.append(nid)
        layer["nodeIds"] = converted

# Sort tour by order
graph["tour"] = sorted(tour_steps, key=lambda s: s.get("order", 999))

with open(f"{intermediate}\\assembled-graph.json", "w", encoding="utf-8") as f:
    json.dump(graph, f, indent=2, ensure_ascii=False)

print(f"Assembled: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges, {len(graph['layers'])} layers, {len(graph['tour'])} tour steps")
