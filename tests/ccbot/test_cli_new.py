"""Tests for `ccbot new` — the terminal client, its attach helper, and dispatch."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import ccbot
from ccbot import cli_new, control


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """Point the CLI at a temp CCBOT_DIR and return that directory."""
    monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "proj"
    path.mkdir()
    return path


@pytest.fixture
def answer(monkeypatch, spool):
    """Make the 'bot' answer the first pending request with `result` during the wait.

    Returns a list that collects the request params seen by the fake bot.
    """
    seen: list[dict] = []

    def install(result: dict) -> list[dict]:
        def fake_sleep(_seconds):
            for rid, req in control.list_requests(base=spool):
                seen.append(req["params"])
                control.write_result(rid, result, base=spool)
                control.delete_request(rid, base=spool)

        monkeypatch.setattr(time, "sleep", fake_sleep)
        return seen

    return install


OK_RESULT = {
    "ok": True,
    "window_id": "@5",
    "window_name": "proj",
    "thread_id": 77,
    "tmux_session": "ccbot",
    "hook_registered": True,
    "link": "https://t.me/c/3978703836/77",
}


def test_non_directory_returns_2_and_writes_nothing(spool, tmp_path, capsys):
    missing = tmp_path / "nope"

    assert cli_new.new_main([str(missing)]) == 2

    assert "Error: not a directory" in capsys.readouterr().err
    assert control.list_requests(base=spool) == []
    assert not list(control.control_dir(spool).glob("*.json"))


def test_success_prints_window_and_link(spool, project, answer, capsys):
    answer(dict(OK_RESULT))

    assert cli_new.new_main([str(project)]) == 0

    out = capsys.readouterr().out
    assert "window @5 'proj'" in out
    assert "Telegram topic thread 77" in out
    assert "https://t.me/c/3978703836/77" in out.splitlines()


def test_unregistered_hook_prints_warning(spool, project, answer, capsys):
    answer({**OK_RESULT, "hook_registered": False})

    assert cli_new.new_main([str(project)]) == 0

    assert "has not registered" in capsys.readouterr().err


def test_error_result_returns_1(spool, project, answer, capsys):
    answer({"ok": False, "error": "boom"})

    assert cli_new.new_main([str(project)]) == 1

    assert "Error: boom" in capsys.readouterr().err


def test_timeout_returns_1_and_removes_request(spool, project, monkeypatch, capsys):
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    assert cli_new.new_main([str(project), "--timeout", "0"]) == 1

    assert control.list_requests(base=spool) == []
    err = capsys.readouterr().err
    assert "no answer from the bot within 0s" in err
    assert "systemctl --user status ccbot" in err


def test_only_given_params_are_sent(spool, project, answer):
    seen = answer(dict(OK_RESULT))

    cli_new.new_main([str(project)])
    assert seen == [{"cwd": str(project)}]

    seen.clear()
    cli_new.new_main(
        [
            str(project),
            "--name",
            "api",
            "--resume",
            "12345678-1234-1234-1234-123456789abc",
            "--user-id",
            "111",
        ]
    )
    assert seen == [
        {
            "cwd": str(project),
            "name": "api",
            "resume": "12345678-1234-1234-1234-123456789abc",
            "user_id": 111,
        }
    ]


def test_relative_directory_is_resolved(spool, project, answer, monkeypatch):
    seen = answer(dict(OK_RESULT))
    monkeypatch.chdir(project.parent)

    cli_new.new_main(["proj"])

    assert seen == [{"cwd": str(project.resolve())}]


@pytest.mark.parametrize(
    ("inside", "expected"),
    [
        (True, ["tmux", "switch-client", "-t", "ccbot:@5"]),
        (False, ["tmux", "attach-session", "-t", "ccbot:@5"]),
    ],
)
def test_attach_command(inside, expected):
    assert cli_new._attach_command("ccbot", "@5", inside) == expected


def test_attach_execs_tmux_outside_tmux(spool, project, answer, monkeypatch):
    answer(dict(OK_RESULT))
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        cli_new.os, "execvp", lambda file, args: calls.append((file, args))
    )
    monkeypatch.delenv("TMUX", raising=False)

    assert cli_new.new_main([str(project), "--attach"]) == 0

    assert calls == [("tmux", ["tmux", "attach-session", "-t", "ccbot:@5"])]


def test_attach_switches_client_inside_tmux(spool, project, answer, monkeypatch):
    answer(dict(OK_RESULT))
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        cli_new.os, "execvp", lambda file, args: calls.append((file, args))
    )
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1,0")

    cli_new.new_main([str(project), "--attach"])

    assert calls == [("tmux", ["tmux", "switch-client", "-t", "ccbot:@5"])]


def test_no_attach_on_error(spool, project, answer, monkeypatch):
    answer({"ok": False, "error": "boom"})
    calls: list[object] = []
    monkeypatch.setattr(cli_new.os, "execvp", lambda *a: calls.append(a))

    assert cli_new.new_main([str(project), "--attach"]) == 1
    assert calls == []


def test_main_dispatches_ccbot_new(monkeypatch, tmp_path):
    from ccbot import main as main_module

    received: list[list[str]] = []

    def fake_new_main(argv):
        received.append(argv)
        return 3

    monkeypatch.setattr(cli_new, "new_main", fake_new_main)
    monkeypatch.setattr(sys, "argv", ["ccbot", "new", str(tmp_path), "--name", "x"])

    with pytest.raises(SystemExit) as exc:
        main_module.main()

    assert exc.value.code == 3
    assert received == [[str(tmp_path), "--name", "x"]]


def test_cli_and_control_import_without_bot_config(tmp_path):
    src = Path(ccbot.__file__).resolve().parent.parent
    env = {"PYTHONPATH": str(src), "PATH": os.environ.get("PATH", "")}
    code = (
        "import sys; import ccbot.cli_new, ccbot.control; "
        "assert 'ccbot.config' not in sys.modules, 'config imported'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
