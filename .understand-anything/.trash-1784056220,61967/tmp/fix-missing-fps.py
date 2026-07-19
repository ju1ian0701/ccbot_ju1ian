import json
import os

root = r"D:\CCbot_tmux\ccbot\ccbot_ju1ian"
gpath = f"{root}\\.understand-anything\\intermediate\\assembled-graph.json"

with open(gpath, encoding="utf-8") as f:
    graph = json.load(f)

# filePath mapping for nodes created by batch-4, batch-5, batch-6, batch-7
# These agents didn't include filePath in their nodes
fp_map = {
    "pipeline:check": ".github/workflows/check.yml",
    "pipeline:claude-code-review": ".github/workflows/claude-code-review.yml",
    "pipeline:claude": ".github/workflows/claude.yml",
    "document:doc-architecture": ".claude/rules/architecture.md",
    "document:doc-message-handling": ".claude/rules/message-handling.md",
    "document:doc-topic-architecture": ".claude/rules/topic-architecture.md",
    "config:env_example": ".env.example",
    "config:pyproject": "pyproject.toml",
    "document:AGENTS": "AGENTS.md",
    "document:CLAUDE_MD": "CLAUDE.md",
    "document:README_EN": "README.md",
    "document:README_CN": "README_CN.md",
    "document:README_RU": "README_RU.md",
    "document:doc_font_license_jetbrainsmono": "src/ccbot/fonts/LICENSE-JetBrainsMono.txt",
    "document:doc_font_license_notosansmono": "src/ccbot/fonts/LICENSE-NotoSansMono.txt",
    "document:doc_font_license_symbola": "src/ccbot/fonts/LICENSE-Symbola.txt",
    "file:scripts/restart.sh": "scripts/restart.sh",
    "file:doc/telegram-bot-features.md": "doc/telegram-bot-features.md",
    "file:doc/WEBSOCKET_PROTOCOL_REVERSED.md": "doc/WEBSOCKET_PROTOCOL_REVERSED.md",
    "file:.understand-anything/.understandignore": ".understand-anything/.understandignore",
    "file:src/ccbot/fonts/NotoSansMonoCJKsc-Regular.otf": "src/ccbot/fonts/NotoSansMonoCJKsc-Regular.otf",
}

# Add filePath where missing
for node in graph["nodes"]:
    nid = node["id"]
    if "filePath" not in node and nid in fp_map:
        node["filePath"] = fp_map[nid]

# Also create ID aliases for nodes that have filePath but different ID conventions
# Map: canonical ID (with full path) -> actual node ID
all_ids = {n["id"] for n in graph["nodes"]}

# Now update layers to use the actual node IDs instead of canonical paths
canonical_to_actual = {}
for n in graph["nodes"]:
    nid = n["id"]
    fp = n.get("filePath", "")
    # Map by filePath basename without extension
    if fp:
        base = os.path.basename(fp)
        stem = os.path.splitext(base)[0]
        canonical_to_actual[stem] = nid
        canonical_to_actual[base] = nid
        canonical_to_actual[fp] = nid

# For document nodes without filePath that match by name patterns
canonical_to_actual.update({
    ".env.example": "config:env_example",
    "pyproject.toml": "config:pyproject",
    "AGENTS.md": "document:AGENTS",
    "CLAUDE.md": "document:CLAUDE_MD",
    "README.md": "document:README_EN",
    "README_CN.md": "document:README_CN",
    "README_RU.md": "document:README_RU",
    ".claude/rules/architecture.md": "document:doc-architecture",
    ".claude/rules/message-handling.md": "document:doc-message-handling",
    ".claude/rules/topic-architecture.md": "document:doc-topic-architecture",
    "src/ccbot/fonts/LICENSE-JetBrainsMono.txt": "document:doc_font_license_jetbrainsmono",
    "src/ccbot/fonts/LICENSE-NotoSansMono.txt": "document:doc_font_license_notosansmono",
    "src/ccbot/fonts/LICENSE-Symbola.txt": "document:doc_font_license_symbola",
    ".github/workflows/check.yml": "pipeline:check",
    ".github/workflows/claude-code-review.yml": "pipeline:claude-code-review",
    ".github/workflows/claude.yml": "pipeline:claude",
    "check.yml": "pipeline:check",
    "claude-code-review.yml": "pipeline:claude-code-review",
    "claude.yml": "pipeline:claude",
    "scripts/restart.sh": "file:scripts/restart.sh",
    "restart.sh": "file:scripts/restart.sh",
})

# Also add standard-prefix versions
for node in graph["nodes"]:
    nid = node["id"]
    fp = node.get("filePath", "")
    if fp:
        # Determine correct prefix
        prefix = nid.split(":")[0]
        canonical = f"{prefix}:{fp}"
        canonical_to_actual[canonical] = nid

# Fix layers
for layer in graph["layers"]:
    new_ids = []
    for ref in layer.get("nodeIds", []):
        ref_clean = ref
        # Strip prefix for lookup
        if ref in all_ids:
            new_ids.append(ref)
        elif ref in canonical_to_actual:
            new_ids.append(canonical_to_actual[ref])
        elif ref.split(":", 1)[1] if ":" in ref else ref in canonical_to_actual:
            bits = ref.split(":", 1)
            if bits[1] in canonical_to_actual:
                new_ids.append(canonical_to_actual[bits[1]])
            else:
                new_ids.append(ref)
        else:
            new_ids.append(ref)
    layer["nodeIds"] = new_ids

# Fix tour
for step in graph["tour"]:
    new_ids = []
    for ref in step.get("nodeIds", []):
        if ref in all_ids:
            new_ids.append(ref)
        elif ref in canonical_to_actual:
            new_ids.append(canonical_to_actual[ref])
        elif ref.split(":", 1)[1] if ":" in ref else ref in canonical_to_actual:
            bits = ref.split(":", 1)
            if bits[1] in canonical_to_actual:
                new_ids.append(canonical_to_actual[bits[1]])
            else:
                new_ids.append(ref)
        else:
            new_ids.append(ref)
    step["nodeIds"] = new_ids

# Verify
all_ids = {n["id"] for n in graph["nodes"]}
layer_issues = 0
for layer in graph["layers"]:
    for ref in layer.get("nodeIds", []):
        if ref not in all_ids:
            layer_issues += 1

tour_issues = 0
for step in graph["tour"]:
    for ref in step.get("nodeIds", []):
        if ref not in all_ids:
            tour_issues += 1

with open(gpath, "w", encoding="utf-8") as f:
    json.dump(graph, f, indent=2, ensure_ascii=False)

print(f"Fixed: {len(fp_map)} filePaths added, {layer_issues} layer issues, {tour_issues} tour issues")
