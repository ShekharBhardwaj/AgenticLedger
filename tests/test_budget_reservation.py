"""#124: the wall holds under concurrency.

Budget and ceiling checks read RECORDED spend, so in-flight calls used to
be invisible to each other and a burst could overshoot by concurrency times
one call. Reservations make in-flight calls visible; the single call that
crosses the line still passes, as one caller always did.
"""

import asyncio

import httpx2 as httpx
import pytest
from httpx2 import ASGITransport, AsyncClient

from agenticledger.proxy.app import create_app
from agenticledger.proxy.normalize import CanonicalRequest
from agenticledger.proxy.reservations import (
    Reservation,
    ReservationLedger,
    estimate_call_cost,
)

from .conftest import UPSTREAM_URL, MockUpstream, openai_response

BODY = {"model": "gpt-4o", "max_tokens": 8,
        "messages": [{"role": "user", "content": "count to three"}]}


def _estimate() -> float:
    est = estimate_call_cost(CanonicalRequest(
        messages=BODY["messages"], model_id="gpt-4o", provider="openai",
        max_tokens=8, timestamp=0.0))
    assert est is not None and est > 0
    return est


def _ok(request):
    # More prompt tokens than the estimate assumes, so the recorded cost
    # exceeds the reservation: the record, not the estimate, is the wall.
    return httpx.Response(200, json=openai_response(prompt_tokens=64, completion_tokens=8))


async def _slow_ok(request):
    await asyncio.sleep(0.25)   # every call in the burst is in flight together
    return _ok(request)


async def _burst(app, upstream, n: int, headers=None):
    """Run n requests concurrently against the app, wired to the mock upstream."""
    async with app.router.lifespan_context(app):
        app.state.client = httpx.AsyncClient(
            transport=httpx.MockTransport(upstream), base_url=UPSTREAM_URL)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            results = await asyncio.gather(*(
                c.post("/v1/chat/completions", json=BODY,
                       headers={**(headers or {}), "x-agenticledger-session-id": f"s{i}"})
                for i in range(n)))
            yield_state = (results, c)
            yield yield_state


# ── The ledger itself ────────────────────────────────────────────────────────

def test_ledger_take_and_release_are_exact_and_idempotent():
    ledger = ReservationLedger()
    a = Reservation(ledger, 0.5)
    b = Reservation(ledger, 0.25)
    ledger.take(a, ("daily", "*"))
    ledger.take(a, ("session", "s1"))
    ledger.take(b, ("daily", "*"))
    assert ledger.held(("daily", "*")) == 0.75
    assert ledger.held(("session", "s1")) == 0.5
    a.release()
    a.release()                                   # idempotent
    assert ledger.held(("daily", "*")) == 0.25
    assert ledger.held(("session", "s1")) == 0.0
    b.release()
    assert ledger.empty()
    zero = Reservation(ledger, 0.0)               # unpriced: holds nothing
    ledger.take(zero, ("daily", "*"))
    assert ledger.held(("daily", "*")) == 0.0 and ledger.empty()


def test_estimate_prices_text_and_max_tokens_and_is_none_when_unpriced():
    priced = estimate_call_cost(CanonicalRequest(
        messages=BODY["messages"], model_id="gpt-4o", provider="openai",
        max_tokens=8, timestamp=0.0))
    roomier = estimate_call_cost(CanonicalRequest(
        messages=BODY["messages"], model_id="gpt-4o", provider="openai",
        max_tokens=None, timestamp=0.0))          # falls back to a conservative default
    assert priced and roomier and roomier > priced
    assert estimate_call_cost(CanonicalRequest(
        messages=BODY["messages"], model_id="totally-unknown-model-9000",
        provider="openai", max_tokens=8, timestamp=0.0)) is None


# ── The concurrent proof ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_burst_admits_exactly_the_room(tmp_path):
    """Eight simultaneous calls into a daily budget with room for three:
    exactly three pass, five are refused, and afterwards the recorded
    wall holds on its own."""
    est = _estimate()
    app = create_app(upstream_url=UPSTREAM_URL, dsn=f"sqlite:///{tmp_path}/t.db",
                     budget_daily=3 * est)
    upstream = MockUpstream(_slow_ok)
    async for results, client in _burst(app, upstream, 8):
        codes = [r.status_code for r in results]
        assert codes.count(200) == 3, codes
        assert codes.count(429) == 5, codes
        for r in results:
            if r.status_code == 429:
                assert r.json()["error"]["type"] == "budget_exceeded"
        assert len(upstream.requests) == 3            # refused calls never reached the model
        assert app.state.reservations.empty()         # every reservation released
        # The three recorded costs exceed the room (the record is the wall
        # now, not the estimate), so the next call meets it alone.
        again = await client.post("/v1/chat/completions", json=BODY,
                                  headers={"x-agenticledger-session-id": "s9"})
        assert again.status_code == 429
        assert app.state.reservations.empty()


