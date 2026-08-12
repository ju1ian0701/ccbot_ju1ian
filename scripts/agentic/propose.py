#!/usr/bin/env python3
"""Proposal subsystem for the ccbot agentic pipeline.

Adds commands:
  propose           create proposal artifacts without applying changes
  import-proposal   validate/finalize a manually created proposal.diff
  show-proposal     show proposal artifacts
  reject            write reject artifact; no working-tree change
  approve           create approval.json after base_sha freshness check
  request-changes   archive proposal and save reviewer notes
  apply             apply diff only with approval + matching base_sha (feature branch)

This module intentionally reuses existing pipeline functions:
  prioritize.run         -- select/plan task
  render_prompt.run      -- render implement prompt
  build_context_pack.run -- build context pack
  paths.find_repo_root   -- repo root detection
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

try:
    from paths import find_repo_root

    ROOT = Path(find_repo_root())
except Exception:
    ROOT = Path(__file__).resolve().parents[2]

AGENTIC_DIR = ROOT / ".agentic"
OUT_DIR = AGENTIC_DIR / "out"
PROPOSALS_DIR = OUT_DIR / "proposals"
REJECTS_DIR = OUT_DIR / "rejects"
ARCHIVE_DIR = PROPOSALS_DIR / "archive"
TMP_DIR = AGENTIC_DIR / "tmp"

CONFIG_PATH = AGENTIC_DIR / "config.json"
TASKS_PATH = AGENTIC_DIR / "backlog" / "tasks.json"

DEFAULT_BASE_REF = "origin/main"
PROTECTED_BRANCHES = frozenset({"main", "master"})

DEFAULT_CONFIG = {
    "implementation": {
        "max_files_changed": 40,
        "max_loc_delta": 2000,
    },
    "guardrails": {
        "allow": [
            "src/ccbot/**/*.py",
            "tests/**",
            ".agentic/**",
            "scripts/agentic/**",
            ".gitignore",
            "REPAIR_ROBOT_INSTRUCTION.md",
        ],
        "block": [
            ".env",
            ".env.*",
            "**/.env",
            "**/.env.*",
            "state.json",
            "**/state.json",
            "session_map.json",
            "**/session_map.json",
            ".github/workflows/check.yml",
        ],
    },
    "human_gate": {
        "required_before_apply": True,
        "allow_auto_propose": True,
        "allow_auto_apply": False,
    },
}


# ---------------------------------------------------------------------------
# Basic utilities
# ---------------------------------------------------------------------------


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(
    cmd: list[str | Path],
    cwd: Path = ROOT,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [str(item) for item in cmd]
    printable = " ".join(cmd)
    print(f"[propose] + {printable}")

    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=check,
        text=True,
        capture_output=True,
        env=env,
    )


def git(*args: str, cwd: Path = ROOT) -> str:
    result = run(["git", *args], cwd=cwd)
    return result.stdout.strip()


def load_json(path: Path):
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")

    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value

    return base


def load_config() -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))

    if CONFIG_PATH.exists():
        user_config = load_json(CONFIG_PATH)
        if isinstance(user_config, dict):
            deep_merge(config, user_config)

    return config


# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------


def task_id_from(task: dict) -> str | None:
    return task.get("id") or task.get("task_id") or task.get("name")


def iter_tasks(data):
    if isinstance(data, list):
        for task in data:
            if isinstance(task, dict):
                yield task
        return

    if isinstance(data, dict):
        tasks = data.get("tasks")
        if isinstance(tasks, list):
            for task in tasks:
                if isinstance(task, dict):
                    yield task
            return

        # Fallback: dict keyed by task id.
        for key, value in data.items():
            if isinstance(value, dict):
                if task_id_from(value) is None:
                    value.setdefault("id", key)
                yield value


def load_task(task_id: str) -> dict:
    if not TASKS_PATH.exists():
        raise RuntimeError(f"Tasks file not found: {TASKS_PATH}")

    data = load_json(TASKS_PATH)

    for task in iter_tasks(data):
        if task_id_from(task) == task_id:
            return task

    raise RuntimeError(f"Task not found: {task_id}")


def update_task_status(task_id: str, status: str, proposal_id: str) -> None:
    if not TASKS_PATH.exists():
        raise RuntimeError(f"Tasks file not found: {TASKS_PATH}")

    data = load_json(TASKS_PATH)
    changed = False

    for task in iter_tasks(data):
        if task_id_from(task) == task_id:
            task["status"] = status
            task["last_proposal_id"] = proposal_id
            task["updated_at"] = iso_now()
            changed = True
            break

    if not changed:
        raise RuntimeError(f"Task not found for status update: {task_id}")

    write_json(TASKS_PATH, data)


# ---------------------------------------------------------------------------
# Git safety
# ---------------------------------------------------------------------------


def ensure_clean_tree(allow_dirty: bool) -> None:
    status = git("status", "--porcelain")

    if not status:
        return

    relevant_lines = []

    for line in status.splitlines():
        # Porcelain format: XY PATH
        path = line[3:].strip().strip('"').replace("\\", "/")

        if path.startswith(".agentic/out/"):
            continue

        if path.startswith(".agentic/tmp/"):
            continue

        relevant_lines.append(line)

    if relevant_lines and not allow_dirty:
        raise RuntimeError(
            "Working tree is dirty. Commit/stash changes or use --allow-dirty.\n"
            + "\n".join(relevant_lines)
        )


# ---------------------------------------------------------------------------
# Context / prompt preparation: reuse existing pipeline functions
# ---------------------------------------------------------------------------


def _task_acceptance(task: dict) -> object:
    """Prefer structured acceptance_criteria; fall back to acceptance blob."""
    if task.get("acceptance_criteria") is not None:
        return task.get("acceptance_criteria")
    if task.get("acceptance") is not None:
        return task.get("acceptance")
    return []


def render_default_prompt(task: dict) -> str:
    """Build implement prompt without triple-quoted f-strings (syntax-safe)."""
    task_id = task_id_from(task) or "UNKNOWN"
    title = str(task.get("title") or task.get("name") or "")
    acceptance = _task_acceptance(task)
    acceptance_json = json.dumps(acceptance, ensure_ascii=False, indent=2)
    problem = str(task.get("problem") or "") or "(see backlog)"
    solution = str(task.get("solution") or "") or "(see backlog)"

    # IMPORTANT: do not use return f"""...""" here — nested ```json fences
    # previously caused "unterminated triple-quoted f-string" when the file
    # was truncated mid-literal. Join plain strings instead.
    fence = chr(96) * 3  # ```
    lines = [
        "# Implement proposal for " + task_id,
        "",
        "## Task",
        title,
        "",
        "## Problem",
        problem,
        "",
        "## Solution direction",
        solution,
        "",
        "## Rules",
        "- Work only on this task.",
        "- Do not modify files outside the allowed context.",
        "- Do not add a new abstraction if acceptance requires deleting an old path.",
        "- Preserve product invariants:",
        "  - topic-only routing;",
        "  - unauthorized user denied;",
        "  - no General-topic routing;",
        "  - no callback wire-format change without explicit approval.",
        "- Add or update tests if product code changes.",
        "- Return a unified diff only (proposal artifacts).",
        "- Do not commit.",
        "- Do not push.",
        "- Do not merge.",
        "- Do not modify .git.",
        "- Do not apply to the working tree without human approve.",
        "",
        "## Acceptance",
        fence + "json",
        acceptance_json,
        fence,
        "",
        "## Output",
        "Write proposal artifacts under `.agentic/out/proposals/<TASK_ID>_<utc>/`:",
        "- proposal.md",
        "- proposal.diff",
        "- files.json",
        "- risk.json",
        "- evidence_plan.json",
        "- meta.json",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Proposal artifact helpers
# ---------------------------------------------------------------------------


def proposal_dir_for(task_id: str, stamp: str | None = None) -> Path:
    stamp = stamp or utc_stamp()
    return PROPOSALS_DIR / f"{task_id}_{stamp}"


def latest_proposal_dir(task_id: str | None = None) -> Path | None:
    if not PROPOSALS_DIR.is_dir():
        return None

    if task_id:
        pointer = PROPOSALS_DIR / f".latest-{task_id}.json"
        if pointer.is_file():
            data = load_json(pointer)
            rel = data.get("path")
            if rel:
                pointed = ROOT / rel
                if pointed.is_dir() and (pointed / "meta.json").is_file():
                    return pointed

    candidates: list[tuple[int, float, Path]] = []
    for path in PROPOSALS_DIR.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        # Pointer dirs like proposals/ISS-014/latest.json only — skip unless full package.
        has_package = (path / "proposal.md").is_file() and (
            path / "meta.json"
        ).is_file()
        if not has_package:
            continue
        if task_id and not (
            path.name == task_id
            or path.name.startswith(f"{task_id}_")
            or path.name.startswith(f"{task_id}-")
        ):
            continue
        # Prefer stamped dirs TASK_YYYY... over bare TASK id aliases.
        stamp_bonus = 1 if task_id and path.name.startswith(f"{task_id}_") else 0
        candidates.append((stamp_bonus, path.stat().st_mtime, path))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def resolve_proposal_dir(
    proposal: str | None, latest: bool, task_id: str | None = None
) -> Path:
    if latest or proposal in (None, "", "latest"):
        found = latest_proposal_dir(task_id=task_id)
        if not found:
            raise RuntimeError(
                "No proposal artifacts found under .agentic/out/proposals/"
            )
        return found

    assert proposal is not None
    raw = Path(proposal)
    if raw.is_dir():
        return raw.resolve()

    # Accept bare id or folder name under proposals/
    candidate = PROPOSALS_DIR / proposal
    if candidate.is_dir():
        return candidate

    # Prefix match: ISS-014 -> ISS-014_*
    matches = sorted(
        [
            p
            for p in PROPOSALS_DIR.iterdir()
            if p.is_dir() and (p.name == proposal or p.name.startswith(f"{proposal}_"))
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if matches:
        return matches[0]

    raise RuntimeError(f"Proposal not found: {proposal}")


def git_base_sha(base_ref: str) -> str:
    """Resolve proposal base_sha from base_ref (e.g. origin/main), not feature HEAD."""
    try:
        return git("rev-parse", base_ref)
    except Exception:
        # Fallback only if base_ref is missing/unresolvable (e.g. no origin/main yet).
        return git("rev-parse", "HEAD")


def capture_working_tree_diff(base_ref: str) -> str:
    """Capture branch changes vs base_ref as a pure unified diff.

    Only ``git diff base_ref...HEAD --binary`` output is allowed here — never
    markdown fences, ``#`` comments, or non-diff section markers.

    Without ``--diff-file``, the proposal diff must be against ``base_ref``
    (e.g. origin/main), not only uncommitted changes vs HEAD.
    """
    result = run(["git", "diff", f"{base_ref}...HEAD", "--binary"], check=False)
    diff = result.stdout or ""

    if not diff.strip():
        return ""

    return diff if diff.endswith("\n") else diff + "\n"


def list_changed_paths_from_diff(diff_text: str) -> list[str]:
    paths: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            # diff --git a/path b/path
            parts = line.split()
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    b_path = b_path[2:]
                paths.append(b_path.replace("\\", "/"))
        elif line.startswith("D\t") or line.startswith("D "):
            paths.append(line[1:].strip().replace("\\", "/"))
        elif line.startswith("# ---") or line.startswith("#"):
            continue
        elif "\t" in line and line[:1] in "AMDCR":
            # name-status lines: D\tpath
            _, _, rest = line.partition("\t")
            if rest:
                paths.append(rest.strip().replace("\\", "/"))
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _glob_to_regex(pattern: str) -> str:
    """Translate a gitignore-like glob (with ``**``) to a full-match regex."""
    i = 0
    out: list[str] = []
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        ch = pattern[i]
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return "^" + "".join(out) + "$"


def _match_glob(path: str, pattern: str) -> bool:
    """Match path against a guardrail glob (supports ``**`` and directory prefixes)."""
    path = path.replace("\\", "/").lstrip("./")
    pattern = pattern.replace("\\", "/").lstrip("./")

    if not pattern:
        return False

    if fnmatch(path, pattern):
        return True
    if fnmatch(Path(path).name, pattern):
        return True

    try:
        if re.fullmatch(_glob_to_regex(pattern), path) is not None:
            return True
    except re.error:
        pass

    # Patterns like **/state.json or **/*secret*
    if pattern.startswith("**/"):
        suffix = pattern[3:]
        if fnmatch(path, suffix) or fnmatch(Path(path).name, suffix):
            return True
        parts = path.split("/")
        for i in range(len(parts)):
            sub = "/".join(parts[i:])
            if fnmatch(sub, suffix):
                return True
            try:
                if re.fullmatch(_glob_to_regex(suffix), sub) is not None:
                    return True
            except re.error:
                pass

    # Bare filename pattern (e.g. state.json) against nested path
    if "/" not in pattern and fnmatch(Path(path).name, pattern):
        return True

    clean_pattern = pattern.rstrip("/")

    if path == clean_pattern:
        return True

    if path.startswith(clean_pattern + "/"):
        return True

    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")

    return False


def path_allowed(path: str, config: dict) -> tuple[bool, str]:
    path = path.replace("\\", "/").lstrip("./")

    guard = config.get("guardrails") or {}
    blocked = list(guard.get("blocked_path_globs") or guard.get("block") or [])
    allowed = list(guard.get("allowed_path_globs") or guard.get("allow") or [])
    ignored = list(guard.get("ignored_path_globs") or [])

    for pattern in blocked:
        if _match_glob(path, pattern):
            return False, f"blocked by {pattern}"

    for pattern in ignored:
        if _match_glob(path, pattern):
            return True, f"ignored {pattern}"

    if not allowed:
        return True, "no allow list"

    for pattern in allowed:
        if _match_glob(path, pattern):
            return True, f"allowed by {pattern}"

    # Repo-hygiene path always allowed when listed tasks touch gitignore.
    if path == ".gitignore":
        return True, "gitignore hygiene"

    return False, "not in allow list"


def _is_empty_diff(diff_text: str) -> bool:
    stripped = (diff_text or "").strip()

    if not stripped:
        return True

    # Legacy empty placeholder (older propose wrote comment-only skeletons).
    first_line = stripped.splitlines()[0]
    return first_line.startswith("# Empty proposal.diff")


def check_no_markdown_fences(diff_text: str) -> None:
    """Reject markdown code fences that corrupt git apply."""
    for i, line in enumerate(diff_text.splitlines(), start=1):
        if line.startswith("```"):
            raise RuntimeError(
                f"proposal.diff contains markdown fence at line {i}. "
                "proposal.diff must be a pure unified diff."
            )


def check_no_diff_comments(diff_text: str) -> None:
    """Reject # comment lines (e.g. staged/name-status section markers)."""
    for i, line in enumerate(diff_text.splitlines(), start=1):
        if line.startswith("#"):
            raise RuntimeError(
                f"proposal.diff contains comment at line {i}. "
                "Remove comments like '# --- staged ---' or '# --- name-status ---'."
            )


