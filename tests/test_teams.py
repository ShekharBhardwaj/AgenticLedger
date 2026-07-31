"""Team cards: ingest-role tokens that open the relay, attribute the team,
and enforce per-team daily budgets — the allowance-card model."""

import httpx

from tests.conftest import openai_response

_CHAT = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def _ok():
    return lambda r: httpx.Response(200, json=openai_response())


def _mint(client, name, budget=None):
    body = {"name": name, "role": "ingest"}
    if budget is not None:
        body["budget_daily"] = budget
    resp = client.post("/api/tokens", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def test_team_card_opens_relay_and_attributes(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "shared-secret")
    client = proxy(handler=_ok())
    card = _mint(client, "marketing")

    # No key at all → still locked out.
    assert client.post("/v1/chat/completions", json=_CHAT).status_code == 401
    # Wrong key → locked out.
    assert client.post("/v1/chat/completions", json=_CHAT,
                       headers={"x-agenticledger-ingest-key": "nope"}).status_code == 401
    # The team card opens the door…
    r = client.post("/v1/chat/completions", json=_CHAT,
                    headers={"x-agenticledger-ingest-key": card,
                             "x-agenticledger-session-id": "team-s1"})
    assert r.status_code == 200
    # …and stamps the team on the captured call.
    row = client.get("/session/team-s1").json()[0]
    assert row["team"] == "marketing"
    # The shared env key still works, with no team.
    r2 = client.post("/v1/chat/completions", json=_CHAT,
                     headers={"x-agenticledger-ingest-key": "shared-secret",
                              "x-agenticledger-session-id": "team-s2"})
    assert r2.status_code == 200
    assert client.get("/session/team-s2").json()[0]["team"] is None


def test_team_budget_blocks_that_team_only(proxy):
    client = proxy(handler=_ok())
    tight = _mint(client, "tight-team", budget=0.000001)
    loose = _mint(client, "loose-team", budget=100.0)

    first = client.post("/v1/chat/completions", json=_CHAT,
                        headers={"x-agenticledger-ingest-key": tight,
                                 "x-agenticledger-session-id": "tb-1"})
    assert first.status_code == 200
    second = client.post("/v1/chat/completions", json=_CHAT,
                         headers={"x-agenticledger-ingest-key": tight,
                                  "x-agenticledger-session-id": "tb-2"})
    assert second.status_code == 429
    assert "Team daily budget" in second.json()["error"]["message"]
    assert 0 < int(second.headers["retry-after"]) <= 86400
    # The other team is unaffected.
    other = client.post("/v1/chat/completions", json=_CHAT,
                        headers={"x-agenticledger-ingest-key": loose,
                                 "x-agenticledger-session-id": "tb-3"})
    assert other.status_code == 200


def test_viewer_token_is_not_a_team_card(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "shared-secret")
    client = proxy(handler=_ok())
    viewer = client.post("/api/tokens", json={"name": "reader", "role": "viewer"}).json()["token"]
    assert client.post("/v1/chat/completions", json=_CHAT,
                       headers={"x-agenticledger-ingest-key": viewer}).status_code == 401


def test_revoked_card_stops_working(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "shared-secret")
    client = proxy(handler=_ok())
    resp = client.post("/api/tokens", json={"name": "old-team", "role": "ingest"}).json()
    ok = client.post("/v1/chat/completions", json=_CHAT,
                     headers={"x-agenticledger-ingest-key": resp["token"]})
    assert ok.status_code == 200
    assert client.delete(f"/api/tokens/{resp['token_id']}").status_code == 200
    denied = client.post("/v1/chat/completions", json=_CHAT,
                         headers={"x-agenticledger-ingest-key": resp["token"]})
    assert denied.status_code == 401


def test_team_card_opens_otlp_gate(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "shared-secret")
    client = proxy(handler=_ok())
    card = _mint(client, "otel-team")
    span = {"resourceSpans": []}
    assert client.post("/v1/traces", json=span,
                       headers={"content-type": "application/json"}).status_code == 401
    assert client.post("/v1/traces", json=span,
                       headers={"content-type": "application/json",
                                "x-agenticledger-ingest-key": card}).status_code == 200


def test_budget_daily_rejected_for_non_ingest_roles(proxy):
    client = proxy()
    r = client.post("/api/tokens", json={"name": "x", "role": "viewer", "budget_daily": 5})
    assert r.status_code == 400


def test_reports_show_team_spend(proxy):
    client = proxy(handler=_ok())
    card = _mint(client, "research")
    client.post("/v1/chat/completions", json=_CHAT,
                headers={"x-agenticledger-ingest-key": card,
                         "x-agenticledger-session-id": "tr-1"})
    teams = client.get("/api/reports?days=1").json()["teams"]
    assert teams and teams[0]["team"] == "research"
    assert teams[0]["cost_usd"] > 0