@pytest.mark.asyncio
async def test_concurrent_burst_respects_a_run_ceiling(tmp_path):
    """The same race lives in the run ceiling; the same ledger closes it."""
    est = _estimate()
    app = create_app(upstream_url=UPSTREAM_URL, dsn=f"sqlite:///{tmp_path}/t.db")
    upstream = MockUpstream(_slow_ok)
    async with app.router.lifespan_context(app):
        app.state.client = httpx.AsyncClient(
            transport=httpx.MockTransport(upstream), base_url=UPSTREAM_URL)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            put = await c.put("/api/labels/run/r-burst", json={"budget_usd": 3 * est})
            assert put.status_code == 200, put.text
            results = await asyncio.gather(*(
                c.post("/v1/chat/completions", json=BODY,
                       headers={"x-agenticledger-run-id": "r-burst",
                                "x-agenticledger-session-id": f"s{i}"})
                for i in range(8)))
            codes = [r.status_code for r in results]
            assert codes.count(200) == 3, codes
            assert all(c_ in (200, 429) for c_ in codes), codes
            refused = [r for r in results if r.status_code == 429]
            assert refused and all("ceiling" in r.text for r in refused)
            assert len(upstream.requests) == 3
            assert app.state.reservations.empty()


# ── Every exit path gives the room back ──────────────────────────────────────

@pytest.mark.asyncio
async def test_unreachable_upstream_releases_the_reservation(tmp_path):
    est = _estimate()
    app = create_app(upstream_url=UPSTREAM_URL, dsn=f"sqlite:///{tmp_path}/t.db",
                     budget_daily=3 * est)
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return _ok(request)

    upstream = MockUpstream(flaky)
    async with app.router.lifespan_context(app):
        app.state.client = httpx.AsyncClient(
            transport=httpx.MockTransport(upstream), base_url=UPSTREAM_URL)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            first = await c.post("/v1/chat/completions", json=BODY,
                                 headers={"x-agenticledger-session-id": "s1"})
            assert first.status_code == 502
            assert app.state.reservations.empty()     # nothing spent, nothing held
            second = await c.post("/v1/chat/completions", json=BODY,
                                  headers={"x-agenticledger-session-id": "s2"})
            assert second.status_code == 200
            assert app.state.reservations.empty()


def test_failed_capture_releases_the_reservation(proxy):
    est = _estimate()
    client = proxy(handler=_ok, budget_daily=3 * est)
    store = client.app.state.store

    async def broken_save(*args, **kwargs):
        raise RuntimeError("disk on fire")

    store.save = broken_save
    resp = client.post("/v1/chat/completions", json=BODY,
                       headers={"x-agenticledger-session-id": "s-drop"})
    assert resp.status_code == 200                    # capture failure never blocks the agent
    assert client.app.state.reservations.empty()      # and never starves the wall


def test_streaming_calls_reserve_and_release(proxy):
    est = _estimate()
    sse = (b'data: {"id":"x","object":"chat.completion.chunk","model":"gpt-4o",'
           b'"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
           b'data: {"id":"x","object":"chat.completion.chunk","model":"gpt-4o",'
           b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
           b'"usage":{"prompt_tokens":64,"completion_tokens":8,"total_tokens":72}}\n\n'
           b'data: [DONE]\n\n')
    client = proxy(
        handler=lambda r: httpx.Response(200, content=sse,
                                         headers={"content-type": "text/event-stream"}),
        budget_daily=3 * est,
    )
    resp = client.post("/v1/chat/completions", json={**BODY, "stream": True},
                       headers={"x-agenticledger-session-id": "s-stream"})
    assert resp.status_code == 200
    assert client.app.state.reservations.empty()


# ── The unpriced-model policy, stated and enforced ───────────────────────────

def test_unpriced_model_passes_uncounted_by_default(proxy):
    client = proxy(handler=_ok, budget_daily=0.000001)
    resp = client.post("/v1/chat/completions",
                       json={**BODY, "model": "totally-unknown-model-9000"},
                       headers={"x-agenticledger-session-id": "s-unpriced"})
    assert resp.status_code == 200
    assert client.app.state.reservations.empty()
    # The policy is stated on the settings page, not buried in a log.
    rows = client.get("/api/settings").json()["rows"]
    assert any(r["label"] == "unpriced models" and r["value"] == "allow"
               and "[budgets] unpriced" in r["set_with"] for r in rows), rows


def test_unpriced_model_is_refused_when_policy_says_so(proxy):
    client = proxy(handler=_ok, budget_daily=1.0, budget_unpriced="refuse")
    resp = client.post("/v1/chat/completions",
                       json={**BODY, "model": "totally-unknown-model-9000"},
                       headers={"x-agenticledger-session-id": "s-refuse"})
    assert resp.status_code == 429
    body = resp.json()["error"]
    assert body["type"] == "budget_exceeded"
    assert "no price" in body["message"] and "totally-unknown-model-9000" in body["message"]
    assert client.app.state.reservations.empty()
    assert client.upstream.requests == []              # refused at the wall
    # A priced model on the same ledger is unaffected.
    ok = client.post("/v1/chat/completions", json=BODY,
                     headers={"x-agenticledger-session-id": "s-priced"})
    assert ok.status_code == 200
