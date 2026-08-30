"""Tests for opt-in async ingestion (capture off the hot path) and /metrics.

Sync mode (default) keeps read-after-write. Async mode persists on a background
worker — eventually consistent — and sheds load (counted) when the queue is full,
so the agent's call is never blocked.
"""

import asyncio
import time as _time

import httpx2 as httpx

from .conftest import openai_response

_CHAT = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def _metric(text: str, name: str) -> float:
    for line in text.splitlines():
        if line.startswith(name + " "):
            return float(line.split()[1])
    raise AssertionError(f"metric {name!r} not found in:\n{text}")


def _wait_status(call, status=200, timeout=3.0, interval=0.02):
    deadline = _time.time() + timeout
    resp = None
    while _time.time() < deadline:
        resp = call()
        if resp.status_code == status:
            return resp
        _time.sleep(interval)
    raise AssertionError(f"status {status} not reached within {timeout}s (last {resp and resp.status_code})")


# ── /metrics ──────────────────────────────────────────────────────────────────

def test_metrics_endpoint_sync_mode(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    client.post("/v1/chat/completions", json=_CHAT, headers={"x-agenticledger-session-id": "s1"})

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    text = resp.text
    assert _metric(text, "agenticledger_captures_persisted_total") >= 1
    assert _metric(text, "agenticledger_captures_dropped_total") == 0
    assert _metric(text, "agenticledger_capture_async") == 0
    assert _metric(text, "agenticledger_capture_queue_depth") == 0


def test_metrics_reports_async_enabled(proxy):
    client = proxy(async_capture=True)
    assert _metric(client.get("/metrics").text, "agenticledger_capture_async") == 1


# ── Async ingestion behavior ──────────────────────────────────────────────────

def test_sync_mode_is_read_after_write(proxy):
    """Default (sync) mode: a captured call is immediately queryable."""
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(content="pong")))
    resp = client.post("/v1/chat/completions", json=_CHAT,
                       headers={"x-agenticledger-session-id": "s-sync"})
    assert resp.status_code == 200
    # No waiting needed.
    assert len(client.get("/session/s-sync").json()) == 1


def test_async_capture_is_eventually_consistent(proxy):
    """Async mode: the agent gets its response, the call persists shortly after."""
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(content="pong")),
                   async_capture=True)
    resp = client.post("/v1/chat/completions", json=_CHAT,
                       headers={"x-agenticledger-session-id": "s-async"})
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "pong"
    action_id = resp.headers["x-agenticledger-action-id"]

    # Persisted by the background worker — eventually, not synchronously.
    persisted = _wait_status(lambda: client.get("/session/s-async"))
    assert len(persisted.json()) == 1
    assert client.get(f"/explain/{action_id}").status_code == 200
    assert _metric(client.get("/metrics").text, "agenticledger_captures_persisted_total") >= 1


def test_async_overflow_sheds_load_without_blocking(proxy):
    """With a tiny queue and a slow store, excess captures are dropped (counted),
    but every agent call still returns 200 immediately."""
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   async_capture=True, capture_queue_max=1)

    async def _slow_save(*args, **kwargs):
        await asyncio.sleep(1.0)  # hold the worker so the queue backs up

    client.app.state.store.save = _slow_save

    for i in range(6):
        resp = client.post("/v1/chat/completions", json=_CHAT,
                           headers={"x-agenticledger-session-id": f"s{i}"})
        assert resp.status_code == 200  # the agent is never blocked by capture

    dropped = _metric(client.get("/metrics").text, "agenticledger_captures_dropped_total")
    assert dropped >= 1  # load was shed rather than blocking
