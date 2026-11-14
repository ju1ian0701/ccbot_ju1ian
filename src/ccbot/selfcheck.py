"""`ccbot selfcheck` — verify the terminal parser against the installed Claude Code.

Starts a real Claude Code inside a throw-away tmux server (its own socket, never
the bot's), drives a few screens (startup, /usage, /model and optionally one
prompt for the status line) and reports which parser functions still recognize
them. Meant to be run by hand after each Claude Code update.

Core responsibilities:
  - selfcheck_main(): CLI entry point; starts Claude, runs the checks, tears down.
  - run_checks(): the checks themselves, written against a small tmux interface
    so tests can inject a fake screen.
  - Tmux: thin wrapper over `tmux -L ccbot-selfcheck`; never touches the default
    server that runs the live bot.
  - child_env() / resolve_command(): environment and command for the child Claude.
  - format_report() / exit_code(): human-readable table and exit status.

This module must NOT import config.py: it runs without the bot's environment.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values

from .health import parse_version
from .terminal_parser import (
    TESTED_CLAUDE_CODE,
    extract_interactive_content,
    has_chrome,
    parse_status_line,
    parse_usage_output,
)
from .utils import ccbot_dir

SOCKET = "ccbot-selfcheck"
SESSION = "check"
DEFAULT_TIMEOUT = 30.0
TEMP_PREFIX = "ccbot-selfcheck-"

# Variables that would make the child Claude think it runs nested inside Claude
# Code or inside tmux. CLAUDE_CONFIG_DIR and CLAUDE_COMMAND are kept.
_KEPT_CLAUDE_VARS = frozenset({"CLAUDE_CONFIG_DIR", "CLAUDE_COMMAND"})
_DROPPED_VARS = frozenset({"TMUX", "TMUX_PANE"})


# ── Pure helpers ─────────────────────────────────────────────────────────


def resolve_command(
    cli_value: str | None, env: Mapping[str, str], dotenv_path: Path
) -> str:
    """Command used to start Claude: --command, then env, then .env file, then `claude`."""
    if cli_value and cli_value.strip():
        return cli_value.strip()
    env_value = (env.get("CLAUDE_COMMAND") or "").strip()
    if env_value:
        return env_value
    file_value = (dotenv_values(dotenv_path).get("CLAUDE_COMMAND") or "").strip()
    if file_value:
        return file_value
    return "claude"


def child_env(env: Mapping[str, str], ccbot_dir_path: str) -> dict[str, str]:
    """Environment for the child Claude: no nesting markers, CCBOT_DIR redirected."""
    result: dict[str, str] = {}
    for name, value in env.items():
        if name in _DROPPED_VARS:
            continue
        if name.startswith("CLAUDE") and name not in _KEPT_CLAUDE_VARS:
            continue
        result[name] = value
    result["CCBOT_DIR"] = ccbot_dir_path
    return result


def trust_dialog_keys(pane: str) -> list[str] | None:
    """Keys that answer the workspace-trust dialog with "Yes", or None.

    Moves the ``❯`` cursor onto the Yes option and presses Enter. Returns None when
    the dialog is not shown or its options cannot be understood.
    """
    lines = pane.split("\n")
    if not any("trust this folder" in line for line in lines):
        return None

    yes_re = re.compile(r"^\s*❯?\s*(\d+\.\s*)?Yes\b")
    cursor_re = re.compile(r"^\s*❯")

    yes_idx = next((i for i, line in enumerate(lines) if yes_re.match(line)), None)
    cursor_idx = next(
        (i for i, line in enumerate(lines) if cursor_re.match(line)), None
    )
    if yes_idx is None or cursor_idx is None:
        return None

    if yes_idx == cursor_idx:
        return ["Enter"]
    if yes_idx > cursor_idx:
        return ["Down"] * (yes_idx - cursor_idx) + ["Enter"]
    return ["Up"] * (cursor_idx - yes_idx) + ["Enter"]


def wait_for(
    capture: Callable[[], str],
    predicate: Callable[[str], bool],
    timeout: float,
    interval: float = 0.5,
) -> str | None:
    """Poll `capture` until `predicate` holds; return that pane, or None on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        pane = capture()
        if predicate(pane):
            return pane
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def submit(tmux: _Pane, text: str) -> None:
    """Type `text` and press Enter; Claude's TUI needs the pause in between."""
    tmux.send_text(text)
    time.sleep(0.5)
    tmux.send_key("Enter")


