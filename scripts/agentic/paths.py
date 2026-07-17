"""Resolve repository root and .agentic paths."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def find_repo_root(start: Path | None = None) -> Path:
    """Walk parents until pyproject.toml + .agentic/ are found."""
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / "pyproject.toml").is_file() and (candidate / ".agentic").is_dir():
            return candidate
    # Fallback: scripts/agentic/../../
    here = Path(__file__).resolve().parent
    return here.parent.parent


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_config(repo_root: Path | None = None) -> dict[str, Any]:
    root = repo_root or find_repo_root()
    return load_json(root / ".agentic" / "config.json")


def out_dir(repo_root: Path, config: dict[str, Any]) -> Path:
    return repo_root / config["outputs"]["dir"]
