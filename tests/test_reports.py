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

    # Latency percentiles (nearest-rank, successful calls only — the 400ms
    # call errored and is excluded per #30) and per-group error counts.
    gpt = models["gpt-4o"]
    assert gpt["error_calls"] == 1
    assert gpt["p50_latency_ms"] == 200.0
    assert gpt["p95_latency_ms"] == 200.0
    assert models["claude-sonnet-5"]["p99_latency_ms"] == 100.0
    assert agents["writer"]["p95_latency_ms"] == 200.0
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


async def test_report_tz_offset_shifts_day_bucket(store):
    """Issue #22: a 23:30 UTC call lands on the next day when viewed from
    UTC+2 — the tz offset moves the bucketing, not the data."""
    import datetime as dt
    ts = dt.datetime(2026, 7, 28, 23, 30, tzinfo=dt.timezone.utc).timestamp()
    req = CanonicalRequest(
        messages=[{"role": "user", "content": "hi"}],
        model_id="gpt-4o", provider="openai", timestamp=ts,
    )
    await store.save("20000000-0000-0000-0000-000000000001", req,
                     _resp(10, 5, 0.001), session_id="tz-s")
    utc = await store.get_report_aggregates(0.0)
    local = await store.get_report_aggregates(0.0, tz_offset_minutes=120)
    assert utc["daily"][0]["day"] == "2026-07-28"
    assert local["daily"][0]["day"] == "2026-07-29"


async def test_latency_percentiles_ignore_blocked_calls(store):
    """Issue #30: 0ms blocked/errored calls must not drag percentiles down —
    latency describes successful calls only."""
    await _seed(store)  # gpt-4o: 200-ok at 200ms, 500-err at 400ms
    raw = await store.get_report_aggregates(0.0)
    gpt = next(m for m in raw["models"] if m["model_id"] == "gpt-4o")
    assert gpt["p50_latency_ms"] == 200.0
    assert gpt["p99_latency_ms"] == 200.0   # the 400ms call errored — excluded


def test_estimate_whatif_math():
    """Repricing math under the anthropic convention, hand-checked."""
    from agenticledger.proxy.reports import estimate_whatif
    rows = [{"tokens_in": 1000, "tokens_out": 200, "cache_read_tokens": 10_000,
             "cache_write_tokens": 2_000, "cost_usd": 0.02}]
    # claude-haiku-4-5: $1/M in, $5/M out; reads 0.1x, writes 1.25x
    out = estimate_whatif(rows, "claude-haiku-4-5", "anthropic")
    expected = (1000 * 1 + 10_000 * 0.1 * 1 + 2_000 * 1.25 * 1 + 200 * 5) / 1e6
    assert out["estimated_cost_usd"] == pytest.approx(expected)
    assert out["actual_cost_usd"] == pytest.approx(0.02)
    assert out["calls"] == 1
    assert estimate_whatif(rows, "not-a-model-anyone-prices", "") is None