# Teardown re-checks for Claude's transcript dir this many times, this far apart.
_TRANSCRIPT_CLEANUP_PASSES = 3
_TRANSCRIPT_CLEANUP_DELAY = 0.7


def remove_transcript_dir(workdir: Path) -> None:
    """Delete Claude's per-project transcript dir for a selfcheck workdir.

    Refuses any workdir whose name lacks the selfcheck prefix, so a wrong path
    can never delete a real project's transcripts.
    """
    if not workdir.name.startswith(TEMP_PREFIX):
        raise ValueError(f"refusing to remove transcripts for {workdir}")
    project = (
        Path.home()
        / ".claude"
        / "projects"
        / re.sub(r"[^A-Za-z0-9]", "-", str(workdir))
    )
    if project.is_dir():
        shutil.rmtree(project, ignore_errors=True)


# ── Results ──────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    """Outcome of one check: status is PASS, FAIL, WARN or SKIP."""

    name: str
    status: str  # "PASS" | "FAIL" | "WARN" | "SKIP"
    detail: str = ""


def exit_code(results: Sequence[CheckResult]) -> int:
    """1 if any check failed, else 0 (WARN and SKIP do not fail the run)."""
    return 1 if any(r.status == "FAIL" for r in results) else 0


def format_report(
    results: Sequence[CheckResult], claude_version: str, tested_version: str
) -> str:
    """Aligned table of check results, preceded by versions and ending in RESULT."""
    lines = [f"Claude Code version: {claude_version} (tested with {tested_version})"]
    name_w = max([len("CHECK")] + [len(r.name) for r in results])
    lines.append(f"{'CHECK':<{name_w}}  {'STATUS':<6}  DETAIL")
    for r in results:
        lines.append(f"{r.name:<{name_w}}  {r.status:<6}  {r.detail}".rstrip())
    lines.append("")
    lines.append("RESULT: FAILED" if exit_code(results) else "RESULT: OK")
    return "\n".join(lines)


# ── tmux wrapper ─────────────────────────────────────────────────────────


class _Pane(Protocol):
    """The tmux operations the checks need (Tmux, or a fake in tests)."""

    def capture(self) -> str: ...
    def send_text(self, text: str) -> None: ...
    def send_key(self, name: str) -> None: ...


class Tmux:
    """Every call goes to the dedicated `ccbot-selfcheck` tmux server."""

    def __init__(self) -> None:
        self._env: dict[str, str] | None = None

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["tmux", "-L", SOCKET, *args],
            capture_output=True,
            text=True,
            env=self._env,
        )

    def start(
        self,
        workdir: str,
        command: str,
        env: Mapping[str, str],
        width: int = 120,
        height: int = 45,
    ) -> None:
        """Start a detached session running `command`; raises RuntimeError on failure."""
        self._env = dict(env)
        proc = self._run(
            "new-session",
            "-d",
            "-s",
            SESSION,
            "-x",
            str(width),
            "-y",
            str(height),
            "-c",
            workdir,
            command,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "tmux new-session failed")

    def capture(self) -> str:
        """Visible text of the session's pane ("" if the session is gone)."""
        proc = self._run("capture-pane", "-p", "-t", SESSION)
        return proc.stdout if proc.returncode == 0 else ""

    def send_text(self, text: str) -> None:
        """Type literal text into the pane."""
        self._run("send-keys", "-t", SESSION, "-l", text)

    def send_key(self, name: str) -> None:
        """Press a named key (Enter, Escape, Up, Down, ...)."""
        self._run("send-keys", "-t", SESSION, name)

    def kill(self) -> None:
        """Stop the selfcheck tmux server; errors (e.g. no server) are ignored."""
        try:
            self._run("kill-server")
        except OSError:
            pass


