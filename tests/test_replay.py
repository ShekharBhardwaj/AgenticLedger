"""Replay: re-executing captured calls against a live (mock) model."""

import httpx2 as httpx

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


def test_replay_preserves_block_form_system(proxy):
    """Issue #25: Claude Code sends system as content blocks — replay must
    carry them through verbatim, not drop the system prompt."""
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   replay_api_key="rk")
    blocks = [{"type": "text", "text": "Be terse.",
               "cache_control": {"type": "ephemeral"}}]
    resp = client.post("/v1/messages",
                       json={"model": "claude-sonnet-4", "max_tokens": 64,
                             "system": blocks,
                             "messages": [{"role": "user", "content": "hi"}]},
                       headers={"x-agenticledger-session-id": "cap-blocks"})
    action_id = resp.headers["x-agenticledger-action-id"]

    out = client.post("/api/replay", json={"action_id": action_id})
    assert out.status_code == 200
    body = client.upstream.last_json()
    assert body["system"] == blocks
    assert all(m["role"] != "system" for m in body["messages"])


# ── Cross-provider replay ────────────────────────────────────────────────────

from tests.conftest import UPSTREAM_URL  # noqa: E402


def _wire_target(client, provider):
    """Point a configured replay target's client at the test's mock upstream."""
    client.app.state.replay_clients[provider] = httpx.AsyncClient(
        transport=httpx.MockTransport(client.upstream), base_url=UPSTREAM_URL,
    )


def test_translator_anthropic_to_openai_round_trip():
    from agenticledger.proxy.replay import build_cross_request
    record = {
        "provider": "anthropic",
        "max_tokens": 512,
        "temperature": 0.2,
        "tools": [{"name": "bash", "description": "run", "input_schema":
                   {"type": "object", "properties": {"cmd": {"type": "string"}}}}],
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": "Be terse."}]},
            {"role": "user", "content": "list files"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Running."},
                {"type": "tool_use", "id": "tu1", "name": "bash", "input": {"cmd": "ls"}},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "a.txt"},
                {"type": "text", "text": "now summarize"},
            ]},
        ],
    }
    path, body = build_cross_request(record, "gpt-4o-mini", "openai")
    assert path == "v1/chat/completions"
    msgs = body["messages"]
    assert msgs[0] == {"role": "system", "content": "Be terse."}
    assert msgs[1] == {"role": "user", "content": "list files"}
    assert msgs[2]["tool_calls"][0]["function"] == {"name": "bash", "arguments": '{"cmd": "ls"}'}
    assert msgs[3] == {"role": "tool", "tool_call_id": "tu1", "content": "a.txt"}
    assert msgs[4] == {"role": "user", "content": "now summarize"}
    assert body["tools"][0]["function"]["parameters"]["properties"]["cmd"]["type"] == "string"
    assert body["max_tokens"] == 512 and body["temperature"] == 0.2


def test_translator_openai_to_anthropic_round_trip():
    from agenticledger.proxy.replay import build_cross_request
    record = {
        "provider": "openai",
        "tools": [{"type": "function", "function": {
            "name": "search", "description": "find", "parameters": {"type": "object"}}}],
        "messages": [
            {"role": "system", "content": "Be terse."},
            {"role": "user", "content": "find docs"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "search", "arguments": '{"q": "docs"}'}},
                {"id": "c2", "type": "function",
                 "function": {"name": "search", "arguments": '{"q": "more"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "found A"},
            {"role": "tool", "tool_call_id": "c2", "content": "found B"},
            {"role": "user", "content": "summarize"},
        ],
    }
    path, body = build_cross_request(record, "claude-haiku-4-5", "anthropic")
    assert path == "v1/messages"
    assert body["system"] == "Be terse."
    msgs = body["messages"]
    assert msgs[0] == {"role": "user", "content": "find docs"}
    assert [b["type"] for b in msgs[1]["content"]] == ["tool_use", "tool_use"]
    assert msgs[1]["content"][0]["input"] == {"q": "docs"}
    # Both tool answers merged into ONE anthropic user turn.
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["c1", "c2"]
    assert msgs[3] == {"role": "user", "content": "summarize"}
    assert body["tools"][0]["input_schema"] == {"type": "object"}
    assert body["max_tokens"] == 4096


def test_translator_refuses_images():
    import pytest as _pytest

    from agenticledger.proxy.replay import NotTranslatable, build_cross_request
    record = {"provider": "anthropic", "messages": [
        {"role": "user", "content": [{"type": "image", "source": {}}]}]}
    with _pytest.raises(NotTranslatable):
        build_cross_request(record, "gpt-4o", "openai")


def test_cross_replay_anthropic_capture_on_openai_target(proxy):
    """The flagship path: a captured Claude call replayed on an OpenAI-style
    target (exactly the LM Studio free-local shape)."""
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   replay_targets={"openai": {"url": UPSTREAM_URL, "key": "lm-studio"}})
    _wire_target(client, "openai")
    resp = client.post("/v1/messages",
                       json={"model": "claude-sonnet-4", "max_tokens": 128,
                             "system": "Be terse.",
                             "messages": [{"role": "user", "content": "hi"}]},
                       headers={"x-agenticledger-session-id": "x-cap"})
    action_id = resp.headers["x-agenticledger-action-id"]

    client.upstream.set(lambda r: httpx.Response(200, json=openai_response(model="qwen3-local")))
    out = client.post("/api/replay", json={"action_id": action_id, "model": "qwen3-local",
                                           "provider": "openai"})
    assert out.status_code == 200, out.text
    sent = client.upstream.last_request
    assert sent.url.path.endswith("/v1/chat/completions")
    assert sent.headers["authorization"] == "Bearer lm-studio"
    body = client.upstream.last_json()
    assert body["model"] == "qwen3-local"
    assert body["messages"][0] == {"role": "system", "content": "Be terse."}
    data = out.json()
    assert data["replay"]["provider"] == "openai"
    # Stored as a real call under the target provider.
    row = client.get(f"/session/replay-{action_id[:8]}").json()[0]
    assert row["provider"] == "openai"
    assert row["framework"] == "replay"


