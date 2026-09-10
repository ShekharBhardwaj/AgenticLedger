"""The compressor chain (docs/chaining.md): agent -> ledger -> second
proxy -> upstream. The docs claim the chain mechanics are proven by
test; this is that test, in-process with two REAL proxy apps: the
front ledger's upstream client routes into the back proxy's full ASGI
stack, and the back proxy forwards to a mock provider."""

import httpx2 as httpx

from agenticledger.proxy.app import create_app

REPLY = {"id": "c", "object": "chat.completion", "model": "gpt-4o",
         "choices": [{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": "through both hops"}}],
         "usage": {"prompt_tokens": 900, "completion_tokens": 30}}


def test_chain_records_on_both_hops_and_keeps_attribution(proxy, tmp_path):
    # The BACK proxy (the compressor stand-in): a full ledger against a
    # mock provider, built by the standard fixture.
    back = proxy(handler=lambda r: httpx.Response(200, json=REPLY))

    # The FRONT ledger: its upstream client routes into the back proxy's
    # ASGI stack, so bytes cross two complete proxy pipelines.
    front_app = create_app("http://back-proxy", f"sqlite:///{tmp_path}/front.db")
    from starlette.testclient import TestClient
    with TestClient(front_app) as front:
        front_app.state.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=back.app),
            base_url="http://back-proxy", timeout=httpx.Timeout(30.0))

        body = {"model": "gpt-4o",
                "messages": [{"role": "system", "content": "chain system prompt " * 40},
                             {"role": "user", "content": "prove the chain"}]}
        resp = front.post("/r/chain-proof/1/v1/chat/completions", json=body,
                          headers={"x-agenticledger-session-id": "chain-s"})
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "through both hops"

        # Front hop: recorded, priced, and the run identity held.
        runs = {r["run_id"]: r for r in front.get("/api/runs").json()}
        assert "chain-proof" in runs and runs["chain-proof"]["call_count"] == 1
        rows = front.get("/session/chain-s").json()
        assert len(rows) == 1 and rows[0]["cost_usd"] and rows[0]["cost_usd"] > 0

    # Back hop: the second proxy captured the same traffic independently.
    back_rows = back.get("/api/sessions").json()
    assert sum(s["call_count"] for s in back_rows) == 1
