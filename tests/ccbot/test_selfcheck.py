"""Tests for `ccbot selfcheck` — pure helpers, a fake tmux, and the check flow.

No real tmux or claude is started: the check flow runs against FakeTmux, a tiny
state machine that answers key presses with scripted screens, and time is faked.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import ccbot
from ccbot import main as main_module
from ccbot import selfcheck
from ccbot.selfcheck import (
    CheckResult,
    Tmux,
    child_env,
    exit_code,
    format_report,
    remove_transcript_dir,
    resolve_command,
    run_checks,
    trust_dialog_keys,
    wait_for,
)

CHROME = (
    "Welcome back\n"
    "────────────────────────────────────────\n"
    "❯ \n"
    "────────────────────────────────────────\n"
    "  [Opus 4.6] Context: 3%"
)
SHELL = "user@host:~$ "
USAGE = (
    " Settings:  Status   Config   Usage\n"
    " Current session\n"
    " █████▋   38% used\n"
    " Resets 3pm\n"
    " Esc to cancel"
)
MODEL = " Select model\n ❯ 1. Default\n   2. Opus\n Enter to confirm · Esc to exit"
TRUST = (
    " Accessing workspace:\n"
    " /tmp/x/work\n"
    " Quick safety check: Is this a project you created or one you trust? (Like your own code, a well-known open\n"
    " source project, or work from your team). If not, take a moment to review what's in this folder first.\n"
    " Claude Code'll be able to read, edit, and execute files here.\n"
    " Security guide\n"
    " ❯ No, exit\n"
    "   Yes, I trust this folder\n"
    " Enter to confirm · Esc to cancel"
)
STATUS = "Welcome back\n✻ Working on it\n" + CHROME.split("\n", 1)[1]


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def clock(monkeypatch):
    """Fake time: sleep advances the clock instead of waiting."""
    now = [0.0]

    def fake_sleep(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(selfcheck.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(selfcheck.time, "sleep", fake_sleep)
    return now


class FakeTmux:
    """Answers key presses like a tiny Claude Code: Enter after a known prompt
    switches to the scripted screen, Escape returns to the prompt."""

    def __init__(
        self,
        screen: str = CHROME,
        on_prompt: dict[str, str] | None = None,
    ) -> None:
        self.screen = screen
        self.on_prompt = on_prompt or {}
        self.pending: str | None = None
        self.sent: list[tuple[str, str]] = []

    def capture(self) -> str:
        return self.screen

    def send_text(self, text: str) -> None:
        self.sent.append(("text", text))
        self.pending = text

    def send_key(self, name: str) -> None:
        self.sent.append(("key", name))
        if name == "Escape":
            self.screen = CHROME
        elif name == "Enter":
            if self.screen == TRUST:
                self.screen = CHROME
            elif self.pending in self.on_prompt:
                self.screen = self.on_prompt[self.pending]
            self.pending = None


STANDARD_SCREENS = {
    "/usage": USAGE,
    "/model": MODEL,
    "Reply with exactly: ok": STATUS,
}


# ── resolve_command ──────────────────────────────────────────────────────


def test_resolve_command_cli_value_wins(tmp_path):
    (tmp_path / ".env").write_text("CLAUDE_COMMAND=from-file\n")
    result = resolve_command(
        "from-cli", {"CLAUDE_COMMAND": "from-env"}, tmp_path / ".env"
    )
    assert result == "from-cli"


def test_resolve_command_env_before_file(tmp_path):
    (tmp_path / ".env").write_text("CLAUDE_COMMAND=from-file\n")
    result = resolve_command(None, {"CLAUDE_COMMAND": "from-env"}, tmp_path / ".env")
    assert result == "from-env"


def test_resolve_command_reads_dotenv_file(tmp_path):
    (tmp_path / ".env").write_text("CLAUDE_COMMAND=from-file\n")
    assert resolve_command(None, {}, tmp_path / ".env") == "from-file"


def test_resolve_command_defaults_to_claude(tmp_path):
    assert resolve_command(None, {}, tmp_path / "missing.env") == "claude"


# ── child_env ────────────────────────────────────────────────────────────


def test_child_env_drops_nesting_markers_and_keeps_needed_vars():
    env = {
        "CLAUDECODE": "1",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_PID": "123",
        "TMUX": "/tmp/tmux-1/default,1,0",
        "TMUX_PANE": "%3",
        "CLAUDE_CONFIG_DIR": "/cfg",
        "CLAUDE_COMMAND": "claude --x",
        "PATH": "/usr/bin",
        "ANTHROPIC_BASE_URL": "http://proxy",
        "CCBOT_DIR": "/old",
    }

    result = child_env(env, "/tmp/ccbot-selfcheck-abc")

    assert "CLAUDECODE" not in result
    assert "CLAUDE_CODE_ENTRYPOINT" not in result
    assert "CLAUDE_PID" not in result
    assert "TMUX" not in result
    assert "TMUX_PANE" not in result
    assert result["CLAUDE_CONFIG_DIR"] == "/cfg"
    assert result["CLAUDE_COMMAND"] == "claude --x"
    assert result["PATH"] == "/usr/bin"
    assert result["ANTHROPIC_BASE_URL"] == "http://proxy"
    assert result["CCBOT_DIR"] == "/tmp/ccbot-selfcheck-abc"


# ── trust_dialog_keys ────────────────────────────────────────────────────


def test_trust_dialog_cursor_on_no_moves_down_to_yes():
    assert trust_dialog_keys(TRUST) == ["Down", "Enter"]


def test_trust_dialog_cursor_already_on_yes():
    pane = TRUST.replace("❯ No, exit", "  No, exit").replace(
        "  Yes, I trust this folder", "❯ Yes, I trust this folder"
    )
    assert trust_dialog_keys(pane) == ["Enter"]


def test_trust_dialog_yes_above_cursor_moves_up():
    pane = (
        " Quick safety check: trust this folder?\n"
        "   Yes, I trust this folder\n"
        " ❯ No, exit\n"
        " Enter to confirm"
    )
    assert trust_dialog_keys(pane) == ["Up", "Enter"]


def test_trust_dialog_not_shown_returns_none():
    assert trust_dialog_keys(CHROME) is None


# ── wait_for ─────────────────────────────────────────────────────────────


def test_wait_for_returns_pane_once_predicate_holds(clock):
    panes = iter(["loading", "loading", "READY"])

    result = wait_for(lambda: next(panes), lambda p: p == "READY", timeout=10)

    assert result == "READY"


def test_wait_for_returns_none_on_timeout(clock):
    result = wait_for(lambda: "still nothing", lambda p: False, timeout=2, interval=0.5)

    assert result is None
    assert clock[0] >= 2


# ── Tmux ─────────────────────────────────────────────────────────────────


def test_every_tmux_call_uses_dedicated_socket(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="pane", stderr="")

    monkeypatch.setattr(selfcheck.subprocess, "run", fake_run)
    tmux = Tmux()

    tmux.start("/tmp/work", "claude", {"PATH": "/usr/bin"})
    assert tmux.capture() == "pane"
    tmux.send_text("/usage")
    tmux.send_key("Enter")
    tmux.kill()

    assert len(calls) == 5
    for call in calls:
        assert call[:3] == ["tmux", "-L", "ccbot-selfcheck"]
    assert calls[0][3:] == [
        "new-session",
        "-d",
        "-s",
        "check",
        "-x",
        "120",
        "-y",
        "45",
        "-c",
        "/tmp/work",
        "claude",
    ]
    assert calls[1][3:] == ["capture-pane", "-p", "-t", "check"]
    assert calls[2][3:] == ["send-keys", "-t", "check", "-l", "/usage"]
    assert calls[3][3:] == ["send-keys", "-t", "check", "Enter"]
    assert calls[4][3:] == ["kill-server"]


def test_tmux_start_raises_when_tmux_fails(monkeypatch):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="no server")

    monkeypatch.setattr(selfcheck.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="no server"):
        Tmux().start("/tmp/work", "claude", {})


# ── Report and exit code ─────────────────────────────────────────────────


def test_all_pass_is_ok_and_zero():
    results = [CheckResult("startup", "PASS"), CheckResult("usage", "PASS")]

    report = format_report(results, "2.1.311", "2.1.311")

    assert report.splitlines()[-1] == "RESULT: OK"
    assert exit_code(results) == 0


def test_warn_and_skip_do_not_fail():
    results = [CheckResult("usage", "WARN", "x"), CheckResult("status", "SKIP")]

    assert format_report(results, "unknown", "2.1.311").splitlines()[-1] == "RESULT: OK"
    assert exit_code(results) == 0


def test_any_fail_is_failed_and_one():
    results = [CheckResult("startup", "PASS"), CheckResult("model", "FAIL", "nope")]

    assert format_report(results, "2.1.311", "2.1.311").splitlines()[-1] == (
        "RESULT: FAILED"
    )
    assert exit_code(results) == 1


# ── Check flow ───────────────────────────────────────────────────────────


def test_full_flow_passes_startup_usage_model_and_skips_status(clock):
    tmux = FakeTmux(on_prompt=STANDARD_SCREENS)

    results = run_checks(tmux, timeout=5, with_prompt=False)

    by_name = {r.name: r for r in results}
    assert by_name["startup"].status == "PASS"
    assert by_name["usage"].status == "PASS"
    assert by_name["model"].status == "PASS"
    assert by_name["status"].status == "SKIP"
    assert by_name["status"].detail == "use --with-prompt"
    assert exit_code(results) == 0


def test_status_check_passes_with_prompt(clock):
    tmux = FakeTmux(on_prompt=STANDARD_SCREENS)

    results = run_checks(tmux, timeout=5, with_prompt=True)

    by_name = {r.name: r for r in results}
    assert by_name["status"].status == "PASS"
    assert "Working on it" in by_name["status"].detail


def test_startup_failure_skips_the_rest(clock):
    tmux = FakeTmux(screen=SHELL)

    results = run_checks(tmux, timeout=2, with_prompt=True)

    by_name = {r.name: r for r in results}
    assert by_name["startup"].status == "FAIL"
    assert "user@host:~$" in by_name["startup"].detail
    for name in ("usage", "model", "status"):
        assert by_name[name].status == "SKIP"
        assert by_name[name].detail == "startup failed"
    assert exit_code(results) == 1


def test_trust_dialog_is_answered_once(clock):
    tmux = FakeTmux(screen=TRUST)

    results = run_checks(tmux, timeout=5, with_prompt=False)

    assert results[0].status == "PASS"
    keys = [name for kind, name in tmux.sent if kind == "key"]
    assert keys[:2] == ["Down", "Enter"]
    assert keys.count("Down") == 1


def test_usage_dialog_not_parsed_is_warn(clock):
    tmux = FakeTmux(on_prompt={"/usage": MODEL, "/model": MODEL})

    results = run_checks(tmux, timeout=2, with_prompt=False)

    usage = next(r for r in results if r.name == "usage")
    assert usage.status == "WARN"
    assert usage.detail == "dialog 'Settings' shown but usage not parsed"


def test_unrecognized_model_screen_fails(clock):
    tmux = FakeTmux(on_prompt={"/usage": USAGE, "/model": "random output"})

    results = run_checks(tmux, timeout=2, with_prompt=False)

    model = next(r for r in results if r.name == "model")
    assert model.status == "FAIL"


def test_save_dir_writes_pane_per_check(clock, tmp_path):
    tmux = FakeTmux(on_prompt=STANDARD_SCREENS)
    save_dir = tmp_path / "panes"

    run_checks(tmux, timeout=5, with_prompt=False, save_dir=save_dir)

    assert (save_dir / "startup.txt").read_text(encoding="utf-8") == CHROME
    assert (save_dir / "usage.txt").read_text(encoding="utf-8") == USAGE
    assert (save_dir / "model.txt").read_text(encoding="utf-8") == MODEL
    assert not (save_dir / "status.txt").exists()


# ── Teardown guard ───────────────────────────────────────────────────────


def test_transcript_removal_refuses_foreign_workdir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / ".claude" / "projects" / "-home-user-project"
    project.mkdir(parents=True)

    with pytest.raises(ValueError):
        remove_transcript_dir(Path("/home/user/project"))

    assert project.is_dir()


def test_transcript_removal_deletes_selfcheck_project_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    workdir = Path("/tmp/ccbot-selfcheck-abc123")
    project = tmp_path / ".claude" / "projects" / "-tmp-ccbot-selfcheck-abc123"
    project.mkdir(parents=True)

    remove_transcript_dir(workdir)

    assert not project.exists()


# ── Dispatch and import isolation ────────────────────────────────────────


def test_main_dispatches_selfcheck(monkeypatch):
    seen: list[list[str]] = []

    def fake_selfcheck_main(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr(selfcheck, "selfcheck_main", fake_selfcheck_main)
    monkeypatch.setattr(sys, "argv", ["ccbot", "selfcheck", "--timeout", "5"])

    with pytest.raises(SystemExit) as exc:
        main_module.main()

    assert exc.value.code == 0
    assert seen == [["--timeout", "5"]]


def test_import_does_not_load_config():
    src_dir = str(Path(ccbot.__file__).resolve().parent.parent)
    code = (
        "import sys\n"
        "import ccbot.selfcheck\n"
        "assert 'ccbot.config' not in sys.modules\n"
        "print('ok')\n"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/"),
        "PYTHONPATH": src_dir,
    }

    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"