def check_pure_unified_diff(diff_text: str) -> None:
    """Ensure proposal.diff is pure unified diff before git apply."""
    if _is_empty_diff(diff_text):
        return

    check_no_markdown_fences(diff_text)
    check_no_diff_comments(diff_text)


def _write_temp_diff(diff_bytes: bytes) -> Path:
    """Stage diff bytes 1:1 — text-mode staging corrupts CRLF patches (ISS-016)."""
    fd = tempfile.NamedTemporaryFile(
        "wb",
        suffix=".diff",
        delete=False,
    )
    path = Path(fd.name)

    fd.write(diff_bytes)
    fd.close()

    return path


def check_diff_applies(diff_bytes: bytes) -> None:
    diff_text = diff_bytes.decode("utf-8")

    if _is_empty_diff(diff_text):
        return

    check_pure_unified_diff(diff_text)

    tmp_path = _write_temp_diff(diff_bytes)

    try:
        attempts = [
            # Обычная проверка против текущего дерева.
            [
                "git",
                "apply",
                "--check",
                "--whitespace=nowarn",
                str(tmp_path),
            ],
            # Если diff сделан из unstaged working tree, проверяем против index/HEAD.
            [
                "git",
                "apply",
                "--cached",
                "--check",
                "--whitespace=nowarn",
                str(tmp_path),
            ],
            # Если изменение уже в working tree, reverse-check подтверждает,
            # что diff соответствует текущему состоянию.
            [
                "git",
                "apply",
                "--reverse",
                "--check",
                "--whitespace=nowarn",
                str(tmp_path),
            ],
        ]

        last_result = None

        for cmd in attempts:
            last_result = run(cmd, check=False)

            if last_result.returncode == 0:
                return

        raise RuntimeError(
            "Diff does not apply cleanly.\n"
            f"stdout:\n{last_result.stdout}\n"
            f"stderr:\n{last_result.stderr}"
        )

    finally:
        tmp_path.unlink(missing_ok=True)


