"""The cache audit (#113): exact where the provider reported, a
stated-method estimate where it never did, and no number without a why
and a do-this. The pure function is tested without a machine; the
endpoint through the proxy fixture."""

import httpx2 as httpx

from agenticledger.proxy.cache_audit import audit_run

BIG = "You are the overnight worker. " * 400   # ~12,000 chars, ~3,000 est tokens


def _call(provider="anthropic", model="claude-sonnet-4-5", system=BIG,
          reads=0, writes=0, status=200):
    return {"provider": provider, "model_id": model, "system_prompt": system,
            "cache_read_tokens": reads, "cache_write_tokens": writes,
            "status_code": status}


def test_never_requested_estimates_with_stated_method():
    result = audit_run([_call(), _call(), _call()])
    assert result["verdict"] == "never_requested"
    assert "never requested" in result["reason"]
    assert "cache_control" in result["fix"]
    e = result["eligible"]
    assert e["occurrences"] == 3
    assert e["estimated_tokens"] == len(BIG) // 4
    assert "chars per token" in e["method"]
    # (occurrences-1) x est_tokens x (input - cache_read) / 1M, from the
    # same pricing table compute_cost uses. Sonnet 4.5: 3.0 in, 0.3 read.
    expected = 2 * (len(BIG) // 4) * (3.0 - 0.3) / 1_000_000
    assert abs(e["estimated_usd"] - round(expected, 4)) < 1e-9


def test_well_cached_says_you_are_fine():
    result = audit_run([_call(writes=3000), _call(reads=3000), _call(reads=3000)])
    assert result["verdict"] == "well_cached"
    assert result["fix"] is None
    # Received is EXACT: 6000 read tokens x (3.0 - 0.3) / 1M.
    assert abs(result["received_usd"] - round(6000 * 2.7 / 1_000_000, 4)) < 1e-9


def test_unstable_opening_shows_the_divergence():
    a = _call(system="run at 08:00 · " + BIG)
    b = _call(system="run at 08:05 · " + BIG)
    result = audit_run([a, b])
    assert result["verdict"] == "unstable_opening"
    assert "character" in result["reason"]
    assert "below the stable prefix" in result["fix"]


def test_too_short_is_honestly_fine():
    result = audit_run([_call(system="short prompt"), _call(system="short prompt")])
    assert result["verdict"] == "too_short"
    assert result["fix"] is None


def test_stripped_prompts_are_not_auditable():
    result = audit_run([_call(system=""), _call(system="")])
    assert result["verdict"] == "not_auditable"
    assert "capture level" in result["fix"]


def test_openai_auto_caching_fix_never_mentions_flags():
    result = audit_run([_call(provider="openai", model="gpt-4o"),
                        _call(provider="openai", model="gpt-4o")])
    assert result["verdict"] == "never_requested"
    assert "cache window" in result["fix"]


def test_endpoint_audits_a_real_run(proxy):
    body = {"model": "claude-sonnet-4-5", "max_tokens": 64, "system": BIG,
            "messages": [{"role": "user", "content": "go"}]}
    reply = {"id": "m", "type": "message", "role": "assistant",
             "model": "claude-sonnet-4-5",
             "content": [{"type": "text", "text": "ok"}],
             "stop_reason": "end_turn",
             "usage": {"input_tokens": 3100, "output_tokens": 5}}
    client = proxy(handler=lambda r: httpx.Response(200, json=reply))
    for i in (1, 2):
        assert client.post(f"/r/audit-me/{i}/v1/messages", json=body).status_code == 200
    audit = client.get("/api/runs/audit-me/cache-audit").json()
    assert audit["run_id"] == "audit-me"
    assert audit["verdict"] == "never_requested"
    assert audit["eligible"]["occurrences"] == 2
    assert audit["eligible"]["estimated_usd"] > 0
    assert client.get("/api/runs/nope/cache-audit").status_code == 404
