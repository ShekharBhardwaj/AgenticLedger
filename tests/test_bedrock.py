"""AWS Bedrock through the adapter contract (0.10 Phase B): the InvokeModel
wire, model in the path, Anthropic-shaped bodies, binary event streams.
Signing is the upstream layer's job and is tested there."""

import httpx

from agenticledger.proxy import providers
from agenticledger.proxy.normalize import normalize_request, normalize_response
from agenticledger.proxy.providers import eventstream as es
from agenticledger.proxy.stream import detect_stream_error, reconstruct_from_sse

MODEL = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
INVOKE = f"model/{MODEL.replace(':', '%3A')}/invoke"
STREAM = f"model/{MODEL.replace(':', '%3A')}/invoke-with-response-stream"
BODY = {"anthropic_version": "bedrock-2023-05-31", "max_tokens": 256,
        "system": "You are the Bedrock worker.",
        "messages": [{"role": "user", "content": "ping"}]}


def _anthropic_json(text="pong", tokens_in=1000, tokens_out=100):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": MODEL,
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out}}


def _event_stream(text="hi there"):
    return (es.encode_chunk({"type": "message_start", "message": {"usage": {"input_tokens": 1000, "output_tokens": 1}}})
            + es.encode_chunk({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            + es.encode_chunk({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
            + es.encode_chunk({"type": "content_block_stop", "index": 0})
            + es.encode_chunk({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 100}})
            + es.encode_chunk({"type": "message_stop"}))


def test_registry_routes_invoke_paths_to_bedrock():
    assert providers.for_path(INVOKE).wire == "bedrock-invoke"
    assert providers.for_path(STREAM).wire == "bedrock-invoke"
    assert providers.for_path(f"/r/night/3/{INVOKE}").wire == "bedrock-invoke"
    assert providers.captures(INVOKE) and providers.captures(STREAM)
    assert not providers.captures("model/x/foo")
    # Streaming is decided by the path, never by a body flag.
    assert providers.streams(STREAM, BODY) and not providers.streams(INVOKE, BODY)
    assert providers.for_path("v1/messages").wire == "anthropic-messages"


def test_request_takes_the_model_from_the_path():
    req = normalize_request(BODY, INVOKE)
    assert req.provider == "bedrock"
    assert req.model_id == MODEL                      # URL-decoded
    assert req.system_prompt == "You are the Bedrock worker."
    assert req.max_tokens == 256


def test_non_streaming_response_prices_as_the_claude_it_names():
    resp = normalize_response(_anthropic_json(), 12.0, MODEL)
    assert resp.content == "pong" and resp.tokens_in == 1000
    assert resp.cost_usd and resp.cost_usd > 0


def test_event_stream_reconstructs_and_prices():
    resp = reconstruct_from_sse(_event_stream(), 40.0, MODEL, path=STREAM)
    assert resp.content == "hi there"
    assert (resp.tokens_in, resp.tokens_out, resp.stop_reason) == (1000, 100, "end_turn")
    assert resp.cost_usd and resp.cost_usd > 0
    assert detect_stream_error(_event_stream(), path=STREAM) is None


def test_exception_frames_surface_as_stream_errors():
    raw = es.encode_chunk({"type": "message_start", "message": {"usage": {"input_tokens": 5}}}) \
        + es.encode_frame({":message-type": "exception", ":exception-type": "throttlingException"},
                          b'{"message": "Too many requests"}')
    assert detect_stream_error(raw, path=STREAM) == "Too many requests"


def test_invoke_call_is_captured_end_to_end(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=_anthropic_json()),
                   upstream_url="https://bedrock-runtime.us-east-1.amazonaws.com")
    resp = client.post(f"/{INVOKE}", json=BODY, headers={"x-agenticledger-session-id": "br-1"})
    assert resp.status_code == 200
    assert resp.json()["content"][0]["text"] == "pong"   # passthrough unchanged
    record = client.get(f"/explain/{resp.headers['x-agenticledger-action-id']}").json()
    assert record["provider"] == "bedrock"
    assert record["model_id"] == MODEL
    assert record["cost_usd"] and record["cost_usd"] > 0


def test_streaming_invoke_passes_bytes_through_and_captures_the_text(proxy):
    raw = _event_stream("streamed pong")
    client = proxy(handler=lambda r: httpx.Response(
        200, content=raw, headers={"content-type": "application/vnd.amazon.eventstream"}),
        upstream_url="https://bedrock-runtime.us-east-1.amazonaws.com")
    resp = client.post(f"/{STREAM}", json=BODY, headers={"x-agenticledger-session-id": "br-2"})
    assert resp.status_code == 200
    assert resp.content == raw                           # the client decodes the frames itself
    rows = client.get("/session/br-2").json()
    assert len(rows) == 1
    assert rows[0]["content"] == "streamed pong"
    assert rows[0]["tokens_in"] == 1000 and rows[0]["cost_usd"] > 0
