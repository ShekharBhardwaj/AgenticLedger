"""AWS Bedrock's Converse wire (#111, the completion of #95): what modern
boto3 agents speak. Converse shapes convert to Anthropic shapes at the
boundary, so loop stitching, tool pairing, and pricing reuse the proven
machinery. Signing is shared with the InvokeModel wire."""

import httpx
import pytest

from agenticledger.proxy import providers
from agenticledger.proxy.normalize import normalize_request, normalize_response
from agenticledger.proxy.providers import eventstream as es
from agenticledger.proxy.providers.bedrock_converse import BedrockConverseProvider

MODEL = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
CONVERSE = f"model/{MODEL.replace(':', '%3A')}/converse"
STREAM = f"model/{MODEL.replace(':', '%3A')}/converse-stream"
BODY = {
    "messages": [{"role": "user", "content": [{"text": "ping"}]}],
    "system": [{"text": "You are the Converse worker."}],
    "inferenceConfig": {"maxTokens": 256, "temperature": 0.5},
    "toolConfig": {"tools": [{"toolSpec": {
        "name": "lookup", "description": "look a thing up",
        "inputSchema": {"json": {"type": "object"}}}}]},
}


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATESTKEY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)


def _converse_json():
    return {"output": {"message": {"role": "assistant", "content": [
                {"text": "pong"},
                {"toolUse": {"toolUseId": "t1", "name": "lookup", "input": {"q": "x"}}}]}},
            "stopReason": "tool_use",
            "usage": {"inputTokens": 1000, "outputTokens": 100,
                      "cacheReadInputTokens": 400, "cacheWriteInputTokens": 50},
            "metrics": {"latencyMs": 321}}


def _frame(etype, body):
    import json as _json
    return es.encode_frame({":event-type": etype, ":content-type": "application/json",
                            ":message-type": "event"}, _json.dumps(body).encode())


def _converse_stream(text="hi there"):
    return (_frame("messageStart", {"role": "assistant"})
            + _frame("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": text}})
            + _frame("contentBlockStop", {"contentBlockIndex": 0})
            + _frame("contentBlockStart", {"contentBlockIndex": 1, "start": {"toolUse": {"toolUseId": "t9", "name": "lookup"}}})
            + _frame("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"toolUse": {"input": "{\"q\":"}}})
            + _frame("contentBlockDelta", {"contentBlockIndex": 1, "delta": {"toolUse": {"input": "\"x\"}"}}})
            + _frame("contentBlockStop", {"contentBlockIndex": 1})
            + _frame("messageStop", {"stopReason": "tool_use"})
            + _frame("metadata", {"usage": {"inputTokens": 1000, "outputTokens": 100},
                                  "metrics": {"latencyMs": 200}}))


def test_registry_routes_converse_paths():
    assert providers.for_path(f"/{CONVERSE}").wire == "bedrock-converse"
    assert providers.for_path(f"/{STREAM}").wire == "bedrock-converse"
    assert providers.for_path(f"/r/night/3/{CONVERSE}").wire == "bedrock-converse"
    assert providers.streams(f"/{STREAM}", {}) is True
    assert providers.streams(f"/{CONVERSE}", {}) is False


def test_request_converts_to_anthropic_dialect():
    req = normalize_request(BODY, f"/{CONVERSE}")
    assert req.provider == "bedrock"
    assert req.model_id == MODEL
    assert req.system_prompt == "You are the Converse worker."
    assert req.max_tokens == 256 and req.temperature == 0.5
    assert req.tools == [{"name": "lookup", "description": "look a thing up",
                          "input_schema": {"type": "object"}}]
    user = req.messages[-1]
    assert user["content"] == [{"type": "text", "text": "ping"}]


def test_tool_results_pair_like_anthropics():
    body = {"messages": [
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "lookup", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "status": "error",
                                                     "content": [{"text": "boom"}]}}]},
    ]}
    req = normalize_request(body, f"/{CONVERSE}")
    assert req.tool_results == [{"tool_use_id": "t1",
                                 "content": [{"type": "text", "text": "boom"}],
                                 "is_error": True}]


