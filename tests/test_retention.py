"""Tests for retention/TTL purge — the store method and the background worker."""

import time

import httpx

from agenticledger.proxy.normalize import CanonicalRequest, CanonicalResponse

from .conftest import openai_response


def _req(ts: float) -> CanonicalRequest:
    return CanonicalRequest(
        messages=[{"role": "user", "content": "hi"}],
        model_id="gpt-4o", provider="openai", timestamp=ts,
    )


def _resp() -> CanonicalResponse:
    return CanonicalResponse(content="ok", tool_calls=None, stop_reason="stop",
                             tokens_in=1, tokens_out=1, latency_ms=5.0, cost_usd=0.0)


async def test_purge_older_than_deletes_only_old_rows(store):
    now = time.time()
    old_day = now - 10 * 86400   # 10 days ago
    recent = now - 1 * 3600      # 1 hour ago

    await store.save("a-old", _req(old_day), _resp(), session_id="s")
    await store.save("a-new", _req(recent), _resp(), session_id="s")

    cutoff = now - 7 * 86400     # keep last 7 days
    assert await store.purge_older_than(cutoff) == 1

    remaining = await store.get_session("s")
    assert [r["action_id"] for r in remaining] == ["a-new"]


async def test_purge_returns_zero_when_nothing_old(store):
    await store.save("a", _req(time.time()), _resp(), session_id="s")
    assert await store.purge_older_than(time.time() - 365 * 86400) == 0


def test_retention_worker_purges_captured_calls(proxy):
    """A retention worker with a 0-day window and a fast tick purges captured calls."""
    client = proxy(
        handler=lambda r: httpx.Response(200, json=openai_response()),
        retention_days=0,                 # purge anything older than 'now'
        retention_interval_seconds=0.05,  # tick fast for the test
    )
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-agenticledger-session-id": "s-ret"},
    )

    # Initially present, then purged by the background worker within a few ticks.
    deadline = time.time() + 3.0
    purged = False
    while time.time() < deadline:
        if client.get("/session/s-ret").status_code == 404:
            purged = True
            break
        time.sleep(0.05)
    assert purged, "retention worker did not purge the captured call"