def diff_numstat(diff_bytes: bytes) -> list[dict]:
    """Parse ``git apply --numstat`` for a unified diff."""
    diff_text = diff_bytes.decode("utf-8")

    if _is_empty_diff(diff_text):
        return []

    # Reject fences/comments before git apply --numstat (corrupt patches).
    check_pure_unified_diff(diff_text)

    tmp_path = _write_temp_diff(diff_bytes)

    try:
        result = run(
            ["git", "apply", "--numstat", str(tmp_path)],
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(
                "git apply --numstat failed.\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            )

        files: list[dict] = []

        for line in (result.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue

            added_raw, deleted_raw, path = parts[0], parts[1], parts[2]

            added = int(added_raw) if added_raw.isdigit() else 0
            deleted = int(deleted_raw) if deleted_raw.isdigit() else 0

            files.append(
                {
                    "path": path.replace("\\", "/"),
                    "added": added,
                    "deleted": deleted,
                }
            )

        return files
    finally:
        tmp_path.unlink(missing_ok=True)


def validate_proposal_diff(diff_bytes: bytes, *, allow_empty: bool = False) -> None:
    """Validate that proposal.diff is empty (if allowed) or a usable unified diff."""
    diff_text = diff_bytes.decode("utf-8")

    if _is_empty_diff(diff_text):
        if allow_empty:
            return
        raise RuntimeError(
            "Empty proposal.diff is not allowed. "
            "Make changes, pass --diff-file, or use --allow-empty."
        )

    check_pure_unified_diff(diff_text)

    has_git_header = any(
        line.startswith("diff --git ") for line in diff_text.splitlines()
    )
    if not has_git_header:
        raise RuntimeError(
            "proposal.diff is not a valid unified diff (missing 'diff --git' headers)"
        )

    check_diff_applies(diff_bytes)


def build_files_json(numstat: list[dict], config: dict) -> dict:
    """Build files.json from numstat rows + path guardrails."""
    entries: list[dict] = []

    for item in numstat:
        path = str(item.get("path") or "").replace("\\", "/")
        ok, reason = path_allowed(path, config)

        entries.append(
            {
                "path": path,
                "added": item.get("added", 0),
                "deleted": item.get("deleted", 0),
                "allowed": ok,
                "reason": reason,
            }
        )

    loc_delta = sum(
        int(entry.get("added") or 0) + int(entry.get("deleted") or 0)
        for entry in entries
    )

    return {
        "files": entries,
        "count": len(entries),
        "blocked_count": sum(1 for entry in entries if not entry["allowed"]),
        "loc_delta": loc_delta,
    }


def build_risk_json(task: dict, paths: list[str]) -> dict:
    risk = str(task.get("estimated_risk") or "medium").lower()
    blast = {"low": "low", "medium": "med", "high": "high"}.get(risk, "med")
    return {
        "task_id": task_id_from(task),
        "blast_radius": blast,
        "regression_surfaces": [
            "git status / review noise",
            "knowledge-graph tooling paths",
        ]
        if any(p.startswith(".understand-anything") or p == ".gitignore" for p in paths)
        else ["see proposal.md"],
        "invariants": [
            "topic-only routing",
            "no silent apply without approve",
            "no secrets/state in proposal",
        ],
        "files_touched": paths,
        "rollback": "git checkout HEAD -- <files>; drop proposal branch if unmerged",
    }


def build_evidence_plan(task: dict) -> dict:
    criteria = _task_acceptance(task)

    if isinstance(criteria, list):
        checks = [str(c) for c in criteria]
    else:
        checks = [str(criteria)]

    acceptance = task.get("acceptance") or task.get("acceptance_criteria") or {}

    if not isinstance(acceptance, dict):
        acceptance = {}

    grep = acceptance.get("grep", [])
    tests = acceptance.get("tests", [])

    if not isinstance(grep, list):
        grep = [str(grep)] if grep else []
    else:
        grep = [str(x) for x in grep]

    if not isinstance(tests, list):
        tests = [str(tests)] if tests else []
    else:
        tests = [str(x) for x in tests]

    manual = acceptance.get("manual", [])

    if not manual:
        manual = checks
    elif not isinstance(manual, list):
        manual = [str(manual)]
    else:
        manual = [str(x) for x in manual]

    commands = [
        "git status --porcelain",
        "git diff --stat",
    ]

    if isinstance(grep, list):
        commands.extend(str(cmd) for cmd in grep)

    return {
        "task_id": task_id_from(task),
        "kind": task.get("kind") or "",
        "acceptance_criteria": checks,
        "evidence": task.get("evidence") or {},
        "grep": grep,
        "tests": tests,
        "manual": manual,
        "commands": commands,
    }


def build_proposal_md(
    task: dict, proposal_id: str, base_sha: str, paths: list[str]
) -> str:
    task_id = task_id_from(task) or "UNKNOWN"
    title = task.get("title") or ""
    problem = task.get("problem") or ""
    solution = task.get("solution") or ""
    criteria = _task_acceptance(task)
    if isinstance(criteria, list):
        crit_lines = "\n".join(f"- {c}" for c in criteria)
    else:
        crit_lines = f"- {criteria}"

    path_lines = (
        "\n".join(f"- `{p}`" for p in paths) or "- (no paths yet — fill proposal.diff)"
    )

    return "\n".join(
        [
            f"# Proposal: {task_id} — {title}",
            "",
            f"**Proposal ID:** `{proposal_id}`  ",
            f"**Task ID:** `{task_id}`  ",
            f"**Base SHA:** `{base_sha}`  ",
            "**Status:** proposed (awaiting human approve)",
            "",
            "## Why",
            str(problem) or "(see backlog)",
            "",
            "## What",
            str(solution) or "(see backlog / proposal.diff)",
            "",
            "## Files",
            path_lines,
            "",
            "## Acceptance",
            crit_lines,
            "",
            "## Risks",
            "See `risk.json`.",
            "",
            "## Evidence plan",
            "See `evidence_plan.json`.",
            "",
            "## Rollback",
            "- Unmerged: drop branch / restore files from HEAD.",
            "- Merged: `git revert` and re-open issue if needed.",
            "",
            "## HITL",
            "- Do **not** apply without human approve.",
            "- Do **not** commit to `main` without PR.",
            "",
            "## Decision",
            "- [ ] approved",
            "- [ ] rejected (reason required)",
            "- [ ] needs-changes",
            "",
        ]
    )


def create_proposal_artifacts(
    task: dict,
    *,
    base_ref: str,
    diff_bytes: bytes | None = None,
    allow_empty: bool = True,
) -> Path:
    config = load_config()
    task_id = task_id_from(task) or "UNKNOWN"
    stamp = utc_stamp()
    proposal_id = f"{task_id}_{stamp}"
    dest = proposal_dir_for(task_id, stamp)
    dest.mkdir(parents=True, exist_ok=True)

    base_sha = git_base_sha(base_ref)
    if diff_bytes is None:
        diff_bytes = capture_working_tree_diff(base_ref).encode("utf-8")

    diff_text = diff_bytes.decode("utf-8")

    if _is_empty_diff(diff_text):
        if not allow_empty:
            raise RuntimeError(
                "No changes to propose (empty unified diff). "
                "Make changes, pass --diff-file, or use --allow-empty."
            )
        # Pure empty file — no # comments / markdown (those break git apply).
        diff_text = ""
        diff_bytes = b""

    # Format guards before any git apply --numstat / --check.
    check_pure_unified_diff(diff_text)

    numstat = diff_numstat(diff_bytes)
    check_diff_applies(diff_bytes)

    paths = [item["path"] for item in numstat] or list_changed_paths_from_diff(
        diff_text
    )
    files_payload = build_files_json(numstat, config)

    if files_payload.get("blocked_count", 0) > 0:
        blocked = [
            entry["path"]
            for entry in files_payload.get("files", [])
            if not entry.get("allowed")
        ]
        raise RuntimeError(
            "Proposal contains blocked paths:\n"
            + "\n".join(f"- {path}" for path in blocked)
        )

    max_files = int(config.get("implementation", {}).get("max_files_changed", 40))
    max_loc = int(config.get("implementation", {}).get("max_loc_delta", 2000))

    if files_payload.get("count", 0) > max_files:
        raise RuntimeError(
            f"Too many files changed: {files_payload.get('count')} > {max_files}"
        )

    if files_payload.get("loc_delta", 0) > max_loc:
        raise RuntimeError(
            f"Too large diff: {files_payload.get('loc_delta')} > {max_loc}"
        )

    risk_payload = build_risk_json(task, paths)
    evidence_payload = build_evidence_plan(task)
    meta = {
        "proposal_id": proposal_id,
        "task_id": task_id,
        "title": task.get("title"),
        "base_sha": base_sha,
        "base_ref": base_ref,
        "head_sha": git("rev-parse", "HEAD"),
        "loc_delta": files_payload.get("loc_delta", 0),
        "created_at": iso_now(),
        "status": "proposed",
        "decision": None,
        "approver": None,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "files_count": files_payload.get("count", len(paths)),
        "blocked_count": files_payload.get("blocked_count", 0),
        "max_files_changed": max_files,
        "max_loc_delta": max_loc,
    }

    write_bytes(dest / "proposal.diff", diff_bytes)
    write_text(
        dest / "proposal.md", build_proposal_md(task, proposal_id, base_sha, paths)
    )
    write_json(dest / "files.json", files_payload)
    write_json(dest / "risk.json", risk_payload)
    write_json(dest / "evidence_plan.json", evidence_payload)
    write_json(dest / "meta.json", meta)

    prompt_dest = dest / "implement-prompt.md"

    if not prompt_dest.exists():
        default_prompt = OUT_DIR / "implement-prompt.md"

        if default_prompt.is_file():
            shutil.copy2(default_prompt, prompt_dest)
        else:
            write_text(prompt_dest, render_default_prompt(task))

    context_dest = dest / "context-pack.json"

    if not context_dest.exists():
        default_context = OUT_DIR / "context-pack.json"

        if default_context.is_file():
            shutil.copy2(default_context, context_dest)
        else:
            write_json(
                context_dest,
                {
                    "task_id": task_id,
                    "note": "Context pack not found. Run select/context before propose.",
                },
            )

    # Pointer file (does not create a competing proposal package directory)
    write_json(
        PROPOSALS_DIR / f".latest-{task_id}.json",
        {
            "latest_proposal_id": proposal_id,
            "path": str(dest.relative_to(ROOT)).replace("\\", "/"),
            "updated_at": iso_now(),
        },
    )

    return dest


def prepare_pipeline_context(task_id: str) -> None:
    """Best-effort: run plan/select + context + prompt render for the task."""
    try:
        from prioritize import run as run_plan

        run_plan(task_id=task_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[propose] warn: plan/select skipped: {exc}")

    try:
        from render_prompt import run as run_render

        run_render()
    except Exception as exc:  # noqa: BLE001
        print(f"[propose] warn: render_prompt skipped: {exc}")

    try:
        from build_context_pack import run as run_context

        run_context(task_id=task_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[propose] warn: context pack skipped: {exc}")


# ---------------------------------------------------------------------------
# HITL: reject / approve / request_changes / apply
# ---------------------------------------------------------------------------


def _proposal_id_of(dest: Path, meta: dict | None = None) -> str:
    if meta and meta.get("proposal_id"):
        return str(meta["proposal_id"])
    return dest.name


def _load_proposal_meta(dest: Path) -> dict:
    meta_path = dest / "meta.json"
    if not meta_path.is_file():
        raise RuntimeError(f"meta.json missing in proposal: {dest}")
    meta = load_json(meta_path)
    if not isinstance(meta, dict):
        raise RuntimeError(f"meta.json is not an object: {meta_path}")
    return meta


def _update_proposal_meta(dest: Path, **fields) -> dict:
    meta = _load_proposal_meta(dest)
    meta.update(fields)
    meta["updated_at"] = iso_now()
    write_json(dest / "meta.json", meta)
    return meta


def _current_base_sha(base_ref: str | None = None) -> str:
    return git_base_sha(base_ref or DEFAULT_BASE_REF)


def _assert_base_sha_fresh(
    expected_sha: str | None,
    *,
    base_ref: str | None = None,
    context: str = "proposal",
) -> str:
    """Require that proposal/approval base_sha still matches origin/main (or base_ref)."""
    if not expected_sha:
        raise RuntimeError(f"{context}: missing base_sha in metadata")

    ref = base_ref or DEFAULT_BASE_REF
    current = _current_base_sha(ref)
    if str(expected_sha) != current:
        raise RuntimeError(
            f"stale base_sha for {context}: "
            f"recorded={expected_sha} current({ref})={current}. "
            "Re-propose from a fresh base before approve/apply."
        )
    return current


def _current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD")


def _assert_feature_branch() -> str:
    branch = _current_branch()
    if branch in PROTECTED_BRANCHES:
        raise RuntimeError(
            f"apply blocked on protected branch '{branch}'. "
            "Checkout a feature branch first."
        )
    if branch == "HEAD":
        raise RuntimeError(
            "apply blocked in detached HEAD state; checkout a feature branch."
        )
    return branch


def reject(proposal_id: str, reason: str, *, task_id: str | None = None) -> Path:
    """Reject a proposal: write reject artifact under .agentic/out/rejects/.

    Does **not** modify the git working tree (only writes under .agentic/out/,
    which is gitignored). Does not mark the task done.
    """
    reason = (reason or "").strip()
    if not reason:
        raise RuntimeError("reject requires a non-empty reason")

    dest = resolve_proposal_dir(proposal=proposal_id, latest=False, task_id=task_id)
    meta = _load_proposal_meta(dest)
    pid = _proposal_id_of(dest, meta)

    payload = {
        "proposal_id": pid,
        "task_id": meta.get("task_id"),
        "decision": "rejected",
        "reason": reason,
        "rejected_at": iso_now(),
        "base_sha": meta.get("base_sha"),
        "base_ref": meta.get("base_ref") or DEFAULT_BASE_REF,
        "proposal_path": str(dest.relative_to(ROOT)).replace("\\", "/"),
        "branch": _current_branch(),
    }

    reject_path = REJECTS_DIR / f"{pid}.json"
    write_json(reject_path, payload)

    _update_proposal_meta(
        dest,
        status="rejected",
        decision="rejected",
        reject_reason=reason,
        rejected_at=payload["rejected_at"],
    )

    # Side-car in proposal dir (gitignored via .agentic/out/).
    write_json(dest / "reject.json", payload)

    return reject_path


def approve(
    proposal_id: str,
    message: str,
    *,
    task_id: str | None = None,
    base_ref: str | None = None,
    approver: str | None = None,
) -> Path:
    """Approve a proposal: create approval.json after base_sha check.

    Does **not** apply the diff — call apply() separately.
    """
    dest = resolve_proposal_dir(proposal=proposal_id, latest=False, task_id=task_id)
    meta = _load_proposal_meta(dest)
    pid = _proposal_id_of(dest, meta)
    ref = base_ref or meta.get("base_ref") or DEFAULT_BASE_REF

    current_sha = _assert_base_sha_fresh(
        meta.get("base_sha"),
        base_ref=ref,
        context=f"approve({pid})",
    )

    notes = (message or "").strip()
    who = (approver or "").strip() or (
        os.environ.get("USER") or os.environ.get("USERNAME") or "human"
    )

    approval = {
        "proposal_id": pid,
        "task_id": meta.get("task_id"),
        "decision": "approved",
        "approver": who,
        "timestamp": iso_now(),
        "base_sha": current_sha,
        "base_ref": ref,
        "notes": notes,
        "message": notes,
        "proposal_path": str(dest.relative_to(ROOT)).replace("\\", "/"),
        "branch": _current_branch(),
    }

    approval_path = dest / "approval.json"
    write_json(approval_path, approval)

    _update_proposal_meta(
        dest,
        status="approved",
        decision="approved",
        approver=who,
        approved_at=approval["timestamp"],
        approval_notes=notes,
        approval_base_sha=current_sha,
    )

    return approval_path


def request_changes(
    proposal_id: str,
    notes: str,
    *,
    task_id: str | None = None,
) -> Path:
    """Request changes: archive the old proposal and persist reviewer notes.

    Does **not** modify the git working tree.
    """
    notes = (notes or "").strip()
    if not notes:
        raise RuntimeError("request_changes requires non-empty notes")

    dest = resolve_proposal_dir(proposal=proposal_id, latest=False, task_id=task_id)
    meta = _load_proposal_meta(dest)
    pid = _proposal_id_of(dest, meta)
    task = meta.get("task_id") or task_id

    changes_payload = {
        "proposal_id": pid,
        "task_id": task,
        "decision": "changes_requested",
        "notes": notes,
        "requested_at": iso_now(),
        "base_sha": meta.get("base_sha"),
        "base_ref": meta.get("base_ref") or DEFAULT_BASE_REF,
        "proposal_path": str(dest.relative_to(ROOT)).replace("\\", "/"),
        "branch": _current_branch(),
    }

    write_json(dest / "request_changes.json", changes_payload)
    _update_proposal_meta(
        dest,
        status="changes_requested",
        decision="changes_requested",
        change_notes=notes,
        changes_requested_at=changes_payload["requested_at"],
    )

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_dest = ARCHIVE_DIR / dest.name
    if archive_dest.exists():
        archive_dest = ARCHIVE_DIR / f"{dest.name}_{utc_stamp()}"

    shutil.copytree(dest, archive_dest)
    write_json(archive_dest / "request_changes.json", changes_payload)

    # Point latest pointer away from the archived package if it was current.
    if task:
        pointer = PROPOSALS_DIR / f".latest-{task}.json"
        if pointer.is_file():
            data = load_json(pointer)
            rel = str(data.get("path") or "").replace("\\", "/")
            current_rel = str(dest.relative_to(ROOT)).replace("\\", "/")
            if rel == current_rel or data.get("latest_proposal_id") == pid:
                write_json(
                    pointer,
                    {
                        "latest_proposal_id": pid,
                        "path": str(archive_dest.relative_to(ROOT)).replace("\\", "/"),
                        "status": "changes_requested",
                        "archived": True,
                        "updated_at": iso_now(),
                    },
                )

    # Keep live package for show-proposal, but mark archived path.
    _update_proposal_meta(
        dest,
        archived_to=str(archive_dest.relative_to(ROOT)).replace("\\", "/"),
    )
    write_json(
        dest / "request_changes.json",
        {**changes_payload, "archived_to": str(archive_dest)},
    )

    return archive_dest


def apply(
    proposal_id: str,
    *,
    task_id: str | None = None,
    base_ref: str | None = None,
    allow_on_main: bool = False,
) -> Path:
    """Apply proposal.diff only with approval.json + matching base_sha.

    Blocks apply on main/master unless allow_on_main is explicitly True
    (never recommended; reserved for emergency tooling).
    """
    dest = resolve_proposal_dir(proposal=proposal_id, latest=False, task_id=task_id)
    meta = _load_proposal_meta(dest)
    pid = _proposal_id_of(dest, meta)
    ref = base_ref or meta.get("base_ref") or DEFAULT_BASE_REF

    config = load_config()
    human_gate = config.get("human_gate") or {}
    if human_gate.get("required_before_apply", True):
        approval_path = dest / "approval.json"
        if not approval_path.is_file():
            raise RuntimeError(
                f"apply blocked: approval.json missing for {pid}. Run approve first."
            )
        approval = load_json(approval_path)
        if not isinstance(approval, dict):
            raise RuntimeError(f"approval.json is not an object: {approval_path}")
        if approval.get("decision") != "approved":
            raise RuntimeError(
                f"apply blocked: decision={approval.get('decision')!r}, "
                "expected 'approved'"
            )
        expected_sha = approval.get("base_sha") or meta.get("base_sha")
        _assert_base_sha_fresh(
            expected_sha,
            base_ref=approval.get("base_ref") or ref,
            context=f"apply({pid})",
        )
        # Also require meta base_sha matches approval (tamper / re-base safety).
        if meta.get("base_sha") and approval.get("base_sha"):
            if str(meta["base_sha"]) != str(approval["base_sha"]):
                raise RuntimeError(
                    f"apply blocked: meta.base_sha ({meta['base_sha']}) != "
                    f"approval.base_sha ({approval['base_sha']})"
                )
    else:
        _assert_base_sha_fresh(
            meta.get("base_sha"), base_ref=ref, context=f"apply({pid})"
        )

    if not allow_on_main:
        _assert_feature_branch()

    diff_path = dest / "proposal.diff"
    if not diff_path.is_file():
        raise RuntimeError(f"proposal.diff missing: {diff_path}")

    diff_bytes = diff_path.read_bytes()
    diff_text = diff_bytes.decode("utf-8")
    if _is_empty_diff(diff_text):
        raise RuntimeError("apply blocked: empty proposal.diff")

    check_pure_unified_diff(diff_text)

    # Check then apply against the working tree (feature branch only).
    check_result = run(
        ["git", "apply", "--check", "--whitespace=nowarn", str(diff_path)],
        check=False,
    )
    if check_result.returncode != 0:
        raise RuntimeError(
            "apply blocked: git apply --check failed.\n"
            f"stdout:\n{check_result.stdout}\n"
            f"stderr:\n{check_result.stderr}"
        )

    apply_result = run(
        ["git", "apply", "--whitespace=nowarn", str(diff_path)],
        check=False,
    )
    if apply_result.returncode != 0:
        raise RuntimeError(
            "apply failed: git apply returned non-zero.\n"
            f"stdout:\n{apply_result.stdout}\n"
            f"stderr:\n{apply_result.stderr}"
        )

    applied_at = iso_now()
    _update_proposal_meta(
        dest,
        status="applied",
        decision="applied",
        applied_at=applied_at,
        applied_branch=_current_branch(),
        applied_head_sha=git("rev-parse", "HEAD"),
    )
    write_json(
        dest / "apply.json",
        {
            "proposal_id": pid,
            "task_id": meta.get("task_id"),
            "decision": "applied",
            "applied_at": applied_at,
            "branch": _current_branch(),
            "base_sha": meta.get("base_sha"),
            "base_ref": ref,
        },
    )

    return dest


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


def cmd_propose(args: argparse.Namespace) -> int:
    task_id = args.task
    if not task_id:
        # Try selected-task.json
        selected = load_json(OUT_DIR / "selected-task.json")
        task_id = selected.get("id") or selected.get("task_id")
    if not task_id:
        print(
            "propose_failed: --task is required (or run select first)", file=sys.stderr
        )
        return 2

    try:
        ensure_clean_tree(allow_dirty=bool(args.allow_dirty))
    except RuntimeError as exc:
        print(f"propose_failed: {exc}", file=sys.stderr)
        return 1

    try:
        task = load_task(task_id)
    except RuntimeError as exc:
        print(f"propose_failed: {exc}", file=sys.stderr)
        return 1

    base_ref = args.base_ref or DEFAULT_BASE_REF

    if not args.skip_context:
        prepare_pipeline_context(task_id)

    diff_bytes = None
    if args.diff_file:
        diff_path = Path(args.diff_file)
        if not diff_path.is_file():
            print(f"propose_failed: diff file not found: {diff_path}", file=sys.stderr)
            return 1
        diff_bytes = diff_path.read_bytes()

    try:
        dest = create_proposal_artifacts(
            task,
            base_ref=base_ref,
            diff_bytes=diff_bytes,
            allow_empty=bool(args.allow_empty),
        )
    except RuntimeError as exc:
        print(f"propose_failed: {exc}", file=sys.stderr)
        return 1

    # Soft status update: PROPOSE (do not mark done)
    try:
        if args.update_status:
            update_task_status(task_id, "PROPOSE", dest.name)
    except RuntimeError as exc:
        print(f"[propose] warn: status update skipped: {exc}")

    meta = load_json(dest / "meta.json")
    print(f"propose_ok task={task_id} proposal={meta.get('proposal_id')}")
    print(f"  dir={dest}")
    for name in (
        "proposal.md",
        "proposal.diff",
        "files.json",
        "risk.json",
        "evidence_plan.json",
        "meta.json",
    ):
        print(f"  {name}={(dest / name)}")
    print("  status=proposed (awaiting human approve; no apply performed)")
    return 0


def cmd_show_proposal(args: argparse.Namespace) -> int:
    try:
        dest = resolve_proposal_dir(
            proposal=args.proposal,
            latest=bool(args.latest) or not args.proposal,
            task_id=args.task,
        )
    except RuntimeError as exc:
        print(f"show_proposal_failed: {exc}", file=sys.stderr)
        return 1

    meta = load_json(dest / "meta.json") if (dest / "meta.json").is_file() else {}
    print(f"proposal_dir={dest}")
    if meta:
        print(json.dumps(meta, indent=2, ensure_ascii=False))
    for name in (
        "proposal.md",
        "proposal.diff",
        "files.json",
        "risk.json",
        "evidence_plan.json",
        "meta.json",
    ):
        path = dest / name
        status = "OK" if path.is_file() else "MISSING"
        size = path.stat().st_size if path.is_file() else 0
        print(f"  [{status}] {name} ({size} bytes)")

    if args.full and (dest / "proposal.md").is_file():
        print("\n----- proposal.md -----\n")
        print((dest / "proposal.md").read_text(encoding="utf-8"))
    return 0


def cmd_import_proposal(args: argparse.Namespace) -> int:
    """Validate/finalize proposal.diff into artifact package."""
    config = load_config()

    # Mode 1: import external diff file as a new proposal.
    if args.diff_file:
        if not args.task:
            print(
                "import_proposal_failed: --task is required with --diff-file",
                file=sys.stderr,
            )
            return 2

        try:
            task = load_task(args.task)
        except RuntimeError as exc:
            print(f"import_proposal_failed: {exc}", file=sys.stderr)
            return 1

        diff_path = Path(args.diff_file)

        if not diff_path.is_file():
            print(
                f"import_proposal_failed: diff file not found: {diff_path}",
                file=sys.stderr,
            )
            return 1

        diff_bytes = diff_path.read_bytes()
        base_ref = args.base_ref or DEFAULT_BASE_REF

        try:
            dest = create_proposal_artifacts(
                task,
                base_ref=base_ref,
                diff_bytes=diff_bytes,
                allow_empty=False,
            )
        except RuntimeError as exc:
            print(f"import_proposal_failed: {exc}", file=sys.stderr)
            return 1

        meta = load_json(dest / "meta.json")
        print(f"import_proposal_ok task={args.task} proposal={meta.get('proposal_id')}")
        print(f"  dir={dest}")
        return 0

    # Mode 2: finalize existing proposal directory.
    try:
        dest = resolve_proposal_dir(
            proposal=args.proposal,
            latest=bool(args.latest) or not args.proposal,
            task_id=args.task,
        )
    except RuntimeError as exc:
        print(f"import_proposal_failed: {exc}", file=sys.stderr)
        return 1

    diff_path = dest / "proposal.diff"

    if not diff_path.is_file():
        print(
            f"import_proposal_failed: proposal.diff not found in {dest}",
            file=sys.stderr,
        )
        return 1

    diff_bytes = diff_path.read_bytes()
    diff_text = diff_bytes.decode("utf-8")

    try:
        check_pure_unified_diff(diff_text)
        numstat = diff_numstat(diff_bytes)
        check_diff_applies(diff_bytes)
    except RuntimeError as exc:
        print(f"import_proposal_failed: {exc}", file=sys.stderr)
        return 1

    files_payload = build_files_json(numstat, config)

    if files_payload.get("blocked_count", 0) > 0:
        blocked = [
            entry["path"]
            for entry in files_payload.get("files", [])
            if not entry.get("allowed")
        ]
        print(
            "import_proposal_failed: blocked paths present in diff:\n"
            + "\n".join(f"- {path}" for path in blocked),
            file=sys.stderr,
        )
        return 1

    max_files = int(config.get("implementation", {}).get("max_files_changed", 40))
    max_loc = int(config.get("implementation", {}).get("max_loc_delta", 2000))

    if files_payload.get("count", 0) > max_files:
        print(
            f"import_proposal_failed: too many files changed: "
            f"{files_payload.get('count')} > {max_files}",
            file=sys.stderr,
        )
        return 1

    if files_payload.get("loc_delta", 0) > max_loc:
        print(
            f"import_proposal_failed: too large diff: "
            f"{files_payload.get('loc_delta')} > {max_loc}",
            file=sys.stderr,
        )
        return 1

    write_json(dest / "files.json", files_payload)

    meta_path = dest / "meta.json"
    meta = load_json(meta_path) if meta_path.is_file() else {}

    if not isinstance(meta, dict):
        meta = {}

    meta.update(
        {
            "status": "proposed",
            "imported_at": iso_now(),
            "blocked_count": files_payload.get("blocked_count", 0),
            "loc_delta": files_payload.get("loc_delta", 0),
        }
    )

    write_json(meta_path, meta)

    if args.update_status:
        task_id = args.task or meta.get("task_id")

        if task_id:
            try:
                update_task_status(task_id, "PROPOSE", dest.name)
            except RuntimeError as exc:
                print(f"[propose] warn: status update skipped: {exc}")

    print(f"import_proposal_ok proposal={dest.name}")
    print(f"  dir={dest}")

    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    try:
        reject_path = reject(
            args.proposal or "latest",
            args.reason,
            task_id=args.task,
        )
    except RuntimeError as exc:
        print(f"reject_failed: {exc}", file=sys.stderr)
        return 1

    print(f"reject_ok proposal={reject_path.stem}")
    print(f"  reject_artifact={reject_path}")
    print("  tree=untouched (artifacts under .agentic/out/ only)")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    try:
        approval_path = approve(
            args.proposal or "latest",
            args.message or "",
            task_id=args.task,
            base_ref=args.base_ref,
            approver=args.approver,
        )
    except RuntimeError as exc:
        print(f"approve_failed: {exc}", file=sys.stderr)
        return 1

    approval = load_json(approval_path)
    print(f"approve_ok proposal={approval.get('proposal_id')}")
    print(f"  approval={approval_path}")
    print(f"  base_sha={approval.get('base_sha')}")
    print("  status=approved (diff not applied; run apply next)")
    return 0


def cmd_request_changes(args: argparse.Namespace) -> int:
    try:
        archive_dest = request_changes(
            args.proposal or "latest",
            args.notes,
            task_id=args.task,
        )
    except RuntimeError as exc:
        print(f"request_changes_failed: {exc}", file=sys.stderr)
        return 1

    print(f"request_changes_ok archive={archive_dest}")
    print("  tree=untouched (artifacts under .agentic/out/ only)")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    try:
        dest = apply(
            args.proposal or "latest",
            task_id=args.task,
            base_ref=args.base_ref,
            allow_on_main=bool(args.allow_on_main),
        )
    except RuntimeError as exc:
        print(f"apply_failed: {exc}", file=sys.stderr)
        return 1

    meta = load_json(dest / "meta.json")
    print(f"apply_ok proposal={meta.get('proposal_id')}")
    print(f"  dir={dest}")
    print(f"  branch={meta.get('applied_branch') or _current_branch()}")
    print("  next: uv run python scripts/agentic/cli.py validate")
    return 0


def register_propose_commands(sub: argparse._SubParsersAction) -> None:
    p_prop = sub.add_parser(
        "propose",
        help="Create proposal artifacts without applying changes (HITL)",
    )
    p_prop.add_argument("--task", help="Task id (e.g. ISS-014)")
    p_prop.add_argument(
        "--base-ref",
        default=DEFAULT_BASE_REF,
        help=f"Base ref for metadata (default: {DEFAULT_BASE_REF})",
    )
    p_prop.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Allow dirty working tree outside .agentic/out",
    )
    p_prop.add_argument(
        "--allow-empty",
        action="store_true",
        default=False,
        help="Allow empty proposal.diff skeleton",
    )
    p_prop.add_argument(
        "--diff-file",
        help="Use an existing unified diff file instead of capturing working tree",
    )
    p_prop.add_argument(
        "--skip-context",
        action="store_true",
        help="Skip plan/context/prompt preparation",
    )
    p_prop.add_argument(
        "--update-status",
        action="store_true",
        help="Write last_proposal_id onto the backlog task",
    )

    p_show = sub.add_parser("show-proposal", help="Show proposal artifacts")
    p_show.add_argument("--proposal", help="Proposal id, path, or folder name")
    p_show.add_argument("--task", help="Filter latest proposal by task id")
    p_show.add_argument("--latest", action="store_true", help="Show latest proposal")
    p_show.add_argument("--full", action="store_true", help="Print proposal.md body")

    p_imp = sub.add_parser(
        "import-proposal",
        help="Validate/finalize a manual proposal.diff into artifact package",
    )
    p_imp.add_argument("--task", required=False, help="Task id")
    p_imp.add_argument("--proposal", help="Proposal id, path, or folder name")
    p_imp.add_argument("--latest", action="store_true", help="Use latest proposal")
    p_imp.add_argument("--diff-file", required=False, help="Path to unified diff")
    p_imp.add_argument("--base-ref", default=DEFAULT_BASE_REF)
    p_imp.add_argument(
        "--update-status",
        action="store_true",
        help="Set backlog task status to PROPOSE",
    )


def register_hitl_commands(sub: argparse._SubParsersAction) -> None:
    """Register human-in-the-loop gate commands: reject / approve / request-changes / apply."""
    p_rej = sub.add_parser(
        "reject",
        help="Reject a proposal (writes .agentic/out/rejects/; no tree change)",
    )
    p_rej.add_argument(
        "--proposal",
        default="latest",
        help="Proposal id, path, folder name, or 'latest'",
    )
    p_rej.add_argument("--task", help="Filter latest proposal by task id")
    p_rej.add_argument("--reason", required=True, help="Rejection reason (required)")

    p_app = sub.add_parser(
        "approve",
        help="Approve a proposal (creates approval.json; checks base_sha vs origin/main)",
    )
    p_app.add_argument(
        "--proposal",
        default="latest",
        help="Proposal id, path, folder name, or 'latest'",
    )
    p_app.add_argument("--task", help="Filter latest proposal by task id")
    p_app.add_argument(
        "--message",
        default="",
        help="Approval notes / message",
    )
    p_app.add_argument(
        "--base-ref",
        default=DEFAULT_BASE_REF,
        help=f"Base ref for freshness check (default: {DEFAULT_BASE_REF})",
    )
    p_app.add_argument(
        "--approver", default=None, help="Approver identity (default: $USER)"
    )

    p_rc = sub.add_parser(
        "request-changes",
        help="Request changes: archive proposal and save notes (no tree change)",
    )
    p_rc.add_argument(
        "--proposal",
        default="latest",
        help="Proposal id, path, folder name, or 'latest'",
    )
    p_rc.add_argument("--task", help="Filter latest proposal by task id")
    p_rc.add_argument("--notes", required=True, help="Reviewer notes for re-propose")

    p_apply = sub.add_parser(
        "apply",
        help="Apply proposal.diff only with approval.json + matching base_sha (feature branch)",
    )
    p_apply.add_argument(
        "--proposal",
        default="latest",
        help="Proposal id, path, folder name, or 'latest'",
    )
    p_apply.add_argument("--task", help="Filter latest proposal by task id")
    p_apply.add_argument(
        "--base-ref",
        default=None,
        help=f"Override base ref for freshness check (default: proposal meta / {DEFAULT_BASE_REF})",
    )
    p_apply.add_argument(
        "--allow-on-main",
        action="store_true",
        help="Dangerous: allow apply on main/master (default: blocked)",
    )


PROPOSE_HANDLERS = {
    "propose": cmd_propose,
    "show-proposal": cmd_show_proposal,
    "import-proposal": cmd_import_proposal,
}

HITL_HANDLERS = {
    "reject": cmd_reject,
    "approve": cmd_approve,
    "request-changes": cmd_request_changes,
    "apply": cmd_apply,
}


__all__ = [
    "PROPOSE_HANDLERS",
    "HITL_HANDLERS",
    "register_propose_commands",
    "register_hitl_commands",
    "cmd_propose",
    "cmd_show_proposal",
    "cmd_import_proposal",
    "cmd_reject",
    "cmd_approve",
    "cmd_request_changes",
    "cmd_apply",
    "reject",
    "approve",
    "request_changes",
    "apply",
    "create_proposal_artifacts",
    "render_default_prompt",
]