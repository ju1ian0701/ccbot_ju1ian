"""Guardrail tests for propose/validate blocked path globs."""

from __future__ import annotations

import propose
from validate_changes import check_guardrails

_BLOCKED = [".env", ".env.*", "**/.env", "**/.env.*"]

_GUARD_CFG = {
    "guardrails": {
        "allowed_path_globs": ["**/*"],
        "blocked_path_globs": _BLOCKED,
        "block": _BLOCKED,
        "allow": ["**/*"],
    }
}


def test_env_example_not_blocked() -> None:
    ok, reason = propose.path_allowed(".env.example", _GUARD_CFG)
    assert ok is True
    report = check_guardrails([".env.example"], _GUARD_CFG)
    assert report["ok"] is True
    assert ".env.example" not in report["blocked_hits"]


def test_env_secret_blocked() -> None:
    ok, _reason = propose.path_allowed(".env", _GUARD_CFG)
    assert ok is False
    report = check_guardrails([".env"], _GUARD_CFG)
    assert report["ok"] is False
    assert ".env" in report["blocked_hits"]


def test_env_local_blocked() -> None:
    ok, _reason = propose.path_allowed(".env.local", _GUARD_CFG)
    assert ok is False
    report = check_guardrails([".env.local"], _GUARD_CFG)
    assert report["ok"] is False
    assert ".env.local" in report["blocked_hits"]


def test_nested_env_production_blocked() -> None:
    path = "sub/.env.production"
    ok, _reason = propose.path_allowed(path, _GUARD_CFG)
    assert ok is False
    report = check_guardrails([path], _GUARD_CFG)
    assert report["ok"] is False
    assert path in report["blocked_hits"]


def test_nested_env_example_not_blocked() -> None:
    path = "sub/.env.example"
    ok, _reason = propose.path_allowed(path, _GUARD_CFG)
    assert ok is True
    report = check_guardrails([path], _GUARD_CFG)
    assert report["ok"] is True
    assert path not in report["blocked_hits"]
