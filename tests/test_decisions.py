"""OpenRouter's Decisions wire (Jev): recorded like any other model call,
with the charged cost from the response and the SDK's /api/alpha/... path
landing on the upstream without a doubled prefix."""

import json

import httpx2 as httpx

DECISION = {
    "id": "dec_1",
    "model": "typesafe/jev-1.13",
    "provider": "TypeSafe",
    "answers": {"decision": {"type": "choice", "choice": "refer", "confidence": 0.91,
                             "probabilities": {"approve": 0.05, "decline": 0.04, "refer": 0.91}}},
    "usage": {"input_tokens": 1800, "output_tokens": 0, "cost": 0.0000756},
}

REQUEST = {
    "model": "typesafe/jev-1.13",
    "state": {"claim": {"claim_id": "C-1", "policy_id": "P-9"}, "policy": {"cover": "medical"}},
    "questions": {"decision": {"type": "choice", "instructions": "Decide the claim.",
                               "criteria": {"approve": "covered", "decline": "not covered",
                                            "refer": "needs a colleague"}}},
}


def test_decisions_call_is_recorded_with_the_charged_cost(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=DECISION),
                   upstream_url="https://openrouter.ai/api")
    resp = client.post("/api/alpha/decisions", json=REQUEST,
                       headers={"x-agenticledger-session-id": "claims", "x-agenticledger-run-id": "jev-batch"})
    assert resp.status_code == 200 and resp.json() == DECISION
    assert client.upstream.last_json()["model"] == "typesafe/jev-1.13"

    rows = client.get("/session/claims").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "openrouter" and row["model_id"] == "typesafe/jev-1.13"
    assert row["run_id"] == "jev-batch"
    assert row["tokens_in"] == 1800 and row["tokens_out"] == 0
    assert row["cost_usd"] == 0.0000756          # what OpenRouter charged, not an estimate
    assert "refer (confidence 0.91)" in row["content"]
    assert "decision (choice): Decide the claim." in row["system_prompt"]
    assert json.loads(row["messages"][-1]["content"])["claim"]["claim_id"] == "C-1"


def test_decisions_without_a_charged_cost_fall_back_to_the_price_table(proxy):
    body = dict(DECISION, usage={"input_tokens": 1000, "output_tokens": 0})
    client = proxy(handler=lambda r: httpx.Response(200, json=body),
                   upstream_url="https://openrouter.ai/api")
    assert client.post("/api/alpha/decisions", json=REQUEST,
                       headers={"x-agenticledger-session-id": "s2"}).status_code == 200
    row = client.get("/session/s2").json()[0]
    assert row["cost_usd"] == round(1000 * 0.042 / 1e6, 6)   # the jev pack rate


def test_the_upstream_path_never_doubles_a_prefix_the_base_already_has():
    """The Decisions SDK sends /api/alpha/decisions; an OpenRouter upstream is
    already .../api. Chat calls (/v1/...) and other upstreams are untouched."""
    from agenticledger.proxy.app import _upstream_path
    openrouter = httpx.AsyncClient(base_url="https://openrouter.ai/api")
    plain = httpx.AsyncClient(base_url="https://upstream.test")
    assert _upstream_path(openrouter, "api/alpha/decisions") == "/alpha/decisions"
    assert _upstream_path(openrouter, "v1/chat/completions") == "/v1/chat/completions"
    assert _upstream_path(plain, "api/alpha/decisions") == "/api/alpha/decisions"
    assert _upstream_path(plain, "v1/messages") == "/v1/messages"
    assert _upstream_path(openrouter, "api") == "/api"   # nothing after the segment: leave it
