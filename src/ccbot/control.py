"""File-based request/result channel between the `ccbot new` CLI and the bot.

The running bot owns all in-memory state, so terminal commands must not touch
it directly. Instead the CLI drops a request file into <CCBOT_DIR>/control/,
the bot executes it and writes a result file next to it, and the CLI reads
that result.

Core responsibilities:
  - write_request() / list_requests(): CLI writes, bot polls pending requests.
  - write_result() / read_result() / wait_for_result(): bot answers, CLI waits.
  - delete_request() / cleanup_stale(): remove consumed or abandoned files.

File layout: <id>.json (request) and <id>.result.json (result), where <id> is
a uuid4 hex string. Malformed requests are answered with an error and removed.

This module must NOT import config.py (the CLI runs without the bot's env).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .utils import atomic_write_json, ccbot_dir

CONTROL_DIR_NAME = "control"
RESULT_SUFFIX = ".result.json"
_REQUEST_ID_RE = re.compile(r"[0-9a-f]{32}")
_MALFORMED_RESULT: dict[str, Any] = {"ok": False, "error": "malformed request"}
_EXPIRED_RESULT: dict[str, Any] = {"ok": False, "error": "request expired"}

# A request older than this is never executed: its CLI has long given up, and a
# bot that starts later must not create a session nobody is waiting for.
MAX_REQUEST_AGE = 120.0


def control_dir(base: Path | None = None) -> Path:
    """Return <base>/control (base defaults to ccbot_dir()), created with mode 0o700."""
    root = base if base is not None else ccbot_dir()
    path = root / CONTROL_DIR_NAME
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _request_path(directory: Path, request_id: str) -> Path:
    return directory / f"{request_id}.json"


def _result_path(directory: Path, request_id: str) -> Path:
    return directory / f"{request_id}{RESULT_SUFFIX}"


def write_request(action: str, params: dict, *, base: Path | None = None) -> str:
    """Write a new request file atomically and return its id."""
    request_id = uuid.uuid4().hex
    data = {"action": action, "params": params, "created": time.time()}
    atomic_write_json(_request_path(control_dir(base), request_id), data)
    return request_id


def _load_json(path: Path) -> Any:
    """Parse a JSON file; raises OSError or ValueError on failure."""
    with open(path, encoding="utf-8") as f:
        return json.loads(f.read())


def list_requests(
    *, base: Path | None = None, max_age: float = MAX_REQUEST_AGE
) -> list[tuple[str, dict]]:
    """Return pending (id, request) pairs sorted by file name.

    Malformed requests, and requests created more than ``max_age`` seconds
    ago, get an error result and their file is removed; they are not returned.
    """
    directory = control_dir(base)
    pending: list[tuple[str, dict]] = []
    for path in sorted(directory.glob("*.json")):
        if path.name.endswith(RESULT_SUFFIX):
            continue
        request_id = path.name[: -len(".json")]
        if not _REQUEST_ID_RE.fullmatch(request_id):
            continue
        try:
            req = _load_json(path)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            req = None

        params = req.get("params", {}) if isinstance(req, dict) else None
        if (
            not isinstance(req, dict)
            or not isinstance(req.get("action"), str)
            or not isinstance(params, dict)
        ):
            write_result(request_id, dict(_MALFORMED_RESULT), base=base)
            delete_request(request_id, base=base)
            continue
        created = req.get("created")
        if (
            isinstance(created, bool)
            or not isinstance(created, (int, float))
            or time.time() - created > max_age
        ):
            write_result(request_id, dict(_EXPIRED_RESULT), base=base)
            delete_request(request_id, base=base)
            continue
        pending.append((request_id, {**req, "params": params}))
    return pending


def write_result(request_id: str, result: dict, *, base: Path | None = None) -> None:
    """Write the result file for a request atomically."""
    atomic_write_json(_result_path(control_dir(base), request_id), result)


def delete_request(request_id: str, *, base: Path | None = None) -> None:
    """Delete the request file; a missing file is not an error."""
    _request_path(control_dir(base), request_id).unlink(missing_ok=True)


def read_result(request_id: str, *, base: Path | None = None) -> dict | None:
    """Return the parsed result for a request, or None if not (yet) readable."""
    path = _result_path(control_dir(base), request_id)
    try:
        data = _load_json(path)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def wait_for_result(
    request_id: str,
    timeout: float,
    *,
    interval: float = 0.3,
    base: Path | None = None,
) -> dict | None:
    """Poll for the result file; return it (and delete it) or None on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        result = read_result(request_id, base=base)
        if result is not None:
            _result_path(control_dir(base), request_id).unlink(missing_ok=True)
            return result
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def cleanup_stale(*, max_age: float = 3600.0, base: Path | None = None) -> int:
    """Delete request and result files older than max_age seconds (by mtime).

    Returns the number of files removed. Files that vanish concurrently are skipped.
    """
    directory = control_dir(base)
    now = time.time()
    removed = 0
    for path in directory.glob("*.json"):
        try:
            if now - path.stat().st_mtime <= max_age:
                continue
            path.unlink()
        except OSError:
            continue
        removed += 1
    return removed
