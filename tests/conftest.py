"""Root conftest — sets env vars BEFORE any ccbot module is imported.

The config.py module-level singleton requires TELEGRAM_BOT_TOKEN and
ALLOWED_USERS at import time, so these must be set before pytest
discovers any test that transitively imports ccbot.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types

# Windows has no fcntl; session.py / hook.py import it for file locking.
# Stub before any ccbot import so unit tests can run on Windows CI/dev hosts.
if sys.platform == "win32" and "fcntl" not in sys.modules:
    _fcntl = types.ModuleType("fcntl")
    _fcntl.LOCK_EX = 2  # type: ignore[attr-defined]
    _fcntl.LOCK_UN = 8  # type: ignore[attr-defined]
    _fcntl.LOCK_SH = 1  # type: ignore[attr-defined]
    _fcntl.LOCK_NB = 4  # type: ignore[attr-defined]

    def _flock(fd: int, op: int) -> None:  # noqa: ARG001
        return None

    _fcntl.flock = _flock  # type: ignore[attr-defined]
    sys.modules["fcntl"] = _fcntl

# Force-set (not setdefault) to prevent real env vars from leaking into tests
os.environ["TELEGRAM_BOT_TOKEN"] = "test:0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
os.environ["ALLOWED_USERS"] = "12345"
os.environ["CCBOT_DIR"] = tempfile.mkdtemp(prefix="ccbot-test-")
# PTB v22.2+: RetryAfter.retry_after as timedelta (must be set before telegram import)
os.environ["PTB_TIMEDELTA"] = "1"
