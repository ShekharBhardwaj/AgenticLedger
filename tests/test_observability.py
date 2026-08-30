"""Tests for the readiness probe and the dropped-capture counter.

Capture must be fail-open (a storage failure never breaks the agent's call), but
the failure must be *visible* — counted and surfaced via /readyz — not silent.
"""

import httpx2 as httpx

from .conftest import openai_response


def test_readyz_ok_when_store_reachable(proxy):
    client = proxy()
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["store"] == "ok"
    assert body["capture_dropped"] == 0


def test_readyz_503_when_store_unreachable(proxy):
    client = proxy()

    async def _boom(*a, **k):
        raise RuntimeError("store down")

    client.app.state.store.ping = _boom
    resp = client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json()["store"] == "error"


def test_health_is_liveness_only(proxy):
    """/health must not touch the store (stays 200 even if the store is down)."""
    client = proxy()

    async def _boom(*a, **k):
        raise RuntimeError("store down")

    client.app.state.store.ping = _boom
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_capture_failure_is_failopen_and_counted(proxy):
    """If saving the call fails, the agent still gets its response and the drop is counted."""
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(content="pong")))

    async def _boom(*a, **k):
        raise RuntimeError("db write failed")

    client.app.state.store.save = _boom

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "ping"}]},
        headers={"x-agenticledger-session-id": "s1"},
    )
    # Fail-open: the upstream response is still returned unmodified.
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "pong"

    # But the dropped capture is now visible.
    ready = client.get("/readyz").json()
    assert ready["capture_dropped"] == 1


async def test_store_ping_succeeds(store):
    """The SQLite store ping is a no-op SELECT that does not raise."""
    await store.ping()
