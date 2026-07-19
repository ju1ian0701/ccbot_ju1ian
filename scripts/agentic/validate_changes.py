"""Validate agent changes: quality gates + path guardrails."""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import find_repo_root, load_config, out_dir, write_json


def _run(cmd: list[str], cwd: Path) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "ok": proc.returncode == 0,
            "stdout_tail": (proc.stdout or "")[-4000:],
            "stderr_tail": (proc.stderr or "")[-4000:],
        }
    except FileNotFoundError as exc:
        return {
            "cmd": cmd,
            "returncode": 127,
            "ok": False,
            "stdout_tail": "",
            "stderr_tail": str(exc),
        }


def _git_changed_files(root: Path, base_ref: str | None) -> list[str]:
    if base_ref:
        proc = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return [ln.strip().replace("\\", "/") for ln in proc.stdout.splitlines() if ln.strip()]
    # Uncommitted + untracked fallback
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    files: list[str] = []
    for ln in (proc.stdout or "").splitlines():
        if len(ln) < 4:
            continue
        path = ln[3:].strip().replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return files


def _match_any(path: str, globs: list[str]) -> bool:
    """Match a changed path against allow/deny globs (files or untracked dirs)."""
    path = path.replace("\\", "/").rstrip("/")
    for pattern in globs:
        pat = pattern.replace("\\", "/")
        if fnmatch.fnmatch(path, pat):
            return True
        if fnmatch.fnmatch(path + "/", pat):
            return True
        # prefix/** covers the directory itself and all descendants
        if pat.endswith("/**"):
            prefix = pat[:-3].rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
        # tests/**/*.py style: allow the tests/ tree when used as dir entry
        if "/**/" in pat:
            root = pat.split("/**/", 1)[0].rstrip("/")
            if root and (path == root or path.startswith(root + "/")):
                return True
        if pat.endswith("/**/*") or pat.endswith("/**/*.py"):
            prefix = pat.split("/**", 1)[0].rstrip("/")
            if prefix and (path == prefix or path.startswith(prefix + "/")):
                return True
    return False


def check_guardrails(
    changed: list[str],
    config: dict[str, Any],
) -> dict[str, Any]:
    g = config.get("guardrails") or {}
    allowed = list(g.get("allowed_path_globs") or ["**/*"])
    blocked = list(g.get("blocked_path_globs") or [])
    ignored = list(g.get("ignored_path_globs") or [])
    max_files = int((config.get("implementation") or {}).get("max_files_changed") or 40)

    violations: list[str] = []
    blocked_hits: list[str] = []
    disallowed: list[str] = []
    ignored_hits: list[str] = []
    considered: list[str] = []

    for path in changed:
        if ignored and _match_any(path, ignored):
            ignored_hits.append(path)
            continue
        considered.append(path)
        if _match_any(path, blocked):
            blocked_hits.append(path)
            violations.append(f"blocked path: {path}")
            continue
        if allowed and not _match_any(path, allowed):
            disallowed.append(path)
            violations.append(f"path not in allow-list: {path}")

    if len(considered) > max_files:
        violations.append(f"too many files changed: {len(considered)} > {max_files}")

    return {
        "changed_files": changed,
        "considered_files": considered,
        "changed_count": len(considered),
        "ignored_paths": ignored_hits,
        "blocked_hits": blocked_hits,
        "disallowed_paths": disallowed,
        "violations": violations,
        "ok": not violations,
    }


def run(
    repo_root: Path | None = None,
    base_ref: str | None = None,
    skip_quality: bool = False,
) -> dict[str, Any]:
    root = repo_root or find_repo_root()
    config = load_config(root)
    impl = config.get("implementation") or {}

    base = base_ref or os.environ.get("AGENTIC_BASE_REF") or os.environ.get("GITHUB_BASE_REF")
    if base and not base.startswith("origin/") and os.environ.get("GITHUB_ACTIONS"):
        # workflow often checks out merge ref; optional
        pass

    changed = _git_changed_files(root, base_ref=base if base_ref else None)
    guard = check_guardrails(changed, config)

    quality: list[dict[str, Any]] = []
    if not skip_quality:
        # Prefer uv if available
        uv = "uv"
        steps: list[list[str]] = []
        if impl.get("require_ruff", True):
            steps.append([uv, "run", "ruff", "check", "src/", "tests/"])
            steps.append([uv, "run", "ruff", "format", "--check", "src/", "tests/"])
        if impl.get("require_pyright", True):
            steps.append([uv, "run", "pyright", "src/ccbot/"])
        if impl.get("require_tests", True):
            steps.append([uv, "run", "pytest", "--tb=short", "-q"])
        for cmd in steps:
            quality.append(_run(cmd, root))

    quality_ok = all(s.get("ok") for s in quality) if quality else True
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "guardrails": guard,
        "quality": quality,
        "ok": bool(guard.get("ok")) and quality_ok,
    }
    out = out_dir(root, config)
    write_json(out / config["outputs"]["validation"], report)
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Validate agentic changes")
    p.add_argument("--base-ref", default=None)
    p.add_argument("--skip-quality", action="store_true")
    args = p.parse_args(argv)
    report = run(base_ref=args.base_ref, skip_quality=args.skip_quality)
    print(json_dumps_summary(report))
    return 0 if report.get("ok") else 1


def json_dumps_summary(report: dict[str, Any]) -> str:
    g = report.get("guardrails") or {}
    lines = [
        f"validation_ok={report.get('ok')}",
        f"files={g.get('changed_count')}",
        f"guardrail_ok={g.get('ok')}",
    ]
    for v in g.get("violations") or []:
        lines.append(f"violation: {v}")
    for q in report.get("quality") or []:
        cmd = " ".join(q.get("cmd") or [])
        lines.append(f"quality[{'OK' if q.get('ok') else 'FAIL'}]: {cmd}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
