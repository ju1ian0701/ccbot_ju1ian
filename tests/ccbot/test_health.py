"""Tests for polling health tracking, the stall watchdog, and the error handler."""

import logging
import types

import httpx
import pytest
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest

from ccbot import health
from ccbot.bot import create_bot
from ccbot.health import (
    PollingHealth,
    TrackedHTTPXRequest,
    WatchdogState,
    error_handler,
    probe_telegram,
    watchdog_tick,
)


class TestPollingHealth:
    def test_mark_ok_resets_failures_and_updates_last_ok(self):
        h = PollingHealth(started_at=0.0, last_ok=0.0)
        h.consecutive_failures = 3
        h.mark_ok()
        assert h.consecutive_failures == 0
        assert h.last_ok > 0.0

    def test_mark_fail_counts_and_stores_error(self):
        h = PollingHealth()
        h.mark_fail(TimedOut("Pool timeout"))
        h.mark_fail(ValueError("boom"))
        assert h.consecutive_failures == 2
        assert h.last_error == "ValueError: boom"

    def test_seconds_since_ok(self):
        h = PollingHealth(started_at=0.0, last_ok=100.0)
        assert h.seconds_since_ok(now=160.0) == 60.0


class TestTrackedHTTPXRequest:
    async def test_do_request_marks_ok_on_success(self, monkeypatch):
        async def fake(self, url, method, **kwargs):
            return 200, b"{}"

        monkeypatch.setattr(HTTPXRequest, "do_request", fake)
        h = PollingHealth(last_ok=0.0)
        req = TrackedHTTPXRequest(health=h)
        result = await req.do_request("https://x", "POST")
        assert result == (200, b"{}")
        assert h.last_ok > 0.0
        assert h.consecutive_failures == 0

    async def test_do_request_marks_fail_and_reraises(self, monkeypatch):
        async def fake(self, url, method, **kwargs):
            raise TimedOut("Pool timeout")

        monkeypatch.setattr(HTTPXRequest, "do_request", fake)
        h = PollingHealth(last_ok=0.0)
        req = TrackedHTTPXRequest(health=h)
        with pytest.raises(TimedOut):
            await req.do_request("https://x", "POST")
        assert h.consecutive_failures == 1
        assert "Pool timeout" in h.last_error
        assert h.last_ok == 0.0

    async def test_reset_client_swaps_and_closes_old(self):
        h = PollingHealth()
        req = TrackedHTTPXRequest(health=h)
        old = req._client
        await req.reset_client()
        assert req._client is not old
        assert old.is_closed
        assert not req._client.is_closed
        assert h.client_resets == 1
        await req.shutdown()


class FakeRequest:
    def __init__(self):
        self.resets = 0

    async def reset_client(self):
        self.resets += 1


def _tick(req, h, state, now, probe, exit_fn, soft=90.0, hard=300.0):
    return watchdog_tick(
        req,
        h,
        state,
        now=now,
        soft=soft,
        hard=hard,
        probe=probe,
        exit_fn=exit_fn,
    )


async def _probe_true():
    return True


async def _probe_false():
    return False


