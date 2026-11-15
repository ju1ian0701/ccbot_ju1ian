"""Claude Code hook subcommand: session tracking and hook event recording.

Called by Claude Code's hooks. SessionStart maintains a window↔session
mapping in <CCBOT_DIR>/session_map.json. Notification, Stop, StopFailure
and SessionEnd are appended as JSON lines to <CCBOT_DIR>/hook_events.jsonl
(see hook_events.py) so the bot can read session state without parsing the
terminal; they are recorded only for the session that owns the window.
Also provides `--install` to auto-configure all of these hooks in
~/.claude/settings.json.

This module must NOT import config.py (which requires TELEGRAM_BOT_TOKEN),
since hooks run inside tmux panes where bot env vars are not set.
Config directory resolution uses utils.ccbot_dir() (shared with config.py).

Key functions: hook_main() (CLI entry), _record_event(), _install_hook().
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .hook_events import (
    EVENTS_FILE_NAME,
    RECORDED_EVENTS,
    append_event,
    build_record,
)
from .utils import LOCK_EX, LOCK_UN, ccbot_dir, flock

logger = logging.getLogger(__name__)

# Validate session_id looks like a UUID
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

_CLAUDE_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

# The hook command suffix for detection
_HOOK_COMMAND_SUFFIX = "ccbot hook"

# Events installed by --install, in install order. Async events never block Claude.
_HOOK_EVENTS_SYNC = ("SessionStart", "SessionEnd")
_HOOK_EVENTS_ASYNC = ("Notification", "Stop", "StopFailure")
_HOOK_EVENTS = (*_HOOK_EVENTS_SYNC, *_HOOK_EVENTS_ASYNC)


def _find_ccbot_path() -> str:
    """Find the full path to the ccbot executable.

    Priority:
    1. shutil.which("ccbot") - if ccbot is in PATH
    2. Same directory as the Python interpreter (for venv installs)
    """
    # Try PATH first
    ccbot_path = shutil.which("ccbot")
    if ccbot_path:
        return ccbot_path

    # Fall back to the directory containing the Python interpreter
    # This handles the case where ccbot is installed in a venv
    python_dir = Path(sys.executable).parent
    ccbot_in_venv = python_dir / "ccbot"
    if ccbot_in_venv.exists():
        return str(ccbot_in_venv)

    # Last resort: assume it will be in PATH
    return "ccbot"


def _is_hook_installed(settings: dict, event: str = "SessionStart") -> bool:
    """Check if ccbot hook is already installed for *event* in the settings.

    Detects both 'ccbot hook' and full paths like '/path/to/ccbot hook'.
    """
    hooks = settings.get("hooks", {})
    event_entries = hooks.get(event, [])

    for entry in event_entries:
        if not isinstance(entry, dict):
            continue
        inner_hooks = entry.get("hooks", [])
        for h in inner_hooks:
            if not isinstance(h, dict):
                continue
            cmd = h.get("command", "")
            # Match 'ccbot hook' or paths ending with 'ccbot hook'
            if cmd == _HOOK_COMMAND_SUFFIX or cmd.endswith("/" + _HOOK_COMMAND_SUFFIX):
                return True
    return False


def _install_hook() -> int:
    """Install the ccbot hook into Claude's settings.json.

    Returns 0 on success, 1 on error.
    """
    settings_file = _CLAUDE_SETTINGS_FILE
    settings_file.parent.mkdir(parents=True, exist_ok=True)

    # Read existing settings
    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Error reading %s: %s", settings_file, e)
            print(f"Error reading {settings_file}: {e}", file=sys.stderr)
            return 1

    # Find the full path to ccbot
    ccbot_path = _find_ccbot_path()
    hook_command = f"{ccbot_path} hook"
    logger.info("Installing hook command: %s", hook_command)

    # Add an entry for each event that does not have one yet.
    # Existing entries (including other tools' hooks) are never modified.
    if "hooks" not in settings:
        settings["hooks"] = {}
    added: list[str] = []
    for event in _HOOK_EVENTS:
        if _is_hook_installed(settings, event):
            continue
        hook_config: dict = {"type": "command", "command": hook_command, "timeout": 5}
        if event in _HOOK_EVENTS_ASYNC:
            hook_config["async"] = True
        settings["hooks"].setdefault(event, []).append({"hooks": [hook_config]})
        added.append(event)

    if not added:
        logger.info("Hook already installed in %s", settings_file)
        print(f"Hook already installed in {settings_file}")
        return 0

    # Write back
    try:
        settings_file.write_text(
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        )
    except OSError as e:
        logger.error("Error writing %s: %s", settings_file, e)
        print(f"Error writing {settings_file}: {e}", file=sys.stderr)
        return 1

    logger.info("Hook installed in %s: added %s", settings_file, ", ".join(added))
    print(f"Hook installed successfully in {settings_file} (added: {', '.join(added)})")
    return 0


def _pid_alive(pid: int) -> bool:
    """Return True if a process with *pid* exists (EPERM counts as alive)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _parse_stat_ppid(text: str) -> int:
    """Extract the ppid from a /proc/<pid>/stat line (comm may hold spaces/parens)."""
    try:
        fields_after_comm = text[text.rindex(")") + 1 :].split()
        return int(fields_after_comm[1])
    except (ValueError, IndexError):
        return 0


