import json
with open(r"D:\CCbot_tmux\ccbot\ccbot_ju1ian\.understand-anything\intermediate\assembled-graph.json") as f:
    g = json.load(f)
targets = ["README", "AGENTS", "CLAUDE", ".env", "pyproject", "check.yml", "claude-code-review", "claude.yml", "architecture", "message-handling", "topic-architecture", "LICENSE", "JetBrains", "NotoSans", "Symbola", ".claude/rules", "restart.sh"]
for n in g["nodes"]:
    fp = n.get("filePath", "")
    nid = n["id"]
    for t in targets:
        if t in fp or t in nid:
            print(f"  {nid}  ->  {fp}")
            break