def test_response_prices_with_cache_semantics():
    resp = normalize_response(_converse_json(), 321.0, MODEL)
    assert resp.content == "pong"
    assert resp.tool_calls == [{"id": "t1", "name": "lookup", "arguments": {"q": "x"}}]
    assert resp.stop_reason == "tool_use"
    assert resp.tokens_in == 1000 and resp.tokens_out == 100
    assert resp.cache_read_tokens == 400 and resp.cache_write_tokens == 50
    assert resp.cost_usd and resp.cost_usd > 0
    # Cache reads price cheaper than fresh input: the cached response must
    # cost less than the same counts priced as all-fresh tokens.
    fresh = normalize_response({**_converse_json(), "usage": {
        "inputTokens": 1450, "outputTokens": 100}}, 321.0, MODEL)
    assert resp.cost_usd < fresh.cost_usd


def test_stream_reconstructs_text_tools_and_usage():
    p = BedrockConverseProvider()
    resp = p.reconstruct_stream(_converse_stream("streamed pong"), 200.0, MODEL)
    assert resp.content == "streamed pong"
    assert resp.tool_calls == [{"id": "t9", "name": "lookup", "arguments": {"q": "x"}}]
    assert resp.stop_reason == "tool_use"
    assert resp.tokens_in == 1000 and resp.tokens_out == 100
    assert resp.cost_usd and resp.cost_usd > 0


def test_exception_frames_surface_as_stream_errors():
    p = BedrockConverseProvider()
    raw = es.encode_frame({":message-type": "exception", ":exception-type": "throttlingException"},
                          b'{"message": "Too many requests"}')
    assert p.stream_error(raw) == "Too many requests"


def test_converse_call_is_captured_end_to_end(proxy, aws_env):
    client = proxy(handler=lambda r: httpx.Response(200, json=_converse_json()))
    resp = client.post(f"/{CONVERSE}", json=BODY, headers={"x-agenticledger-session-id": "cv-1"})
    assert resp.status_code == 200
    assert resp.json()["output"]["message"]["content"][0]["text"] == "pong"  # passthrough
    record = client.get(f"/explain/{resp.headers['x-agenticledger-action-id']}").json()
    assert record["provider"] == "bedrock"
    assert record["model_id"] == MODEL
    assert record["cost_usd"] and record["cost_usd"] > 0


def test_streaming_converse_passes_bytes_and_captures(proxy, aws_env):
    raw = _converse_stream("streamed pong")
    client = proxy(handler=lambda r: httpx.Response(
        200, content=raw, headers={"content-type": "application/vnd.amazon.eventstream"}))
    resp = client.post(f"/{STREAM}", json=BODY, headers={"x-agenticledger-session-id": "cv-2"})
    assert resp.status_code == 200
    assert resp.content == raw
    rows = client.get("/session/cv-2").json()
    assert len(rows) == 1
    assert rows[0]["content"] == "streamed pong"
    assert rows[0]["tokens_in"] == 1000 and rows[0]["cost_usd"] > 0


def test_converse_is_signed_like_invoke(proxy, aws_env):
    seen = {}
    def handler(r):
        seen.update(dict(r.headers))
        return httpx.Response(200, json=_converse_json())
    client = proxy(handler=handler)
    client.post(f"/{CONVERSE}", json=BODY,
                headers={"authorization": "AWS4-HMAC-SHA256 Credential=CLIENTKEY/..."})
    assert "AKIATESTKEY" in seen.get("authorization", "")      # the ledger's own signature
    assert "CLIENTKEY" not in seen.get("authorization", "")    # the client's is stripped


def test_without_credentials_converse_is_refused_with_the_fix_named(proxy, monkeypatch):
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE",
                "AWS_SESSION_TOKEN", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(var, raising=False)
    client = proxy(handler=lambda r: httpx.Response(200, json=_converse_json()))
    resp = client.post(f"/{CONVERSE}", json=BODY)
    assert resp.status_code in (400, 502, 503)
    assert "credential" in resp.text.lower() or "aws" in resp.text.lower()