def _ppid(pid: int) -> int:
    """Return the parent pid of *pid*, or 0 if it cannot be determined."""
    if os.path.exists("/proc/self/stat"):
        try:
            return _parse_stat_ppid(
                Path(f"/proc/{pid}/stat").read_text(errors="replace")
            )
        except OSError:
            return 0
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip() or 0)
    except (OSError, ValueError):
        return 0


def _is_ancestor(ancestor: int, pid: int) -> bool:
    """Return True if *ancestor* is a parent (not pid itself) of *pid*, within 64 hops."""
    if ancestor == pid:
        return False
    cur = pid
    for _ in range(64):
        cur = _ppid(cur)
        if cur == ancestor:
            return True
        if cur <= 1:
            return False
    return False


def _looks_like_claude(pid: int) -> bool:
    """Return True if *pid* looks like a Claude Code process (Linux /proc only)."""
    try:
        comm = Path(f"/proc/{pid}/comm").read_text(errors="replace").strip()
        if comm == "claude":
            return True
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        args = [a.decode("utf-8", errors="replace") for a in argv]
    except OSError:
        return False
    if args and os.path.basename(args[0]) == "claude":
        return True
    return any(a.endswith("cli.js") and "claude" in a for a in args[:2])


def _count_claude_ancestors(pid: int) -> int:
    """Count Claude-like processes among *pid* and its ancestors (max 64 steps)."""
    count = 0
    cur = pid
    for _ in range(64):
        if cur <= 1:
            break
        if _looks_like_claude(cur):
            count += 1
        cur = _ppid(cur)
    return count


def _resolve_window(pane_id: str) -> tuple[str, str, str] | None:
    """Return (tmux_session_name, window_id, window_name) for *pane_id*, or None."""
    try:
        result = subprocess.run(
            [
                "tmux",
                "display-message",
                "-t",
                pane_id,
                "-p",
                "#{session_name}:#{window_id}:#{window_name}",
            ],
            capture_output=True,
            text=True,
        )
    except OSError as e:
        logger.warning("Failed to run tmux display-message (pane=%s): %s", pane_id, e)
        return None
    raw_output = result.stdout.strip()
    # Expected format: "session_name:@id:window_name"
    parts = raw_output.split(":", 2)
    if len(parts) < 3:
        logger.warning(
            "Failed to parse session:window_id:window_name from tmux (pane=%s, output=%s)",
            pane_id,
            raw_output,
        )
        return None
    tmux_session_name, window_id, window_name = parts
    return tmux_session_name, window_id, window_name


