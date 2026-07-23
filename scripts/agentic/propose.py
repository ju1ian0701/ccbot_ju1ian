#!/usr/bin/env python3
"""Proposal subsystem for the ccbot agentic pipeline.

Adds commands:
  propose           create proposal artifacts without applying changes
  import-proposal   validate/finalize a manually created proposal.diff
  show-proposal     show proposal artifacts

This module intentionally reuses existing pipeline functions:
  prioritize.run         -- select/plan task
  render_prompt.run      -- render implement prompt
  build_context_pack.run -- build context pack
  paths.find_repo_root   -- repo root detection
"""

from __future__ import annotations

import argparse
import json
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
TMP_DIR = AGENTIC_DIR / "tmp"

CONFIG_PATH = AGENTIC_DIR / "config.json"
TASKS_PATH = AGENTIC_DIR / "backlog" / "tasks.json"

DEFAULT_BASE_REF = "origin/main"

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
        has_package = (path / "proposal.md").is_file() and (path / "meta.json").is_file()
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


def resolve_proposal_dir(proposal: str | None, latest: bool, task_id: str | None = None) -> Path:
    if latest or proposal in (None, "", "latest"):
        found = latest_proposal_dir(task_id=task_id)
        if not found:
            raise RuntimeError("No proposal artifacts found under .agentic/out/proposals/")
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
    """Capture staged + unstaged changes as a pure unified diff.

    Only ``git diff HEAD --binary`` output is allowed here — never markdown
    fences, ``#`` comments, or non-diff section markers.

    ``base_ref`` is accepted for call-site compatibility; capture is vs HEAD.
    """
    _ = base_ref
    result = run(["git", "diff", "HEAD", "--binary"], check=False)
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


def _write_temp_diff(diff_text: str) -> Path:
    fd = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".diff",
        delete=False,
    )
    path = Path(fd.name)

    fd.write(diff_text if diff_text.endswith("\n") else diff_text + "\n")
    fd.close()

    return path


def check_diff_applies(diff_text: str) -> None:
    if _is_empty_diff(diff_text):
        return

    check_pure_unified_diff(diff_text)

    tmp_path = _write_temp_diff(diff_text)

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

def diff_numstat(diff_text: str) -> list[dict]:
    """Parse ``git apply --numstat`` for a unified diff."""
    if _is_empty_diff(diff_text):
        return []

    # Reject fences/comments before git apply --numstat (corrupt patches).
    check_pure_unified_diff(diff_text)

    tmp_path = _write_temp_diff(diff_text)

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


def validate_proposal_diff(diff_text: str, *, allow_empty: bool = False) -> None:
    """Validate that proposal.diff is empty (if allowed) or a usable unified diff."""
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

    check_diff_applies(diff_text)


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

    loc_delta = sum(int(entry.get("added") or 0) + int(entry.get("deleted") or 0) for entry in entries)

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
        "acceptance_criteria": checks,
        "grep": grep,
        "tests": tests,
        "manual": manual,
        "commands": commands,
    }


def build_proposal_md(task: dict, proposal_id: str, base_sha: str, paths: list[str]) -> str:
    task_id = task_id_from(task) or "UNKNOWN"
    title = task.get("title") or ""
    problem = task.get("problem") or ""
    solution = task.get("solution") or ""
    criteria = _task_acceptance(task)
    if isinstance(criteria, list):
        crit_lines = "\n".join(f"- {c}" for c in criteria)
    else:
        crit_lines = f"- {criteria}"

    path_lines = "\n".join(f"- `{p}`" for p in paths) or "- (no paths yet — fill proposal.diff)"

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
    diff_text: str | None = None,
    allow_empty: bool = True,
) -> Path:
    config = load_config()
    task_id = task_id_from(task) or "UNKNOWN"
    stamp = utc_stamp()
    proposal_id = f"{task_id}_{stamp}"
    dest = proposal_dir_for(task_id, stamp)
    dest.mkdir(parents=True, exist_ok=True)

    base_sha = git_base_sha(base_ref)
    if diff_text is None:
        diff_text = capture_working_tree_diff(base_ref)

    if _is_empty_diff(diff_text):
        if not allow_empty:
            raise RuntimeError(
                "No changes to propose (empty unified diff). "
                "Make changes, pass --diff-file, or use --allow-empty."
            )
        # Pure empty file — no # comments / markdown (those break git apply).
        diff_text = ""

    # Format guards before any git apply --numstat / --check.
    check_pure_unified_diff(diff_text)

    numstat = diff_numstat(diff_text)
    check_diff_applies(diff_text)

    paths = [item["path"] for item in numstat] or list_changed_paths_from_diff(diff_text)
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

    write_text(dest / "proposal.diff", diff_text if diff_text.endswith("\n") else diff_text + "\n")
    write_text(dest / "proposal.md", build_proposal_md(task, proposal_id, base_sha, paths))
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
# CLI handlers
# ---------------------------------------------------------------------------


def cmd_propose(args: argparse.Namespace) -> int:
    task_id = args.task
    if not task_id:
        # Try selected-task.json
        selected = load_json(OUT_DIR / "selected-task.json")
        task_id = selected.get("id") or selected.get("task_id")
    if not task_id:
        print("propose_failed: --task is required (or run select first)", file=sys.stderr)
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

    diff_text = None
    if args.diff_file:
        diff_path = Path(args.diff_file)
        if not diff_path.is_file():
            print(f"propose_failed: diff file not found: {diff_path}", file=sys.stderr)
            return 1
        diff_text = diff_path.read_text(encoding="utf-8")

    try:
        dest = create_proposal_artifacts(
            task,
            base_ref=base_ref,
            diff_text=diff_text,
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

        diff_text = diff_path.read_text(encoding="utf-8")
        base_ref = args.base_ref or DEFAULT_BASE_REF

        try:
            dest = create_proposal_artifacts(
                task,
                base_ref=base_ref,
                diff_text=diff_text,
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

    diff_text = diff_path.read_text(encoding="utf-8")

    try:
        check_pure_unified_diff(diff_text)
        numstat = diff_numstat(diff_text)
        check_diff_applies(diff_text)
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


PROPOSE_HANDLERS = {
    "propose": cmd_propose,
    "show-proposal": cmd_show_proposal,
    "import-proposal": cmd_import_proposal,
}


__all__ = [
    "PROPOSE_HANDLERS",
    "register_propose_commands",
    "cmd_propose",
    "cmd_show_proposal",
    "cmd_import_proposal",
    "create_proposal_artifacts",
    "render_default_prompt",
]