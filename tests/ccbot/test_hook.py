"""Tests for Claude Code session tracking hook."""

import io
import json
import sys
import types
from pathlib import Path

import pytest

import ccbot.hook as hook_mod
from ccbot.hook import (
    _UUID_RE,
    _count_claude_ancestors,
    _install_hook,
    _is_ancestor,
    _is_hook_installed,
    _parse_stat_ppid,
    _pid_alive,
    _ppid,
    hook_main,
)

SESSION_A = "550e8400-e29b-41d4-a716-446655440000"
SESSION_B = "660e8400-e29b-41d4-a716-446655440111"


@pytest.fixture(autouse=True)
def _clear_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not depend on the Claude Code session running the suite."""
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    monkeypatch.delenv("CLAUDE_PID", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    # Real /proc must not decide the outcome; tests override this where relevant.
    monkeypatch.setattr(hook_mod, "_count_claude_ancestors", lambda pid: 1)


class TestUuidRegex:
    @pytest.mark.parametrize(
        "value",
        [
            "550e8400-e29b-41d4-a716-446655440000",
            "00000000-0000-0000-0000-000000000000",
            "abcdef01-2345-6789-abcd-ef0123456789",
        ],
        ids=["standard", "all-zeros", "all-hex"],
    )
    def test_valid_uuid_matches(self, value: str) -> None:
        assert _UUID_RE.match(value) is not None

    @pytest.mark.parametrize(
        "value",
        [
            "not-a-uuid",
            "550e8400-e29b-41d4-a716",
            "550e8400-e29b-41d4-a716-44665544000g",
            "",
        ],
        ids=["gibberish", "truncated", "invalid-hex-char", "empty"],
    )
    def test_invalid_uuid_no_match(self, value: str) -> None:
        assert _UUID_RE.match(value) is None


class TestIsHookInstalled:
    def test_hook_present(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {"type": "command", "command": "ccbot hook", "timeout": 5}
                        ]
                    }
                ]
            }
        }
        assert _is_hook_installed(settings) is True

    def test_no_hooks_key(self) -> None:
        assert _is_hook_installed({}) is False

    def test_different_hook_command(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "other-tool hook"}]}
                ]
            }
        }
        assert _is_hook_installed(settings) is False

    def test_full_path_matches(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/usr/bin/ccbot hook",
                                "timeout": 5,
                            }
                        ]
                    }
                ]
            }
        }
        assert _is_hook_installed(settings) is True


class TestHookMainValidation:
    def _run_hook_main(
        self, monkeypatch: pytest.MonkeyPatch, payload: dict, *, tmux_pane: str = ""
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["ccbot", "hook"])
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        if tmux_pane:
            monkeypatch.setenv("TMUX_PANE", tmux_pane)
        else:
            monkeypatch.delenv("TMUX_PANE", raising=False)
        hook_main()

    def test_missing_session_id(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        self._run_hook_main(
            monkeypatch,
            {"cwd": "/tmp", "hook_event_name": "SessionStart"},
        )
        assert not (tmp_path / "session_map.json").exists()

    def test_invalid_uuid_format(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        self._run_hook_main(
            monkeypatch,
            {
                "session_id": "not-a-uuid",
                "cwd": "/tmp",
                "hook_event_name": "SessionStart",
            },
        )
        assert not (tmp_path / "session_map.json").exists()

    def test_relative_cwd(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        self._run_hook_main(
            monkeypatch,
            {
                "session_id": "550e8400-e29b-41d4-a716-446655440000",
                "cwd": "relative/path",
                "hook_event_name": "SessionStart",
            },
        )
        assert not (tmp_path / "session_map.json").exists()

    def test_non_session_start_event(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
        self._run_hook_main(
            monkeypatch,
            {
                "session_id": "550e8400-e29b-41d4-a716-446655440000",
                "cwd": "/tmp",
                "hook_event_name": "Stop",
            },
        )
        assert not (tmp_path / "session_map.json").exists()


def _fake_tmux(
    monkeypatch: pytest.MonkeyPatch, output: str = "ccbot:@1:mywin\n"
) -> None:
    def fake_run(*args, **kwargs):
        return types.SimpleNamespace(stdout=output, returncode=0)

    monkeypatch.setattr(hook_mod.subprocess, "run", fake_run)


def _run_session_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session_id: str,
    *,
    env: dict[str, str] | None = None,
) -> None:
    monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
    monkeypatch.setenv("TMUX_PANE", "%0")
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    _fake_tmux(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["ccbot", "hook"])
    payload = {
        "session_id": session_id,
        "cwd": "/tmp",
        "hook_event_name": "SessionStart",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    hook_main()


def _seed_map(tmp_path: Path, entry: dict) -> Path:
    map_file = tmp_path / "session_map.json"
    map_file.write_text(json.dumps({"ccbot:@1": entry}))
    return map_file


class TestHeadlessAndOwnership:
    def test_headless_entrypoint_not_registered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _run_session_start(
            monkeypatch,
            tmp_path,
            SESSION_A,
            env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli", "CLAUDE_PID": "200"},
        )
        assert not (tmp_path / "session_map.json").exists()

    def test_unknown_entrypoint_still_registers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _run_session_start(
            monkeypatch,
            tmp_path,
            SESSION_A,
            env={"CLAUDE_CODE_ENTRYPOINT": "weird"},
        )
        data = json.loads((tmp_path / "session_map.json").read_text())
        assert data["ccbot:@1"]["session_id"] == SESSION_A
        assert "claude_pid" not in data["ccbot:@1"]

    def test_new_entry_records_claude_pid(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _run_session_start(
            monkeypatch,
            tmp_path,
            SESSION_A,
            env={"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "200"},
        )
        data = json.loads((tmp_path / "session_map.json").read_text())
        assert data["ccbot:@1"] == {
            "session_id": SESSION_A,
            "cwd": "/tmp",
            "window_name": "mywin",
            "claude_pid": 200,
        }

    def test_nested_child_of_live_ancestor_does_not_overwrite(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        map_file = _seed_map(
            tmp_path,
            {
                "session_id": SESSION_A,
                "cwd": "/tmp",
                "window_name": "mywin",
                "claude_pid": 100,
            },
        )
        before = map_file.read_text()
        monkeypatch.setattr(hook_mod, "_pid_alive", lambda pid: True)
        monkeypatch.setattr(hook_mod, "_is_ancestor", lambda a, p: (a, p) == (100, 200))
        _run_session_start(
            monkeypatch,
            tmp_path,
            SESSION_B,
            env={"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "200"},
        )
        assert map_file.read_text() == before
        assert json.loads(before)["ccbot:@1"]["session_id"] == SESSION_A

    def test_same_pid_new_session_updates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        map_file = _seed_map(
            tmp_path,
            {
                "session_id": SESSION_A,
                "cwd": "/tmp",
                "window_name": "mywin",
                "claude_pid": 100,
            },
        )
        _run_session_start(
            monkeypatch,
            tmp_path,
            SESSION_B,
            env={"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "100"},
        )
        entry = json.loads(map_file.read_text())["ccbot:@1"]
        assert entry["session_id"] == SESSION_B
        assert entry["claude_pid"] == 100

    def test_dead_owner_is_replaced(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        map_file = _seed_map(
            tmp_path,
            {
                "session_id": SESSION_A,
                "cwd": "/tmp",
                "window_name": "mywin",
                "claude_pid": 100,
            },
        )
        monkeypatch.setattr(hook_mod, "_pid_alive", lambda pid: False)
        monkeypatch.setattr(hook_mod, "_is_ancestor", lambda a, p: True)
        _run_session_start(
            monkeypatch,
            tmp_path,
            SESSION_B,
            env={"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "200"},
        )
        entry = json.loads(map_file.read_text())["ccbot:@1"]
        assert entry["session_id"] == SESSION_B
        assert entry["claude_pid"] == 200

    def test_legacy_entry_without_claude_pid_is_replaced(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        map_file = _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        _run_session_start(
            monkeypatch,
            tmp_path,
            SESSION_B,
            env={"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "200"},
        )
        entry = json.loads(map_file.read_text())["ccbot:@1"]
        assert entry["session_id"] == SESSION_B
        assert entry["claude_pid"] == 200

    def test_legacy_entry_not_hijacked_by_nested_claude(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Entry written by the old hook has no owner: fall back to process names."""
        map_file = _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        monkeypatch.setattr(hook_mod, "_count_claude_ancestors", lambda pid: 2)
        _run_session_start(
            monkeypatch,
            tmp_path,
            SESSION_B,
            env={"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "200"},
        )
        entry = json.loads(map_file.read_text())["ccbot:@1"]
        assert entry["session_id"] == SESSION_A
        assert "claude_pid" not in entry

    def test_no_entry_nested_claude_with_pid_not_registered(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(hook_mod, "_count_claude_ancestors", lambda pid: 2)
        _run_session_start(
            monkeypatch,
            tmp_path,
            SESSION_A,
            env={"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "200"},
        )
        assert not (tmp_path / "session_map.json").exists()

    def test_no_claude_pid_nested_when_two_claude_ancestors(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(hook_mod, "_count_claude_ancestors", lambda pid: 2)
        _run_session_start(monkeypatch, tmp_path, SESSION_A)
        assert not (tmp_path / "session_map.json").exists()

    def test_no_claude_pid_written_with_one_claude_ancestor(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(hook_mod, "_count_claude_ancestors", lambda pid: 1)
        _run_session_start(monkeypatch, tmp_path, SESSION_A)
        entry = json.loads((tmp_path / "session_map.json").read_text())["ccbot:@1"]
        assert entry["session_id"] == SESSION_A
        assert "claude_pid" not in entry


class TestProcessHelpers:
    def test_parse_stat_ppid_with_spaces_and_parens_in_comm(self) -> None:
        assert _parse_stat_ppid("123 (tmux: server (x)) S 77 123 0 0") == 77

    def test_parse_stat_ppid_simple_comm(self) -> None:
        assert _parse_stat_ppid("9 (claude) S 1 9 9 0") == 1

    @pytest.mark.parametrize("text", ["", "garbage", "1 (x"])
    def test_parse_stat_ppid_malformed_returns_zero(self, text: str) -> None:
        assert _parse_stat_ppid(text) == 0

    def test_ppid_uses_stat_when_proc_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "read_text", lambda self, **kw: "5 (bash) S 42 5 5 0")
        assert _ppid(5) == 42

    @staticmethod
    def _chain(monkeypatch: pytest.MonkeyPatch, parents: dict[int, int]) -> list[int]:
        calls: list[int] = []

        def fake_ppid(pid: int) -> int:
            calls.append(pid)
            return parents.get(pid, 0)

        monkeypatch.setattr(hook_mod, "_ppid", fake_ppid)
        return calls

    def test_is_ancestor_walks_chain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._chain(monkeypatch, {5: 4, 4: 3, 3: 1})
        assert _is_ancestor(3, 5) is True
        assert _is_ancestor(4, 5) is True
        assert _is_ancestor(9, 5) is False

    def test_is_ancestor_self_is_not_ancestor(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._chain(monkeypatch, {5: 4, 4: 5})
        assert _is_ancestor(5, 5) is False

    def test_is_ancestor_depth_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        parents = {i: i - 1 for i in range(2, 101)}
        calls = self._chain(monkeypatch, parents)
        assert _is_ancestor(50, 100) is True  # 50 hops, within cap
        assert _is_ancestor(2, 100) is False  # 98 hops, beyond cap
        assert len(calls) <= 128

    def test_is_ancestor_stops_at_init(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._chain(monkeypatch, {5: 1})
        assert _is_ancestor(7, 5) is False
        assert calls == [5]

    def test_pid_alive(self) -> None:
        import os

        assert _pid_alive(0) is False
        assert _pid_alive(-1) is False
        assert _pid_alive(os.getpid()) is True

    def test_count_claude_ancestors_counts_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        parents = {30: 20, 20: 10, 10: 1}
        claudes = {30, 10}
        monkeypatch.setattr(hook_mod, "_ppid", lambda p: parents.get(p, 0))
        monkeypatch.setattr(hook_mod, "_looks_like_claude", lambda p: p in claudes)
        assert _count_claude_ancestors(30) == 2
        assert _count_claude_ancestors(20) == 1


def _run_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: dict,
    *,
    env: dict[str, str] | None = None,
    pane: bool = True,
) -> None:
    monkeypatch.setenv("CCBOT_DIR", str(tmp_path))
    if pane:
        monkeypatch.setenv("TMUX_PANE", "%0")
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    _fake_tmux(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["ccbot", "hook"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    hook_main()


def _event_payload(event: str, session_id: str = SESSION_A, **extra: object) -> dict:
    return {
        "session_id": session_id,
        "cwd": "/tmp",
        "hook_event_name": event,
        **extra,
    }


def _recorded(tmp_path: Path) -> list[dict]:
    events_file = tmp_path / "hook_events.jsonl"
    if not events_file.exists():
        return []
    return [json.loads(x) for x in events_file.read_text().splitlines()]


class TestEventRecording:
    def test_stop_owned_by_window_is_recorded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        map_file = _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        before = map_file.read_text()
        _run_event(
            monkeypatch, tmp_path, _event_payload("Stop", last_assistant_message="hi")
        )
        records = _recorded(tmp_path)
        assert len(records) == 1
        assert records[0]["event"] == "Stop"
        assert records[0]["window_key"] == "ccbot:@1"
        assert records[0]["session_id"] == SESSION_A
        assert map_file.read_text() == before

    def test_stop_from_nested_claude_not_recorded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        _run_event(monkeypatch, tmp_path, _event_payload("Stop", session_id=SESSION_B))
        assert not (tmp_path / "hook_events.jsonl").exists()

    def test_event_without_map_entry_not_recorded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _run_event(monkeypatch, tmp_path, _event_payload("Stop"))
        assert not (tmp_path / "hook_events.jsonl").exists()
        assert not (tmp_path / "session_map.json").exists()

    def test_notification_permission_prompt_recorded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        _run_event(
            monkeypatch,
            tmp_path,
            _event_payload(
                "Notification",
                notification_type="permission_prompt",
                message="Claude needs permission to run Bash",
            ),
        )
        (record,) = _recorded(tmp_path)
        assert record["event"] == "Notification"
        assert record["notification_type"] == "permission_prompt"
        assert record["message"] == "Claude needs permission to run Bash"

    def test_stop_failure_recorded_with_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        _run_event(
            monkeypatch, tmp_path, _event_payload("StopFailure", error="rate_limit")
        )
        (record,) = _recorded(tmp_path)
        assert record["event"] == "StopFailure"
        assert record["error"] == "rate_limit"

    def test_session_end_recorded_with_reason(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        _run_event(monkeypatch, tmp_path, _event_payload("SessionEnd", reason="clear"))
        (record,) = _recorded(tmp_path)
        assert record["event"] == "SessionEnd"
        assert record["reason"] == "clear"

    def test_sdk_entrypoint_not_recorded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        _run_event(
            monkeypatch,
            tmp_path,
            _event_payload("Stop"),
            env={"CLAUDE_CODE_ENTRYPOINT": "sdk-cli"},
        )
        assert not (tmp_path / "hook_events.jsonl").exists()

    def test_missing_tmux_pane_not_recorded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        _run_event(monkeypatch, tmp_path, _event_payload("Stop"), pane=False)
        assert not (tmp_path / "hook_events.jsonl").exists()

    def test_unknown_event_ignored(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_map(
            tmp_path,
            {"session_id": SESSION_A, "cwd": "/tmp", "window_name": "mywin"},
        )
        _run_event(
            monkeypatch, tmp_path, _event_payload("PreToolUse", tool_name="Bash")
        )
        assert not (tmp_path / "hook_events.jsonl").exists()
        assert not any(tmp_path.glob("*.lock"))


class TestInstallAllEvents:
    ALL_EVENTS = ("SessionStart", "SessionEnd", "Notification", "Stop", "StopFailure")
    ASYNC_EVENTS = ("Notification", "Stop", "StopFailure")

    @pytest.fixture
    def settings_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        path = tmp_path / "claude" / "settings.json"
        monkeypatch.setattr(hook_mod, "_CLAUDE_SETTINGS_FILE", path)
        monkeypatch.setattr(hook_mod, "_find_ccbot_path", lambda: "/opt/ccbot")
        return path

    def test_fresh_install_adds_all_events(
        self, settings_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _install_hook() == 0
        hooks = json.loads(settings_file.read_text())["hooks"]
        assert set(hooks) == set(self.ALL_EVENTS)
        for event in self.ALL_EVENTS:
            (wrapper,) = hooks[event]
            assert "matcher" not in wrapper
            (entry,) = wrapper["hooks"]
            assert entry["command"] == "/opt/ccbot hook"
            assert entry["timeout"] == 5
            if event in self.ASYNC_EVENTS:
                assert entry["async"] is True
            else:
                assert "async" not in entry
        assert "added: SessionStart, SessionEnd" in capsys.readouterr().out

    def test_second_install_is_noop(
        self, settings_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _install_hook()
        before = settings_file.read_text()
        capsys.readouterr()
        assert _install_hook() == 0
        assert settings_file.read_text() == before
        assert "Hook already installed" in capsys.readouterr().out

    def test_upgrade_from_session_start_only(self, settings_file: Path) -> None:
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "/opt/ccbot hook",
                                        "timeout": 5,
                                    }
                                ]
                            }
                        ]
                    }
                }
            )
        )
        assert _install_hook() == 0
        hooks = json.loads(settings_file.read_text())["hooks"]
        assert len(hooks["SessionStart"]) == 1
        for event in ("SessionEnd", "Notification", "Stop", "StopFailure"):
            assert len(hooks[event]) == 1

    def test_unrelated_stop_hook_preserved(self, settings_file: Path) -> None:
        other = {"hooks": [{"type": "command", "command": "other-tool notify"}]}
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"hooks": {"Stop": [other]}}))
        assert _install_hook() == 0
        hooks = json.loads(settings_file.read_text())["hooks"]
        assert hooks["Stop"][0] == other
        assert len(hooks["Stop"]) == 2
        assert _is_hook_installed(json.loads(settings_file.read_text()), "Stop")