def test_whatif_endpoint(proxy):
    client = proxy()
    now_ns = int(time.time() * 1e9)
    span = {"resourceSpans": [{"scopeSpans": [{"spans": [{
        "traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": "e1e2e3e4e5e6e7e8",
        "name": "chat gpt-4o", "startTimeUnixNano": str(now_ns),
        "endTimeUnixNano": str(now_ns + 1_000_000_000),
        "attributes": [
            {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
            {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1200"}},
            {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "300"}},
            {"key": "gen_ai.conversation.id", "value": {"stringValue": "wi-1"}},
        ]}]}]}]}
    assert client.post("/v1/traces", json=span,
                       headers={"content-type": "application/json"}).status_code == 200

    r = client.get("/api/whatif", params={"session_id": "wi-1", "model": "gpt-4o-mini"})
    assert r.status_code == 200
    data = r.json()
    assert data["calls"] == 1
    assert 0 < data["estimated_cost_usd"] < data["actual_cost_usd"]

    assert client.get("/api/whatif", params={"model": "gpt-4o"}).status_code == 400
    assert client.get("/api/whatif", params={"session_id": "wi-1", "run_id": "x",
                                             "model": "gpt-4o"}).status_code == 400
    assert client.get("/api/whatif", params={"session_id": "nope",
                                             "model": "gpt-4o"}).status_code == 404
    assert client.get("/api/whatif", params={"session_id": "wi-1",
                                             "model": "mystery-9000"}).status_code == 400


def test_reports_csv_endpoint(proxy):
    client = proxy()
    now_ns = int(time.time() * 1e9)
    span = {
        "resourceSpans": [{
            "scopeSpans": [{"spans": [{
                "traceId": "0af7651916cd43dd8448eb211c80319c",
                "spanId": "d7ad6b7169203331",
                "name": "chat gpt-4o",
                "startTimeUnixNano": str(now_ns),
                "endTimeUnixNano": str(now_ns + 1_000_000_000),
                "attributes": [
                    {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                    {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                    {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1200"}},
                    {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "300"}},
                    {"key": "gen_ai.conversation.id", "value": {"stringValue": "rep-csv"}},
                ],
            }]}],
        }]
    }
    assert client.post("/v1/traces", json=span,
                       headers={"content-type": "application/json"}).status_code == 200

    data = client.get("/api/reports.csv?days=7")
    assert data.status_code == 200
    assert "text/csv" in data.headers["content-type"]
    assert "attachment" in data.headers["content-disposition"]

    text = data.text
    assert "model_id,provider,call_count" in text
    assert "gpt-4o,openai,1," in text
    assert "gpt-4o" in text


# ── #107: project-scoped reports ─────────────────────────────────────────────

def _chat(client, session, run=None, content="hi"):
    import httpx2 as _hx  # noqa: F401
    headers = {"x-agenticledger-session-id": session}
    if run:
        headers["x-agenticledger-run-id"] = run
    return client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": content}]},
        headers=headers)


def test_report_scopes_to_a_project(proxy):
    """?project= narrows every aggregate to sessions resolved into that
    project — hand labels, app bindings, and run inheritance all count."""
    import httpx2 as _hx

    from .conftest import openai_response

    client = proxy(handler=lambda r: _hx.Response(200, json=openai_response()))
    _chat(client, "in-acme", run="acme-run")
    _chat(client, "outside")
    # File the run under acme; its session inherits.
    client.put("/api/labels/run/acme-run", json={"project": "acme"})

    full = client.get("/api/reports?days=7").json()
    scoped = client.get("/api/reports?days=7&project=acme").json()
    assert full["totals"]["call_count"] == 2
    assert scoped["totals"]["call_count"] == 1
    assert [p["project"] for p in scoped["projects"]] == ["acme"]

    # A run-default group scopes the same way.
    run_scoped = client.get("/api/reports?days=7&project=run:acme-run").json()
    assert run_scoped["totals"]["call_count"] == 1

    # An unknown project reads as an empty report, not an error.
    empty = client.get("/api/reports?days=7&project=nothing-here").json()
    assert empty["totals"]["call_count"] == 0
    assert empty["daily"] == [] and empty["models"] == []


def test_report_csv_scopes_too(proxy):
    import httpx2 as _hx

    from .conftest import openai_response

    client = proxy(handler=lambda r: _hx.Response(200, json=openai_response()))
    _chat(client, "csv-in", run="csv-run")
    _chat(client, "csv-out")
    client.put("/api/labels/run/csv-run", json={"project": "beta"})
    body = client.get("/api/reports.csv?days=7&project=beta").text
    # One model row for the scoped call; the unscoped session's traffic
    # is identical in shape, so scope shows via the period totals row.
    assert "gpt-4o" in body
    full = client.get("/api/reports.csv?days=7").text
    assert body != full
