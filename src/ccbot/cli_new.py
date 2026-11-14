"""Terminal client for `ccbot new [DIR]` — asks the running bot to create a session.

The bot owns all in-memory state, so this module only writes a request into the
control spool (see control.py), waits for the bot's result, and prints it. It
optionally attaches to the new tmux window afterwards.

Core responsibilities:
  - new_main(): parse arguments, send the request, print the outcome.
  - _attach_command(): build the tmux attach/switch-client command.

This module must NOT import config.py, bot.py or telegram: it runs from a plain
shell without the bot's environment. Only stdlib, control and utils are used.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import control

DEFAULT_TIMEOUT = 20.0


def _attach_command(tmux_session: str, window_id: str, inside_tmux: bool) -> list[str]:
    """Return the tmux command that shows the given window."""
    target = f"{tmux_session}:{window_id}"
    if inside_tmux:
        return ["tmux", "switch-client", "-t", target]
    return ["tmux", "attach-session", "-t", target]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccbot new",
        description="Create a Claude Code session and a Telegram topic bound to it.",
    )
    parser.add_argument("directory", nargs="?", default=".", help="project directory")
    parser.add_argument("--name", help="topic and window name")
    parser.add_argument("--resume", metavar="SESSION_ID", help="resume a session")
    parser.add_argument(
        "--user-id", type=int, help="Telegram user id (needed with several users)"
    )
    parser.add_argument(
        "--attach", action="store_true", help="attach to the new window"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"seconds to wait for the bot (default {DEFAULT_TIMEOUT:g})",
    )
    return parser


def new_main(argv: list[str]) -> int:
    """Run `ccbot new`; returns the process exit code."""
    args = _build_parser().parse_args(argv)

    path = Path(args.directory).expanduser().resolve()
    if not path.is_dir():
        print(f"Error: not a directory: {path}", file=sys.stderr)
        return 2

    params: dict[str, object] = {"cwd": str(path)}
    if args.name is not None:
        params["name"] = args.name
    if args.resume is not None:
        params["resume"] = args.resume
    if args.user_id is not None:
        params["user_id"] = args.user_id

    request_id = control.write_request("new_session", params)
    result = control.wait_for_result(request_id, args.timeout)
    if result is None:
        # Remove the request so a bot started later does not act on it.
        control.delete_request(request_id)
        print(
            f"Error: no answer from the bot within {args.timeout:g}s. "
            "Is it running? Check: systemctl --user status ccbot",
            file=sys.stderr,
        )
        return 1

    if not result.get("ok"):
        print(f"Error: {result.get('error', 'unknown error')}", file=sys.stderr)
        return 1

    window_id = result["window_id"]
    print(
        f"Session created: window {window_id} '{result['window_name']}', "
        f"Telegram topic thread {result['thread_id']}"
    )
    if result.get("link"):
        print(result["link"])
    if not result.get("hook_registered"):
        print(
            "Warning: Claude Code has not registered with the bot yet "
            "(the SessionStart hook did not report in time).",
            file=sys.stderr,
        )

    if args.attach:
        tmux_session = str(result.get("tmux_session", "ccbot"))
        cmd = _attach_command(tmux_session, window_id, "TMUX" in os.environ)
        os.execvp("tmux", cmd)
    return 0