def _read_session_map(map_file: Path) -> dict:
    """Read session_map.json without locking; missing or corrupt file gives {}."""
    try:
        data = json.loads(map_file.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _record_event(payload: dict) -> None:
    """Append a Notification/Stop/StopFailure/SessionEnd event to hook_events.jsonl.

    Only records events for the session that owns the window, so a nested
    Claude (e.g. `claude -p` run from the window's Bash tool, which inherits
    TMUX_PANE) cannot attribute its events to the window's session.
    Never raises.
    """
    event = payload.get("hook_event_name", "")
    session_id = payload.get("session_id", "")

    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("sdk"):
        logger.debug("headless session, not recording %s event", event)
        return

    pane_id = os.environ.get("TMUX_PANE", "")
    if not pane_id:
        logger.debug("TMUX_PANE not set, not recording %s event", event)
        return

    resolved = _resolve_window(pane_id)
    if resolved is None:
        return
    tmux_session_name, window_id, _window_name = resolved
    session_window_key = f"{tmux_session_name}:{window_id}"

    base_dir = ccbot_dir()
    session_map = _read_session_map(base_dir / "session_map.json")
    existing = session_map.get(session_window_key)
    owner_session_id = (
        existing.get("session_id") if isinstance(existing, dict) else None
    )
    if owner_session_id != session_id:
        logger.debug(
            "%s event for %s not owned by window session (session_id=%s), ignoring",
            event,
            session_window_key,
            session_id,
        )
        return

    try:
        append_event(
            base_dir / EVENTS_FILE_NAME,
            build_record(payload, session_window_key),
        )
    except OSError as e:
        logger.error("Failed to append hook event: %s", e)


def hook_main() -> None:
    """Process a Claude Code hook event from stdin, or install the hook."""
    # Configure logging for the hook subprocess (main.py logging doesn't apply here)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.DEBUG,
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        prog="ccbot hook",
        description="Claude Code session tracking hook",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Install the hook into ~/.claude/settings.json",
    )
    # Parse only known args to avoid conflicts with stdin JSON
    args, _ = parser.parse_known_args(sys.argv[2:])

    if args.install:
        logger.info("Hook install requested")
        sys.exit(_install_hook())

    # Normal hook processing: read JSON from stdin
    logger.debug("Processing hook event from stdin")
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse stdin JSON: %s", e)
        return

    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd", "")
    event = payload.get("hook_event_name", "")

    if not session_id or not event:
        logger.debug("Empty session_id or event, ignoring")
        return

    # Validate session_id format
    if not _UUID_RE.match(session_id):
        logger.warning("Invalid session_id format: %s", session_id)
        return

    # Validate cwd is an absolute path (if provided)
    if cwd and not os.path.isabs(cwd):
        logger.warning("cwd is not absolute: %s", cwd)
        return

    if event in RECORDED_EVENTS:
        _record_event(payload)
        return

    if event != "SessionStart":
        logger.debug("Ignoring non-SessionStart event: %s", event)
        return

    # Headless (SDK / `claude -p`) sessions never own a window.
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    if entrypoint.startswith("sdk"):
        logger.debug("headless session, not registering (session_id=%s)", session_id)
        return

    # PID of the claude process that launched this hook (newer Claude Code only).
    try:
        claude_pid = int(os.environ.get("CLAUDE_PID") or 0)
    except ValueError:
        claude_pid = 0

    # Get tmux session:window key for the pane running this hook.
    # TMUX_PANE is set by tmux for every process inside a pane.
    pane_id = os.environ.get("TMUX_PANE", "")
    if not pane_id:
        # E.g. SessionStart:fork runs without TMUX_PANE; the monitor follows the
        # fork via the transcript's continued-in record. Log enough to diagnose.
        logger.warning(
            "TMUX_PANE not set, cannot determine window "
            "(session_id=%s, source=%s, cwd=%s, payload_keys=%s)",
            session_id,
            payload.get("source", ""),
            cwd,
            sorted(payload),
        )
        return

    resolved = _resolve_window(pane_id)
    if resolved is None:
        return
    tmux_session_name, window_id, window_name = resolved
    # Key uses window_id for uniqueness
    session_window_key = f"{tmux_session_name}:{window_id}"

    logger.debug(
        "tmux key=%s, window_name=%s, session_id=%s, cwd=%s",
        session_window_key,
        window_name,
        session_id,
        cwd,
    )

    # Read-modify-write with file locking to prevent concurrent hook races
    map_file = ccbot_dir() / "session_map.json"
    map_file.parent.mkdir(parents=True, exist_ok=True)

    lock_path = map_file.with_suffix(".lock")
    try:
        with open(lock_path, "w") as lock_f:
            flock(lock_f, LOCK_EX)
            logger.debug("Acquired lock on %s", lock_path)
            try:
                session_map: dict[str, dict] = {}
                if map_file.exists():
                    try:
                        session_map = json.loads(map_file.read_text())
                    except (json.JSONDecodeError, OSError):
                        logger.warning(
                            "Failed to read existing session_map, starting fresh"
                        )

                # Nested Claude (e.g. `claude -p` run from the window's Bash tool)
                # inherits TMUX_PANE; it must not take over the window's entry.
                existing = session_map.get(session_window_key)
                owner = (
                    existing.get("claude_pid") if isinstance(existing, dict) else None
                )
                if claude_pid:
                    if (
                        isinstance(owner, int)
                        and owner != claude_pid
                        and _pid_alive(owner)
                        and _is_ancestor(owner, claude_pid)
                    ):
                        logger.info(
                            "nested Claude session ignored: %s session_id=%s "
                            "claude_pid=%s is a descendant of owner pid %s",
                            session_window_key,
                            session_id,
                            claude_pid,
                            owner,
                        )
                        return
                    # No recorded owner (entry from an older hook, or none yet):
                    # fall back to the process-name heuristic.
                    if (
                        not isinstance(owner, int)
                        and _count_claude_ancestors(claude_pid) >= 2
                    ):
                        logger.info(
                            "nested Claude session ignored: %s session_id=%s "
                            "claude_pid=%s has a claude ancestor (no recorded owner)",
                            session_window_key,
                            session_id,
                            claude_pid,
                        )
                        return
                elif _count_claude_ancestors(os.getppid()) >= 2:
                    logger.info(
                        "nested Claude session ignored: %s session_id=%s "
                        "(no CLAUDE_PID, claude ancestors >= 2)",
                        session_window_key,
                        session_id,
                    )
                    return

                entry: dict = {
                    "session_id": session_id,
                    "cwd": cwd,
                    "window_name": window_name,
                }
                if claude_pid:
                    entry["claude_pid"] = claude_pid
                session_map[session_window_key] = entry

                # Clean up old-format key ("session:window_name") if it exists.
                # Previous versions keyed by window_name instead of window_id.
                old_key = f"{tmux_session_name}:{window_name}"
                if old_key != session_window_key and old_key in session_map:
                    del session_map[old_key]
                    logger.info("Removed old-format session_map key: %s", old_key)

                from .utils import atomic_write_json

                atomic_write_json(map_file, session_map)
                logger.info(
                    "Updated session_map: %s -> session_id=%s, cwd=%s",
                    session_window_key,
                    session_id,
                    cwd,
                )
            finally:
                flock(lock_f, LOCK_UN)
    except OSError as e:
        logger.error("Failed to write session_map: %s", e)
