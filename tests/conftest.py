"""Root conftest — sets env vars BEFORE any ccbot module is imported.

The config.py module-level singleton requires TELEGRAM_BOT_TOKEN and
ALLOWED_USERS at import time, so these must be set before pytest
discovers any test that transitively imports ccbot.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Belt-and-suspenders for src-layout: even if pytest's pythonpath option is
# ignored (old pytest / wrong rootdir / bare invocation), tests can import ccbot.
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# Keep pytest's numbered basetemp under the repo, not %TEMP%/pytest-of-<user>.
# On Windows, cleanup of that shared tree frequently raises
# PermissionError: [WinError 5] Access is denied (pytest#7491), marking every
# tmp_path-using test as ERROR. Project-local root avoids the polluted ACL tree.
_PYTEST_TEMPROOT = _REPO_ROOT / ".pytest_tmp"
try:
    _PYTEST_TEMPROOT.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(_PYTEST_TEMPROOT))
    _temp_parent: str | None = str(_PYTEST_TEMPROOT)
except OSError:
    # Fall back to system temp if the repo path is not writable.
    _temp_parent = None

# Windows has no fcntl; session.py / hook.py import it for file locking.
# Stub before any ccbot import so unit tests can run on Windows CI/dev hosts.
if sys.platform == "win32" and "fcntl" not in sys.modules:
    _fcntl = types.ModuleType("fcntl")
    _fcntl.LOCK_EX = 2  # type: ignore[attr-defined]
    _fcntl.LOCK_UN = 8  # type: ignore[attr-defined]
    _fcntl.LOCK_SH = 1  # type: ignore[attr-defined]
    _fcntl.LOCK_NB = 4  # type: ignore[attr-defined]

    def _flock(_fd: int, _op: int) -> None:
        return None

    _fcntl.flock = _flock  # type: ignore[attr-defined]
    sys.modules["fcntl"] = _fcntl

# Force-set (not setdefault) to prevent real env vars from leaking into tests
os.environ["TELEGRAM_BOT_TOKEN"] = "test:0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
os.environ["ALLOWED_USERS"] = "12345"
# Isolate session CCBOT_DIR under the same project temp root (and clean it up).
_CCBOT_TEST_DIR = tempfile.mkdtemp(prefix="ccbot-test-", dir=_temp_parent)
os.environ["CCBOT_DIR"] = _CCBOT_TEST_DIR
atexit.register(lambda: shutil.rmtree(_CCBOT_TEST_DIR, ignore_errors=True))
# PTB v22.2+: RetryAfter.retry_after as timedelta (must be set before telegram import)
os.environ["PTB_TIMEDELTA"] = "1"
