"""Tests for the control spool: request/result files between the CLI and the bot."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ccbot import control


def _request_files(base: Path) -> list[str]:
    return sorted(
        p.name
        for p in control.control_dir(base).glob("*.json")
        if not p.name.endswith(".result.json")
    )


def test_control_dir_is_private(tmp_path):
    path = control.control_dir(tmp_path)
    assert path == tmp_path / "control"
    assert path.is_dir()
    assert path.stat().st_mode & 0o777 == 0o700


def test_write_request_then_list_returns_it(tmp_path):
    request_id = control.write_request("new_session", {"cwd": "/x"}, base=tmp_path)
    assert len(request_id) == 32

    pending = control.list_requests(base=tmp_path)

    assert [rid for rid, _ in pending] == [request_id]
    req = pending[0][1]
    assert req["action"] == "new_session"
    assert req["params"] == {"cwd": "/x"}
    assert isinstance(req["created"], float)


def test_list_ignores_results_and_foreign_names(tmp_path):
    request_id = control.write_request("new_session", {}, base=tmp_path)
    control.write_result(request_id, {"ok": True}, base=tmp_path)
    directory = control.control_dir(tmp_path)
    (directory / "notes.json").write_text(json.dumps({"action": "x"}))
    (directory / "XYZ.json").write_text(json.dumps({"action": "x"}))

    pending = control.list_requests(base=tmp_path)

    assert [rid for rid, _ in pending] == [request_id]


def test_malformed_request_gets_error_result_and_is_removed(tmp_path):
    directory = control.control_dir(tmp_path)
    bad_ids = ["a" * 32, "b" * 32, "c" * 32, "d" * 32]
    (directory / f"{bad_ids[0]}.json").write_text("not json {")
    (directory / f"{bad_ids[1]}.json").write_text(json.dumps(["list"]))
    (directory / f"{bad_ids[2]}.json").write_text(json.dumps({"params": {}}))
    (directory / f"{bad_ids[3]}.json").write_text(
        json.dumps({"action": "new_session", "params": "oops"})
    )

    assert control.list_requests(base=tmp_path) == []

    for rid in bad_ids:
        assert not (directory / f"{rid}.json").exists()
        assert control.read_result(rid, base=tmp_path) == {
            "ok": False,
            "error": "malformed request",
        }


def test_params_default_to_empty_dict(tmp_path):
    directory = control.control_dir(tmp_path)
    rid = "e" * 32
    (directory / f"{rid}.json").write_text(
        json.dumps({"action": "new_session", "created": time.time()})
    )

    pending = control.list_requests(base=tmp_path)

    assert pending[0][1]["params"] == {}


def test_write_and_read_result_roundtrip(tmp_path):
    rid = control.write_request("new_session", {}, base=tmp_path)
    assert control.read_result(rid, base=tmp_path) is None

    control.write_result(rid, {"ok": True, "window_id": "@3"}, base=tmp_path)

    assert control.read_result(rid, base=tmp_path) == {"ok": True, "window_id": "@3"}


def test_delete_request_missing_file_is_fine(tmp_path):
    control.delete_request("f" * 32, base=tmp_path)  # must not raise


def test_delete_request_removes_file(tmp_path):
    rid = control.write_request("new_session", {}, base=tmp_path)
    control.delete_request(rid, base=tmp_path)
    assert _request_files(tmp_path) == []


def test_wait_for_result_returns_and_removes_result(tmp_path, monkeypatch):
    rid = control.write_request("new_session", {}, base=tmp_path)
    control.write_result(rid, {"ok": True}, base=tmp_path)

    def no_sleep(_seconds):
        raise AssertionError("must not sleep when the result is already there")

    monkeypatch.setattr(time, "sleep", no_sleep)

    assert control.wait_for_result(rid, 1.0, base=tmp_path) == {"ok": True}
    assert control.read_result(rid, base=tmp_path) is None


def test_wait_for_result_polls_until_result_appears(tmp_path, monkeypatch):
    rid = control.write_request("new_session", {}, base=tmp_path)
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        control.write_result(rid, {"ok": False, "error": "boom"}, base=tmp_path)

    monkeypatch.setattr(time, "sleep", fake_sleep)

    result = control.wait_for_result(rid, 5.0, interval=0.1, base=tmp_path)

    assert result == {"ok": False, "error": "boom"}
    assert sleeps == [0.1]


def test_wait_for_result_timeout_returns_none(tmp_path, monkeypatch):
    rid = control.write_request("new_session", {}, base=tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _s: None)

    assert control.wait_for_result(rid, 0.0, base=tmp_path) is None


def test_cleanup_stale_removes_only_old_files(tmp_path):
    old_req = control.write_request("new_session", {}, base=tmp_path)
    old_res = control.write_request("new_session", {}, base=tmp_path)
    control.write_result(old_res, {"ok": True}, base=tmp_path)
    fresh = control.write_request("new_session", {}, base=tmp_path)

    directory = control.control_dir(tmp_path)
    long_ago = time.time() - 7200
    for path in (
        directory / f"{old_req}.json",
        directory / f"{old_res}.json",
        directory / f"{old_res}.result.json",
    ):
        os.utime(path, (long_ago, long_ago))

    removed = control.cleanup_stale(max_age=3600.0, base=tmp_path)

    assert removed == 3
    assert not (directory / f"{old_req}.json").exists()
    assert not (directory / f"{old_res}.result.json").exists()
    assert _request_files(tmp_path) == [f"{fresh}.json"]


def test_cleanup_stale_tolerates_vanishing_files(tmp_path, monkeypatch):
    rid = control.write_request("new_session", {}, base=tmp_path)
    path = control.control_dir(tmp_path) / f"{rid}.json"
    os.utime(path, (0, 0))

    real_unlink = Path.unlink

    def racing_unlink(self, *args, **kwargs):
        real_unlink(self, *args, **kwargs)
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "unlink", racing_unlink)

    assert control.cleanup_stale(max_age=1.0, base=tmp_path) == 0


def test_expired_request_is_rejected_not_executed(tmp_path):
    """A request left behind by a CLI that gave up must not create a session later."""
    request_id = control.write_request("new_session", {"cwd": "/tmp"}, base=tmp_path)
    path = control.control_dir(tmp_path) / f"{request_id}.json"
    data = json.loads(path.read_text())
    data["created"] = time.time() - 600
    path.write_text(json.dumps(data))

    assert control.list_requests(base=tmp_path) == []
    assert not path.exists()
    assert control.read_result(request_id, base=tmp_path) == {
        "ok": False,
        "error": "request expired",
    }


def test_request_without_created_timestamp_is_rejected(tmp_path):
    request_id = "0" * 32
    path = control.control_dir(tmp_path) / f"{request_id}.json"
    path.write_text(json.dumps({"action": "new_session", "params": {}}))

    assert control.list_requests(base=tmp_path) == []
    assert control.read_result(request_id, base=tmp_path) == {
        "ok": False,
        "error": "request expired",
    }