# ── Checks ───────────────────────────────────────────────────────────────


class _Checker:
    """Runs the checks in order against one tmux session."""

    def __init__(self, tmux: _Pane, timeout: float, save_dir: Path | None) -> None:
        self.tmux = tmux
        self.timeout = timeout
        self.save_dir = save_dir
        self.results: list[CheckResult] = []
        self.last_pane = ""
        self._trust_polls = 0
        self._trust_sends = 0

    def capture(self) -> str:
        """Capture the pane; answer the trust dialog once per appearance."""
        pane = self.tmux.capture()
        self.last_pane = pane
        keys = trust_dialog_keys(pane)
        if keys is None:
            self._trust_polls = 0
            self._trust_sends = 0
            return pane
        # Keys can be swallowed if the TUI draws the dialog before it reads
        # input, so re-send them while the dialog is still up (about every 2 s,
        # at most three times per appearance).
        if self._trust_polls % 4 == 0 and self._trust_sends < 3:
            self._trust_sends += 1
            for key in keys:
                self.tmux.send_key(key)
        self._trust_polls += 1
        return pane

    def record(
        self, name: str, status: str, detail: str, pane: str | None = None
    ) -> None:
        self.results.append(CheckResult(name, status, detail))
        if pane is not None and self.save_dir is not None:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            (self.save_dir / f"{name}.txt").write_text(pane, encoding="utf-8")

    def close_dialog(self) -> None:
        """Escape out of a modal and wait for the prompt chrome (Escape once more if needed)."""
        self.tmux.send_key("Escape")
        if wait_for(self.capture, has_chrome, self.timeout) is None:
            self.tmux.send_key("Escape")
            wait_for(self.capture, has_chrome, self.timeout)

    def startup(self) -> bool:
        pane = wait_for(self.capture, has_chrome, self.timeout)
        if pane is not None:
            self.record("startup", "PASS", "Claude Code prompt visible", pane)
            return True
        tail = [line for line in self.last_pane.split("\n") if line.strip()][-5:]
        detail = " | ".join(tail) or "(empty pane)"
        self.record("startup", "FAIL", detail, self.last_pane)
        return False

    def usage(self) -> None:
        submit(self.tmux, "/usage")
        pane = wait_for(
            self.capture, lambda p: parse_usage_output(p) is not None, self.timeout
        )
        if pane is not None:
            info = parse_usage_output(pane)
            count = len(info.parsed_lines) if info else 0
            self.record("usage", "PASS", f"{count} lines parsed", pane)
        else:
            pane = self.last_pane
            dialog = extract_interactive_content(pane)
            if dialog is not None:
                self.record(
                    "usage",
                    "WARN",
                    f"dialog '{dialog.name}' shown but usage not parsed",
                    pane,
                )
            else:
                self.record("usage", "FAIL", "usage screen not recognized", pane)
        self.close_dialog()

    def model(self) -> None:
        submit(self.tmux, "/model")
        pane = wait_for(
            self.capture,
            lambda p: extract_interactive_content(p) is not None,
            self.timeout,
        )
        if pane is None:
            self.record("model", "FAIL", "model picker not recognized", self.last_pane)
        else:
            dialog = extract_interactive_content(pane)
            name = dialog.name if dialog else ""
            if name == "Settings":
                self.record("model", "PASS", "model picker recognized", pane)
            else:
                self.record("model", "WARN", name, pane)
        self.close_dialog()

    def status(self, with_prompt: bool) -> None:
        if not with_prompt:
            self.record("status", "SKIP", "use --with-prompt")
            return
        submit(self.tmux, "Reply with exactly: ok")
        pane = wait_for(
            self.capture,
            lambda p: parse_status_line(p) is not None,
            self.timeout,
            interval=0.2,
        )
        if pane is None:
            self.record("status", "FAIL", "status line not seen", self.last_pane)
            return
        text = parse_status_line(pane) or ""
        self.record("status", "PASS", f"status line: {text}", pane)
        wait_for(self.capture, lambda p: parse_status_line(p) is None, self.timeout)

    def run(self, with_prompt: bool) -> list[CheckResult]:
        if not self.startup():
            for name in ("usage", "model", "status"):
                self.record(name, "SKIP", "startup failed")
            return self.results
        self.usage()
        self.model()
        self.status(with_prompt)
        return self.results


