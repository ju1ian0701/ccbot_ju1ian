"""Polling health tracking and self-healing for the getUpdates connection.

Works around upstream ccbot issue #79: python-telegram-bot gives the getUpdates
long-poll a separate httpx client with a tiny connection pool. If that connection
hangs, every later getUpdates fails with "Pool timeout" while the process
stays alive. This module:
  - tracks getUpdates successes/failures (PollingHealth, TrackedHTTPXRequest)
  - rebuilds the httpx client when polling stalls (soft threshold)
  - exits the process, so systemd restarts it, only when polling is stalled
    while Telegram is still reachable (hard threshold, see watchdog_tick)
  - logs Telegram network errors as one-line warnings (error_handler)

Key components:
  - PollingHealth / polling_health: shared success/failure state
  - TrackedHTTPXRequest: HTTPXRequest that records health and can reset_client()
  - watchdog_tick / polling_watchdog_loop: stall decision logic and its loop
  - error_handler: PTB error handler for network vs. unexpected errors
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from telegram._utils.types import ODVInput
from telegram.error import NetworkError
from telegram.ext import ContextTypes
from telegram.request import BaseRequest, HTTPXRequest, RequestData

logger = logging.getLogger(__name__)

# Delay before the hard-exit safety net fires after SIGTERM was sent.
_EXIT_SAFETY_NET_SECONDS = 20.0

# Consecutive watchdog ticks on which Telegram must be reachable (while polling
# is still stalled) before the process exits. PTB backs off up to 30 s between
# getUpdates retries, so right after a real outage ends polling may need a
# moment to recover by itself; a single successful probe is not proof of a wedge.
_REACHABLE_TICKS_BEFORE_EXIT = 3


@dataclass
class PollingHealth:
    """Success/failure bookkeeping for getUpdates requests."""

    started_at: float = field(default_factory=time.monotonic)
    last_ok: float = field(default_factory=time.monotonic)
    consecutive_failures: int = 0
    last_error: str = ""
    client_resets: int = 0

    def mark_ok(self) -> None:
        self.last_ok = time.monotonic()
        self.consecutive_failures = 0

    def mark_fail(self, exc: BaseException) -> None:
        self.consecutive_failures += 1
        self.last_error = f"{type(exc).__name__}: {exc}"

    def seconds_since_ok(self, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        return current - self.last_ok


polling_health = PollingHealth()


class TrackedHTTPXRequest(HTTPXRequest):
    """HTTPXRequest that reports request outcomes to a PollingHealth."""

    def __init__(
        self,
        *args: Any,
        health: PollingHealth | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._health: PollingHealth = health if health is not None else polling_health

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout: ODVInput[float] = BaseRequest.DEFAULT_NONE,
        write_timeout: ODVInput[float] = BaseRequest.DEFAULT_NONE,
        connect_timeout: ODVInput[float] = BaseRequest.DEFAULT_NONE,
        pool_timeout: ODVInput[float] = BaseRequest.DEFAULT_NONE,
    ) -> tuple[int, bytes]:
        try:
            result = await super().do_request(
                url,
                method,
                request_data=request_data,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
                connect_timeout=connect_timeout,
                pool_timeout=pool_timeout,
            )
        except Exception as exc:
            self._health.mark_fail(exc)
            raise
        self._health.mark_ok()
        return result

    async def reset_client(self) -> None:
        """Replace the httpx client; close the old one so hung connections drop."""
        old = self._client
        self._client = self._build_client()  # swap first, no await in between
        self._health.client_resets += 1
        try:
            await old.aclose()
        except Exception as e:  # closing a broken client must never raise
            logger.debug("closing old httpx client failed: %s", e)


async def probe_telegram(token: str, timeout: float = 10.0) -> bool:
    """Return True iff a fresh client gets HTTP 200 from Telegram's getMe.

    The URL contains the bot token, so neither it nor the token is logged.
    """
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        logger.debug("Telegram probe failed: %s", type(exc).__name__)
        return False
    return response.status_code == 200


@dataclass
class WatchdogState:
    """Mutable memory of the watchdog between ticks."""

    last_reset: float = 0.0
    reachable_streak: int = 0
    exit_requested: bool = False


class ClientResetter(Protocol):
    """Anything the watchdog can ask to rebuild its HTTP client."""

    async def reset_client(self) -> None: ...


async def watchdog_tick(
    request: ClientResetter,
    health: PollingHealth,
    state: WatchdogState,
    *,
    now: float,
    soft: float,
    hard: float,
    probe: Callable[[], Awaitable[bool]],
    exit_fn: Callable[[], None],
) -> str:
    """Make one stall-handling decision. Returns "ok", "reset", "exit" or "wait"."""
    stalled = now - health.last_ok
    if stalled <= soft:
        state.reachable_streak = 0
        return "ok"

    if stalled > hard and not state.exit_requested:
        if await probe():
            state.reachable_streak += 1
            if state.reachable_streak >= _REACHABLE_TICKS_BEFORE_EXIT:
                logger.critical(
                    "Polling stalled for %.0f s while Telegram is reachable; "
                    "restarting process",
                    stalled,
                )
                state.exit_requested = True
                exit_fn()
                return "exit"
        else:
            # Probe failed: the network is really down. Do not restart; keep waiting.
            state.reachable_streak = 0

    if now - state.last_reset >= soft:
        await request.reset_client()
        state.last_reset = now
        logger.warning(
            "Polling stalled for %.0f s; rebuilt getUpdates HTTP client "
            "(last error: %s)",
            stalled,
            health.last_error or "none",
        )
        return "reset"

    return "wait"


def request_process_exit() -> None:
    """Ask the process to exit via SIGTERM, with a hard-exit safety net."""
    safety = threading.Timer(_EXIT_SAFETY_NET_SECONDS, os._exit, args=(1,))
    safety.daemon = True
    safety.start()
    os.kill(os.getpid(), signal.SIGTERM)


async def polling_watchdog_loop(
    request: TrackedHTTPXRequest,
    health: PollingHealth,
    *,
    token: str,
    soft: float,
    hard: float,
    interval: float = 30.0,
) -> None:
    """Check polling health every ``interval`` seconds forever."""
    state = WatchdogState()
    while True:
        await asyncio.sleep(interval)
        try:
            await watchdog_tick(
                request,
                health,
                state,
                now=time.monotonic(),
                soft=soft,
                hard=hard,
                probe=lambda: probe_telegram(token),
                exit_fn=request_process_exit,
            )
        except Exception:
            logger.exception("Polling watchdog tick failed")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log network errors as one WARNING line; log anything else with traceback."""
    error = context.error
    if isinstance(error, NetworkError):
        logger.warning("Telegram network error: %s", error)
        return
    logger.error("Unhandled exception in handler", exc_info=error)
