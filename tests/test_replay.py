"""Replay: re-executing captured calls against a live (mock) model."""

import httpx

from tests.conftest import anthropic_response, openai_response

_CHAT = {"model": "gpt-4o", "temperature": 0.3,
         "messages": [{"role": "system", "content": "Be terse."},
                      {"role": "user", "content": "hello"}]}


def _capture_one(client, body=None, headers=None):
    resp = client.post("/v1/chat/completions", json=body or _CHAT,
                       headers={"x-agenticledger-session-id": "cap-1",
                                "x-agenticledger-agent-name": "researcher",
                                **(headers or {})})
    assert resp.status_code == 200
    return resp.headers["x-agenticledger-action-id"]


def test_replay_reruns_captured_call(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   replay_api_key="rk-123")
    action_id = _capture_one(client)

    out = client.post("/api/replay", json={"action_id": action_id})
    assert out.status_code == 200
    data = out.json()

    # The upstream got the rebuilt request with the replay credential,
    # not the agent's (never stored) auth.
    sent = client.upstream.last_request
    assert sent.headers["authorization"] == "Bearer rk-123"
    body = client.upstream.last_json()
    assert body["model"] == "gpt-4o"
    assert body["messages"] == _CHAT["messages"]
    assert body["temperature"] == 0.3
    assert "stream" not in body

    # Result priced and linked.
    assert data["replay"]["cost_usd"] > 0
    assert data["original"]["action_id"] == action_id
    rows = client.get(f"/session/replay-{action_id[:8]}").json()
    assert len(rows) == 1
    assert rows[0]["framework"] == "replay"
    assert rows[0]["parent_action_id"] == action_id
    assert rows[0]["agent_name"] == "researcher"


def test_replay_model_swap_same_provider(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(model="gpt-4o-mini")),
                   replay_api_key="rk-123")
    action_id = _capture_one(client)

    out = client.post("/api/replay", json={"action_id": action_id, "model": "gpt-4o-mini"})
    assert out.status_code == 200
    assert client.upstream.last_json()["model"] == "gpt-4o-mini"
    assert out.json()["replay"]["model_id"] == "gpt-4o-mini"


def test_replay_anthropic_rebuilds_system_key(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   replay_api_key="rk-a")
    resp = client.post("/v1/messages",
                       json={"model": "claude-sonnet-4", "max_tokens": 2048,
                             "system": "You are terse.",
                             "messages": [{"role": "user", "content": "hi"}]},
                       headers={"x-agenticledger-session-id": "cap-a"})
    action_id = resp.headers["x-agenticledger-action-id"]

    out = client.post("/api/replay", json={"action_id": action_id})
    assert out.status_code == 200
    body = client.upstream.last_json()
    # System went back to the top-level key, not a messages entry.
    assert body["system"] == "You are terse."
    assert all(m["role"] != "system" for m in body["messages"])
    assert body["max_tokens"] == 2048
    sent = client.upstream.last_request
    assert sent.headers["x-api-key"] == "rk-a"
    assert "authorization" not in sent.headers


def test_replay_off_without_key(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    action_id = _capture_one(client)
    out = client.post("/api/replay", json={"action_id": action_id})
    assert out.status_code == 409
    assert "AGENTICLEDGER_REPLAY_API_KEY" in out.json()["error"]


def test_replay_unknown_action_404(proxy):
    client = proxy(replay_api_key="rk")
    assert client.post("/api/replay", json={"action_id": "nope"}).status_code == 404


def test_replay_rejects_metadata_only_capture(proxy):
    client = proxy(replay_api_key="rk")
    # OTLP-ingested calls are metadata-level: no messages stored.
    span = {
        "resourceSpans": [{"scopeSpans": [{"spans": [{
            "traceId": "0af7651916cd43dd8448eb211c80319c",
            "spanId": "d7ad6b7169203331",
            "name": "chat gpt-4o",
            "startTimeUnixNano": "1753500000000000000",
            "endTimeUnixNano": "1753500001000000000",
            "attributes": [
                {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
                {"key": "gen_ai.conversation.id", "value": {"stringValue": "otlp-r"}},
            ],
        }]}]}]
    }
    assert client.post("/v1/traces", json=span,
                       headers={"content-type": "application/json"}).status_code == 200
    action_id = client.get("/session/otlp-r").json()[0]["action_id"]
    out = client.post("/api/replay", json={"action_id": action_id})
    assert out.status_code == 400
    assert "Not replayable" in out.json()["error"]


def test_replay_upstream_error_surfaces_as_502(proxy):
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=openai_response())
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    client = proxy(handler=handler, replay_api_key="rk-bad")
    action_id = _capture_one(client)
    out = client.post("/api/replay", json={"action_id": action_id})
    assert out.status_code == 502
    assert out.json()["upstream_status"] == 401
