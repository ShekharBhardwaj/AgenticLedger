"""Reports: store aggregates, cache savings, digest text, and the API."""

import time

import pytest

from agenticledger.proxy.normalize import CanonicalRequest, CanonicalResponse
from agenticledger.proxy.pricing import compute_cost
from agenticledger.proxy.reports import build_report, digest_text


def _req(model: str, provider: str) -> CanonicalRequest:
    return CanonicalRequest(
        messages=[{"role": "user", "content": "hi"}],
        model_id=model, provider=provider, timestamp=time.time(),
    )


def _resp(tokens_in, tokens_out, cost, cache_read=None, cache_write=None,
          latency=100.0) -> CanonicalResponse:
    return CanonicalResponse(
        content="ok", tool_calls=None, stop_reason="stop",
        tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency,
        cost_usd=cost, cache_read_tokens=cache_read, cache_write_tokens=cache_write,
    )


async def _seed(store) -> None:
    await store.save(
        "10000000-0000-0000-0000-000000000001",
        _req("claude-sonnet-5", "anthropic"),
        _resp(500, 200, 0.01, cache_read=10_000, cache_write=2_000),
        session_id="s1", agent_name="researcher",
    )
    await store.save(
        "10000000-0000-0000-0000-000000000002",
        _req("gpt-4o", "openai"),
        _resp(2_000, 100, 0.006, cache_read=1_000, latency=200.0),
        session_id="s2", agent_name="writer",
    )
    await store.save(
        "10000000-0000-0000-0000-000000000003",
        _req("gpt-4o", "openai"),
        _resp(100, 0, 0.0, latency=400.0),
        session_id="s2", agent_name="writer",
        status_code=500, error_detail="boom",
    )


async def test_report_aggregates_and_cache_savings(store):
    await _seed(store)
    raw = await store.get_report_aggregates(0.0)
    report = build_report(raw["daily"], raw["models"], raw["agents"], days=30)

    t = report["totals"]
    assert t["call_count"] == 3
    assert t["error_calls"] == 1
    assert t["cache_read_tokens"] == 11_000
    assert t["cache_write_tokens"] == 2_000
    assert t["total_cost_usd"] == pytest.approx(0.016)

    models = {m["model_id"]: m for m in report["models"]}
    # Anthropic convention: without caching, reads+writes would be plain input.
    expected_claude = (
        compute_cost("claude-sonnet-5", 500 + 10_000 + 2_000, 0)
        - compute_cost("claude-sonnet-5", 500, 0,
                       cache_read_tokens=10_000, cache_write_tokens=2_000,
                       provider="anthropic")
    )
    assert models["claude-sonnet-5"]["cache_savings_usd"] == pytest.approx(expected_claude)
    # OpenAI convention: cached tokens are a subset of prompt tokens.
    expected_gpt = (
        compute_cost("gpt-4o", 2_000, 0)
        - compute_cost("gpt-4o", 2_000, 0, cache_read_tokens=1_000, provider="openai")
    )
    assert expected_gpt > 0
    assert models["gpt-4o"]["cache_savings_usd"] == pytest.approx(expected_gpt)

    agents = {a["agent_name"]: a for a in report["agents"]}
    assert agents["writer"]["call_count"] == 2
    assert agents["writer"]["session_count"] == 1
    assert agents["researcher"]["call_count"] == 1

    # Latency percentiles (nearest-rank) and per-group error counts.
    gpt = models["gpt-4o"]
    assert gpt["error_calls"] == 1
    assert gpt["p50_latency_ms"] == 200.0     # of [200, 400]
    assert gpt["p95_latency_ms"] == 400.0
    assert models["claude-sonnet-5"]["p99_latency_ms"] == 100.0
    assert agents["writer"]["p95_latency_ms"] == 400.0
    assert agents["researcher"]["error_calls"] == 0

    assert len(report["daily"]) == 1  # seeded "now" — one UTC day bucket
    assert report["daily"][0]["call_count"] == 3


async def test_report_window_excludes_older_calls(store):
    await _seed(store)
    raw = await store.get_report_aggregates(time.time() + 3600)  # future cutoff
    report = build_report(raw["daily"], raw["models"], raw["agents"], days=1)
    assert report["totals"]["call_count"] == 0
    assert report["daily"] == []


async def test_digest_text_mentions_cache_and_top_spenders(store):
    await _seed(store)
    raw = await store.get_report_aggregates(0.0)
    text = digest_text(build_report(raw["daily"], raw["models"], raw["agents"], days=1))
    assert "Spend: $" in text
    assert "Prompt cache saved $" in text
    assert "Top models:" in text and "gpt-4o" in text
    assert "Top agents:" in text and "researcher" in text


def test_reports_endpoint(proxy):
    client = proxy()
    now_ns = int(time.time() * 1e9)
    span = {
        "resourceSpans": [{
            "scopeSpans": [{"spans": [{
                "traceId": "0af7651916cd43dd8448eb211c80319c",
                "spanId": "c7ad6b7169203331",
                "name": "chat gpt-4o",
                "startTimeUnixNano": str(now_ns),
                "endTimeUnixNano": str(now_ns + 1_000_000_000),
                "attributes": [
                    {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1200"}},
                    {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "300"}},
                    {"key": "gen_ai.conversation.id", "value": {"stringValue": "rep-1"}},
                ],
            }]}],
        }]
    }
    assert client.post("/v1/traces", json=span,
                       headers={"content-type": "application/json"}).status_code == 200

    data = client.get("/api/reports?days=7").json()
    assert data["days"] == 7
    assert data["totals"]["call_count"] == 1
    assert data["models"][0]["model_id"] == "gpt-4o"
    assert data["totals"]["total_cost_usd"] == pytest.approx(
        (1200 * 2.50 + 300 * 10.00) / 1_000_000)