def run_checks(
    tmux: _Pane,
    *,
    timeout: float,
    with_prompt: bool,
    save_dir: Path | None = None,
) -> list[CheckResult]:
    """Run all checks against an already started Claude session."""
    return _Checker(tmux, timeout, save_dir).run(with_prompt)


# ── CLI ──────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccbot selfcheck",
        description="Check the terminal parser against the installed Claude Code.",
    )
    parser.add_argument(
        "--command",
        metavar="CMD",
        help="command that starts Claude (default: CLAUDE_COMMAND, else 'claude')",
    )
    parser.add_argument(
        "--save-dir", metavar="DIR", help="write each captured pane as <check>.txt"
    )
    parser.add_argument(
        "--with-prompt",
        action="store_true",
        help="also send one prompt (spends model tokens) to check the status line",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"seconds to wait per check (default {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="do not tear down the tmux server and temp dirs (for debugging)",
    )
    return parser


def claude_version() -> str:
    """Output of `claude --version` as "N.N.N", or "unknown"."""
    try:
        proc = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    parsed = parse_version(proc.stdout) if proc.returncode == 0 else None
    return ".".join(str(part) for part in parsed) if parsed else "unknown"


def _teardown(tmux: Tmux, workdir: Path, ccbot_tmp: Path) -> None:
    tmux.kill()
    shutil.rmtree(workdir, ignore_errors=True)
    shutil.rmtree(ccbot_tmp, ignore_errors=True)
    # Claude flushes its transcript while it dies from the kill above, so the
    # project dir can (re)appear a moment later: remove it, wait, remove again.
    for _ in range(_TRANSCRIPT_CLEANUP_PASSES):
        remove_transcript_dir(workdir)
        time.sleep(_TRANSCRIPT_CLEANUP_DELAY)
    remove_transcript_dir(workdir)


def selfcheck_main(argv: list[str]) -> int:
    """Run `ccbot selfcheck`; returns the process exit code."""
    args = _build_parser().parse_args(argv)
    command = resolve_command(args.command, os.environ, ccbot_dir() / ".env")
    save_dir = Path(args.save_dir) if args.save_dir else None

    workdir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    ccbot_tmp = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    tmux = Tmux()
    try:
        try:
            tmux.kill()
            tmux.start(str(workdir), command, child_env(os.environ, str(ccbot_tmp)))
        except (OSError, RuntimeError) as exc:
            results = [CheckResult("startup", "FAIL", f"could not start: {exc}")]
            results += [
                CheckResult(name, "SKIP", "startup failed")
                for name in ("usage", "model", "status")
            ]
        else:
            results = run_checks(
                tmux,
                timeout=args.timeout,
                with_prompt=args.with_prompt,
                save_dir=save_dir,
            )
    finally:
        if args.keep:
            print(
                f"Left running (debugging). Attach with: tmux -L {SOCKET} attach\n"
                f"Temp dirs kept: {workdir} {ccbot_tmp}"
            )
        else:
            _teardown(tmux, workdir, ccbot_tmp)

    print(format_report(results, claude_version(), TESTED_CLAUDE_CODE))
    return exit_code(results)
