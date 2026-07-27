"""Thin re-export of auth/topic helpers (ISS-002).

Canonical definitions live in ``ccbot.session_guard``. This module exists for
backward-compatible imports (``from ccbot.handlers.auth import …``) only —
do not reintroduce independent implementations here.
"""

from __future__ import annotations

from ..session_guard import get_thread_id, is_user_allowed

__all__ = ["get_thread_id", "is_user_allowed"]
