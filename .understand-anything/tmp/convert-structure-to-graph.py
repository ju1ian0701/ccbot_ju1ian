#!/usr/bin/env python3
"""Convert extract-structure.mjs output to GraphNode/GraphEdge format."""

import json
import sys
import os
from pathlib import Path

CATEGORY_TO_TYPE = {
    "code": "file",
    "config": "config",
    "docs": "document",
    "infra": "service",
    "data": "schema",
    "script": "file",
    "markup": "file",
}

def convert_file(file_result, import_map):
    nodes = []
    edges = []
    path = file_result["path"]
    ftype = CATEGORY_TO_TYPE.get(file_result.get("fileCategory", "code"), "file")
    lang = file_result.get("language", "unknown")
    total_lines = file_result.get("totalLines", 0)

    # Determine complexity
    if total_lines <= 50:
        complexity = "simple"
    elif total_lines <= 200:
        complexity = "moderate"
    else:
        complexity = "complex"

    # File-level node
    node_id = f"{ftype}:{path}"
    file_node = {
        "id": node_id,
        "type": ftype,
        "name": path.split("/")[-1],
        "filePath": path,
        "summary": f"{path} ({lang}, {total_lines} lines)",
        "tags": [lang, ftype, file_result.get("fileCategory", "code")],
        "complexity": complexity,
        "languageNotes": f"{lang}",
    }
    nodes.append(file_node)

    # Functions
    for fn in file_result.get("functions", []):
        fn_id = f"function:{path}:{fn['name']}"
        fn_node = {
            "id": fn_id,
            "type": "function",
            "name": fn["name"],
            "filePath": path,
            "summary": f"Function {fn['name']} in {path}",
            "tags": ["function", lang],
            "complexity": "moderate",
            "languageNotes": lang,
        }
        nodes.append(fn_node)
        edges.append({
            "source": node_id,
            "target": fn_id,
            "type": "contains",
            "weight": 1.0,
        })

    # Classes
    for cls in file_result.get("classes", []):
        cls_id = f"class:{path}:{cls['name']}"
        cls_node = {
            "id": cls_id,
            "type": "class",
            "name": cls["name"],
            "filePath": path,
            "summary": f"Class {cls['name']} in {path}",
            "tags": ["class", lang],
            "complexity": "moderate",
            "languageNotes": lang,
        }
        nodes.append(cls_node)
        edges.append({
            "source": node_id,
            "target": cls_id,
            "type": "contains",
            "weight": 1.0,
        })
        for m in cls.get("methods", []):
            m_id = f"function:{path}:{m['name']}" if isinstance(m, dict) else None
            if m_id and not any(n["id"] == m_id for n in nodes):
                mn = m.get("name", str(m)) if isinstance(m, dict) else str(m)
                m_node = {
                    "id": m_id,
                    "type": "function",
                    "name": mn,
                    "filePath": path,
                    "summary": f"Method {mn} in {cls['name']}",
                    "tags": ["method", lang],
                    "complexity": "moderate",
                    "languageNotes": lang,
                }
                nodes.append(m_node)
                edges.append({
                    "source": cls_id,
                    "target": m_id,
                    "type": "contains",
                    "weight": 1.0,
                })

    # Call graph edges (within same file)
    for cg in file_result.get("callGraph", []):
        caller = cg.get("caller", "")
        callee = cg.get("callee", "")
        if caller and callee:
            caller_id = f"function:{path}:{caller}"
            callee_id = f"function:{path}:{callee}"
            # Only create edge if both nodes exist
            if any(n["id"] == caller_id for n in nodes) and any(n["id"] == callee_id for n in nodes):
                edges.append({
                    "source": caller_id,
                    "target": callee_id,
                    "type": "calls",
                    "weight": 0.8,
                })

    # Import edges (from import_map)
    if path in import_map:
        for imp in import_map[path]:
            target_path = imp if isinstance(imp, str) else imp.get("path", "")
            if target_path:
                if ":" not in target_path:
                    # Determine target type based on file category
                    ttype = "file"
                    for inp, outp in CATEGORY_TO_TYPE.items():
                        if target_path.startswith("src/ccbot/") and inp == "code":
                            ttype = outp
                            break
                        if target_path.startswith("tests/") and inp == "code":
                            ttype = outp
                            break
                        if target_path.endswith(".md"):
                            ttype = "document"
                            break
                        if target_path.endswith(".toml") or target_path.endswith(".json") or target_path.endswith(".example"):
                            ttype = "config"
                            break
                    target_id = f"{ttype}:{target_path}"
                else:
                    target_id = target_path
                edges.append({
                    "source": node_id,
                    "target": target_id,
                    "type": "imports",
                    "weight": 0.7,
                })

    return nodes, edges


def main():
    project_root = Path(sys.argv[1])
    intermediate = project_root / ".understand-anything" / "intermediate"
    tmp_dir = project_root / ".understand-anything" / "tmp"
    import_map_path = intermediate / "import-map.json"

    # Load import map
    import_map = {}
    if import_map_path.exists():
        im_data = json.loads(import_map_path.read_text(encoding="utf-8"))
        import_map = im_data.get("importMap", {})

    # Process each batch
    batch_files = sorted(intermediate.glob("batch-*.json"))
    for bf in batch_files:
        data = json.loads(bf.read_text(encoding="utf-8"))
        all_nodes = []
        all_edges = []
        for result in data.get("results", []):
            nodes, edges = convert_file(result, import_map)
            all_nodes.extend(nodes)
            all_edges.extend(edges)

        # Write converted output
        output = {"nodes": all_nodes, "edges": all_edges}
        bf.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {bf.name}: {len(all_nodes)} nodes, {len(all_edges)} edges")

    print("Conversion complete")


if __name__ == "__main__":
    main()
