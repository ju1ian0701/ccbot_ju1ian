"""Tests for structural-task evidence checks (ISS-013)."""

from __future__ import annotations

from pathlib import Path

from update_backlog_status import done_block_reasons
from validate_changes import check_evidence


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_deleted_path_absent_passes(tmp_path):
    report = check_evidence(tmp_path, {"deleted_paths": ["src/ccbot/old.py"]})
    assert report["ok"] is True
    assert report["checks"][0]["detail"] == "absent"


def test_deleted_path_still_exists_fails(tmp_path):
    _write(tmp_path, "src/ccbot/old.py", "x = 1\n")
    report = check_evidence(tmp_path, {"deleted_paths": ["src/ccbot/old.py"]})
    assert report["ok"] is False
    check = report["checks"][0]
    assert check["check"] == "deleted_path"
    assert check["detail"] == "still exists"


def test_call_site_found_in_src(tmp_path):
    _write(tmp_path, "src/ccbot/session.py", "store = BindingStore()\n")
    report = check_evidence(tmp_path, {"call_sites": [r"BindingStore\("]})
    assert report["ok"] is True


def test_call_site_missing_fails(tmp_path):
    _write(tmp_path, "src/ccbot/session.py", "store = object()\n")
    report = check_evidence(tmp_path, {"call_sites": [r"BindingStore\("]})
    assert report["ok"] is False
    assert report["checks"][0]["detail"] == "no match in src/"


def test_call_site_in_tests_does_not_count(tmp_path):
    # production proof: matches outside src/ are ignored
    _write(tmp_path, "tests/ccbot/test_x.py", "BindingStore()\n")
    report = check_evidence(tmp_path, {"call_sites": [r"BindingStore\("]})
    assert report["ok"] is False


def test_grep_present_and_absent(tmp_path):
    _write(tmp_path, "src/ccbot/a.py", "require_session(update)\n")
    evidence = {
        "grep": [
            {"pattern": "require_session", "path": "src/", "expect": "present"},
            {
                "pattern": r"is_user_allowed\(user.id\)",
                "path": "src/",
                "expect": "absent",
            },
        ]
    }
    report = check_evidence(tmp_path, evidence)
    assert report["ok"] is True


def test_grep_absent_but_found_fails(tmp_path):
    _write(tmp_path, "src/ccbot/a.py", "is_user_allowed(user.id)\n")
    evidence = {
        "grep": [{"pattern": "is_user_allowed", "path": "src/", "expect": "absent"}]
    }
    report = check_evidence(tmp_path, evidence)
    assert report["ok"] is False


def test_grep_bad_regex_fails(tmp_path):
    _write(tmp_path, "src/ccbot/a.py", "x = 1\n")
    report = check_evidence(tmp_path, {"grep": [{"pattern": "([", "path": "src/"}]})
    assert report["ok"] is False


def test_test_file_missing_fails(tmp_path):
    report = check_evidence(tmp_path, {"tests": ["tests/ccbot/test_x.py"]})
    assert report["ok"] is False


def test_test_file_exists_passes(tmp_path):
    _write(tmp_path, "tests/ccbot/test_x.py", "def test_x():\n    pass\n")
    report = check_evidence(tmp_path, {"tests": ["tests/ccbot/test_x.py"]})
    assert report["ok"] is True


def test_done_block_reasons_plain_task_allowed(tmp_path):
    assert done_block_reasons(tmp_path, {"id": "T-1"}) == []


def test_done_block_reasons_structural_without_evidence_blocked(tmp_path):
    reasons = done_block_reasons(tmp_path, {"id": "T-2", "kind": "structural"})
    assert reasons
    assert "no evidence block" in reasons[0]


def test_done_block_reasons_failing_evidence_blocked(tmp_path):
    _write(tmp_path, "src/ccbot/old.py", "x = 1\n")
    task = {"id": "T-3", "evidence": {"deleted_paths": ["src/ccbot/old.py"]}}
    reasons = done_block_reasons(tmp_path, task)
    assert reasons
    assert "deleted_path" in reasons[0]


def test_done_block_reasons_passing_evidence_allowed(tmp_path):
    task = {"id": "T-4", "evidence": {"deleted_paths": ["src/ccbot/old.py"]}}
    assert done_block_reasons(tmp_path, task) == []