class TestWatchdogTick:
    async def test_healthy_is_ok(self):
        h = PollingHealth(last_ok=100.0)
        req = FakeRequest()
        result = await _tick(req, h, WatchdogState(), 150.0, _probe_true, lambda: None)
        assert result == "ok"
        assert req.resets == 0

    async def test_past_soft_resets_then_waits(self):
        h = PollingHealth(last_ok=0.0)
        req = FakeRequest()
        state = WatchdogState()
        exits = []
        r1 = await _tick(req, h, state, 100.0, _probe_false, lambda: exits.append(1))
        assert r1 == "reset"
        assert req.resets == 1
        r2 = await _tick(req, h, state, 150.0, _probe_false, lambda: exits.append(1))
        assert r2 == "wait"
        assert req.resets == 1
        assert exits == []

    async def test_past_hard_and_reachable_exits_once(self):
        h = PollingHealth(last_ok=0.0)
        req = FakeRequest()
        state = WatchdogState()
        exits = []
        # Reachable on one or two ticks is not enough: polling may still be
        # backing off after a real outage.
        r1 = await _tick(req, h, state, 400.0, _probe_true, lambda: exits.append(1))
        r2 = await _tick(req, h, state, 430.0, _probe_true, lambda: exits.append(1))
        assert "exit" not in (r1, r2)
        assert exits == []
        r3 = await _tick(req, h, state, 460.0, _probe_true, lambda: exits.append(1))
        assert r3 == "exit"
        assert exits == [1]
        assert state.exit_requested
        await _tick(req, h, state, 500.0, _probe_true, lambda: exits.append(1))
        assert exits == [1]

    async def test_reachable_streak_resets_on_failed_probe_and_on_recovery(self):
        h = PollingHealth(last_ok=0.0)
        req = FakeRequest()
        state = WatchdogState()
        exits = []
        await _tick(req, h, state, 400.0, _probe_true, lambda: exits.append(1))
        await _tick(req, h, state, 430.0, _probe_true, lambda: exits.append(1))
        await _tick(req, h, state, 460.0, _probe_false, lambda: exits.append(1))
        assert state.reachable_streak == 0
        await _tick(req, h, state, 490.0, _probe_true, lambda: exits.append(1))
        await _tick(req, h, state, 520.0, _probe_true, lambda: exits.append(1))
        assert exits == []
        h.last_ok = 530.0  # polling recovered by itself
        assert await _tick(req, h, state, 540.0, _probe_true, lambda: None) == "ok"
        assert state.reachable_streak == 0

    async def test_past_hard_but_unreachable_does_not_exit(self):
        h = PollingHealth(last_ok=0.0)
        req = FakeRequest()
        state = WatchdogState()
        exits = []
        r1 = await _tick(req, h, state, 400.0, _probe_false, lambda: exits.append(1))
        assert r1 == "reset"
        assert exits == []
        assert not state.exit_requested
        r2 = await _tick(req, h, state, 420.0, _probe_false, lambda: exits.append(1))
        assert r2 == "wait"
        assert exits == []


class TestProbeTelegram:
    async def test_connect_error_returns_false(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            raise httpx.ConnectError("no route")

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        assert await probe_telegram("123:secret") is False

    async def test_non_200_returns_false(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return httpx.Response(401, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        assert await probe_telegram("123:secret") is False

    async def test_200_returns_true(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return httpx.Response(200, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
        assert await probe_telegram("123:secret") is True


class TestErrorHandler:
    async def test_network_error_logs_warning_without_traceback(self, caplog):
        ctx = types.SimpleNamespace(error=NetworkError("Bad Gateway"))
        with caplog.at_level(logging.DEBUG, logger="ccbot.health"):
            await error_handler(None, ctx)
        records = [r for r in caplog.records if r.name == "ccbot.health"]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert records[0].exc_info is None

    async def test_other_error_logs_error_with_traceback(self, caplog):
        ctx = types.SimpleNamespace(error=ValueError("bad"))
        with caplog.at_level(logging.DEBUG, logger="ccbot.health"):
            await error_handler(None, ctx)
        records = [r for r in caplog.records if r.name == "ccbot.health"]
        assert len(records) == 1
        assert records[0].levelno == logging.ERROR
        assert records[0].exc_info is not None


class TestCreateBotWiring:
    def test_get_updates_uses_tracked_request(self):
        application = create_bot()
        get_updates_request, request = application.bot._request
        assert isinstance(get_updates_request, TrackedHTTPXRequest)
        assert not isinstance(request, TrackedHTTPXRequest)

    def test_error_handler_registered(self):
        application = create_bot()
        assert health.error_handler in application.error_handlers