def test_cross_replay_provider_inferred_from_model_name(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   replay_targets={"anthropic": {"url": UPSTREAM_URL, "key": "rk-a"}})
    _wire_target(client, "anthropic")
    action_id = _capture_one(client)

    client.upstream.set(lambda r: httpx.Response(200, json=anthropic_response()))
    out = client.post("/api/replay", json={"action_id": action_id,
                                           "model": "claude-haiku-4-5"})
    assert out.status_code == 200, out.text
    sent = client.upstream.last_request
    assert sent.url.path.endswith("/v1/messages")
    assert sent.headers["x-api-key"] == "rk-a"
    assert client.upstream.last_json()["system"] == "Be terse."


def test_cross_replay_without_target_gets_actionable_409(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   replay_api_key="rk-same-provider-only")
    action_id = _capture_one(client)
    out = client.post("/api/replay", json={"action_id": action_id,
                                           "model": "claude-haiku-4-5"})
    assert out.status_code == 409
    assert "AGENTICLEDGER_REPLAY_ANTHROPIC_KEY" in out.json()["error"]


# ── 0.7 fix round ─────────────────────────────────────────────────────────────

def test_auto_routes_unknown_model_to_sole_target(proxy):
    """#37: unrecognized model name + the capture's provider can't replay +
    exactly one target configured → it obviously goes there."""
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   replay_targets={"openai": {"url": UPSTREAM_URL, "key": "lm-studio"}})
    _wire_target(client, "openai")
    resp = client.post("/v1/messages",
                       json={"model": "claude-sonnet-4", "max_tokens": 64,
                             "messages": [{"role": "user", "content": "hi"}]},
                       headers={"x-agenticledger-session-id": "auto-cap"})
    action_id = resp.headers["x-agenticledger-action-id"]

    client.upstream.set(lambda r: httpx.Response(200, json=openai_response(model="qwen-local")))
    r = client.post("/api/replay", json={"action_id": action_id, "model": "qwen3.6-35b-a3b"})
    assert r.status_code == 200, r.text
    assert r.json()["replay"]["provider"] == "openai"
    assert client.upstream.last_request.url.path == "/v1/chat/completions"


def test_replay_targets_endpoint_names_destinations(proxy):
    """#38: the dashboard can ask where replays can go."""
    client = proxy(replay_targets={
        "openai": {"url": "http://localhost:1234", "key": "lm-studio"}})
    body = client.get("/api/replay/targets").json()
    assert body["targets"] == [
        {"provider": "openai", "host": "localhost:1234", "local": True}]
    assert body["same_provider"] is False


def test_replay_models_endpoint_lists_target_models(proxy):
    """#40: the model box can offer what the target actually serves."""
    def handler(r):
        if r.url.path == "/v1/models" and r.method == "GET":
            return httpx.Response(200, json={"data": [
                {"id": "qwen/qwen3.6-35b-a3b"}, {"id": "embed-mini"}]})
        return httpx.Response(200, json=openai_response())
    client = proxy(handler=handler,
                   replay_targets={"openai": {"url": UPSTREAM_URL, "key": "lm-studio"}})
    _wire_target(client, "openai")
    body = client.get("/api/replay/models?provider=openai").json()
    assert body["models"] == ["qwen/qwen3.6-35b-a3b", "embed-mini"]
    assert client.get("/api/replay/models?provider=anthropic").status_code == 404


def test_get_call_by_id(proxy):
    """#43: a replay's parent_action_id can be resolved back to its session."""
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    resp = client.post("/v1/chat/completions",
                       json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                       headers={"x-agenticledger-session-id": "orig-sess"})
    action_id = resp.headers["x-agenticledger-action-id"]
    row = client.get(f"/api/calls/{action_id}").json()
    assert row["session_id"] == "orig-sess"
    assert client.get("/api/calls/00000000-0000-0000-0000-000000000000").status_code == 404
