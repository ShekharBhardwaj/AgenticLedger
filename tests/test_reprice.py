"""Repricing: calls recorded before their model had a price get one once
a price exists, from the dashboard button or the CLI, through one
endpoint. Priced calls are left alone."""

import httpx2 as httpx

from agenticledger.proxy import pricing

from .conftest import openai_response


def test_reprice_gives_unpriced_calls_a_cost(proxy, monkeypatch):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(model="mystery-model-x")))
    body = {"model": "mystery-model-x", "messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/v1/chat/completions", json=body,
                       headers={"x-agenticledger-session-id": "s"}).status_code == 200
    priced = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/v1/chat/completions", json=priced,
                       headers={"x-agenticledger-session-id": "s"}).status_code == 200
    rows = client.get("/session/s").json()
    before = {r["model_id"]: r["cost_usd"] for r in rows}
    assert before["mystery-model-x"] is None and before["gpt-4o"] is not None

    # Nothing to price yet: the model is still unknown.
    assert client.post("/api/pricing/reprice", headers={"sec-fetch-site": "same-origin"}).json() == {"repriced": 0}

    # A price arrives (a pack update or an override); the old call gets it.
    monkeypatch.setitem(pricing._PRICES, "mystery-model-x", (1.0, 2.0))
    assert client.post("/api/pricing/reprice", headers={"sec-fetch-site": "same-origin"}).json() == {"repriced": 1}
    after = {r["model_id"]: r["cost_usd"] for r in client.get("/session/s").json()}
    assert after["mystery-model-x"] == round(12 * 1.0 / 1e6 + 8 * 2.0 / 1e6, 6)
    assert after["gpt-4o"] == before["gpt-4o"]
    # Running it again changes nothing.
    assert client.post("/api/pricing/reprice", headers={"sec-fetch-site": "same-origin"}).json() == {"repriced": 0}
    details = [r["details"] for r in client.get("/api/audit").json() if r["action"] == "reprice"]
    assert "1 call priced" in details and "0 calls priced" in details
