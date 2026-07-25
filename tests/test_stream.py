"""Tests for agentledger/proxy/stream.py — SSE chunk accumulation.

`reconstruct_from_sse` turns raw SSE bytes (OpenAI-format *or* Anthropic-native)
into a CanonicalResponse. These tests assert the intended reconstruction
behavior described in the module docstring and the task spec:

* OpenAI: content deltas concatenated; usage -> tokens; finish_reason ->
  stop_reason; cost computed for a known model.
* OpenAI tool-call streaming: function.name + incremental function.arguments
  accumulated by index into one tool_call with full arguments and id.
* Anthropic native SSE: format auto-detected; text_delta concatenated;
  input_json_delta accumulated into tool_use arguments; message_start
  input_tokens; message_delta output_tokens + stop_reason.
* `[DONE]` terminates; malformed/non-JSON data lines skipped; empty stream
  -> content None.
"""

import json

from agentledger.proxy.pricing import compute_cost
from agentledger.proxy.stream import detect_stream_error, reconstruct_from_sse

from .conftest import anthropic_sse, openai_sse, sse

# ── OpenAI SSE: content / usage / finish_reason / cost ───────────────────────

def test_openai_content_deltas_concatenated():
    """Per-character OpenAI content deltas are joined into the final content."""
    resp = reconstruct_from_sse(openai_sse("hello"), latency_ms=5.0, model_id="gpt-4o")
    assert resp.content == "hello"


def test_openai_usage_maps_to_tokens_in_out():
    """OpenAI usage.prompt_tokens/completion_tokens map to tokens_in/tokens_out."""
    resp = reconstruct_from_sse(
        openai_sse("hi", prompt_tokens=11, completion_tokens=4),
        latency_ms=1.0,
        model_id="gpt-4o",
    )
    assert resp.tokens_in == 11
    assert resp.tokens_out == 4


def test_openai_finish_reason_maps_to_stop_reason():
    """OpenAI finish_reason is captured as the CanonicalResponse.stop_reason."""
    resp = reconstruct_from_sse(openai_sse("x"), latency_ms=1.0, model_id="gpt-4o")
    assert resp.stop_reason == "stop"


def test_openai_finish_reason_non_default():
    """A non-'stop' finish_reason (e.g. length) is propagated verbatim."""
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": "abc"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}],
         "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="gpt-4o")
    assert resp.stop_reason == "length"


def test_openai_cost_computed_for_known_model():
    """Cost is computed from the pricing table for a recognized model id."""
    resp = reconstruct_from_sse(
        openai_sse("hi", prompt_tokens=1000, completion_tokens=500),
        latency_ms=1.0,
        model_id="gpt-4o",
    )
    expected = compute_cost("gpt-4o", 1000, 500)
    assert expected is not None
    assert resp.cost_usd == expected


def test_openai_cost_none_for_unknown_model():
    """An unknown model id yields no cost (None)."""
    resp = reconstruct_from_sse(
        openai_sse("hi", prompt_tokens=10, completion_tokens=5),
        latency_ms=1.0,
        model_id="totally-unknown-model",
    )
    assert resp.cost_usd is None


def test_openai_latency_passed_through():
    """The provided latency_ms ends up on the CanonicalResponse unchanged."""
    resp = reconstruct_from_sse(openai_sse("a"), latency_ms=42.5, model_id="gpt-4o")
    assert resp.latency_ms == 42.5


def test_openai_no_tool_calls_when_only_text():
    """A pure-text OpenAI stream produces tool_calls == None."""
    resp = reconstruct_from_sse(openai_sse("hello"), latency_ms=1.0, model_id="gpt-4o")
    assert resp.tool_calls is None


# ── OpenAI tool-call streaming ───────────────────────────────────────────────

def test_openai_tool_call_accumulated_by_index():
    """name comes in one delta, arguments stream across chunks; both accumulate
    by index into a single tool_call with full arguments string and id."""
    chunks = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_abc", "function": {"name": "get_weather", "arguments": ""}}
        ]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"ci'}}
        ]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'ty":"SF"}'}}
        ]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
         "usage": {"prompt_tokens": 7, "completion_tokens": 9}},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="gpt-4o")

    assert resp.tool_calls is not None
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc["id"] == "call_abc"
    assert tc["name"] == "get_weather"
    assert tc["arguments"] == '{"city":"SF"}'
    assert json.loads(tc["arguments"]) == {"city": "SF"}
    assert resp.stop_reason == "tool_calls"
    # tool-call stream has no text content
    assert resp.content is None


def test_openai_multiple_tool_calls_by_index():
    """Two parallel tool calls (index 0 and 1) are reconstructed independently."""
    chunks = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_0", "function": {"name": "f0", "arguments": '{"a":1}'}}
        ]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 1, "id": "call_1", "function": {"name": "f1", "arguments": '{"b":2}'}}
        ]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="gpt-4o")
    assert resp.tool_calls is not None
    by_id = {tc["id"]: tc for tc in resp.tool_calls}
    assert by_id["call_0"]["name"] == "f0"
    assert by_id["call_0"]["arguments"] == '{"a":1}'
    assert by_id["call_1"]["name"] == "f1"
    assert by_id["call_1"]["arguments"] == '{"b":2}'


def test_openai_tool_call_id_persists_across_argument_chunks():
    """The id supplied only in the first delta is retained even though later
    argument deltas omit it."""
    chunks = [
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_xyz", "function": {"name": "do", "arguments": ""}}
        ]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{}"}}
        ]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="gpt-4o")
    assert resp.tool_calls[0]["id"] == "call_xyz"
    assert resp.tool_calls[0]["arguments"] == "{}"


# ── Anthropic native SSE (auto-detected via "type") ──────────────────────────

def test_anthropic_format_auto_detected_text():
    """An Anthropic-native stream (chunks carry 'type') is detected and its
    text_delta blocks are concatenated into content."""
    resp = reconstruct_from_sse(anthropic_sse("world"), latency_ms=2.0,
                                model_id="claude-3-5-sonnet")
    assert resp.content == "world"


def test_anthropic_message_start_input_tokens():
    """message_start.message.usage.input_tokens becomes tokens_in."""
    resp = reconstruct_from_sse(
        anthropic_sse("hi", input_tokens=21, output_tokens=3),
        latency_ms=1.0,
        model_id="claude-3-5-sonnet",
    )
    assert resp.tokens_in == 21


def test_anthropic_message_delta_output_tokens_and_stop_reason():
    """message_delta carries output_tokens and stop_reason."""
    resp = reconstruct_from_sse(
        anthropic_sse("hi", input_tokens=5, output_tokens=8),
        latency_ms=1.0,
        model_id="claude-3-5-sonnet",
    )
    assert resp.tokens_out == 8
    assert resp.stop_reason == "end_turn"


def test_anthropic_cost_computed_for_known_model():
    """Cost is computed for a recognized Anthropic model id."""
    resp = reconstruct_from_sse(
        anthropic_sse("hi", input_tokens=1000, output_tokens=200),
        latency_ms=1.0,
        model_id="claude-3-5-sonnet",
    )
    expected = compute_cost("claude-3-5-sonnet", 1000, 200)
    assert expected is not None
    assert resp.cost_usd == expected


def test_anthropic_input_json_delta_accumulated_into_tool_use():
    """input_json_delta fragments accumulate into the tool_use's arguments,
    keyed off the content_block_start id/name, finalized at content_block_stop."""
    chunks = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_weather"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"ci'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": 'ty":"SF"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 12}},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="claude-3-5-sonnet")

    assert resp.tool_calls is not None
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc["id"] == "toolu_1"
    assert tc["name"] == "get_weather"
    assert tc["arguments"] == '{"city":"SF"}'
    assert resp.stop_reason == "tool_use"
    assert resp.tokens_out == 12
    # no text blocks → content None
    assert resp.content is None


def test_anthropic_mixed_text_then_tool_use():
    """A stream with a text block followed by a tool_use block produces both
    content and a tool_call."""
    chunks = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 4}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "Let me check. "}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "toolu_2", "name": "lookup"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"q":"x"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 6}},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="claude-3-5-sonnet")
    assert resp.content == "Let me check. "
    assert resp.tool_calls is not None and len(resp.tool_calls) == 1
    assert resp.tool_calls[0] == {"id": "toolu_2", "name": "lookup", "arguments": '{"q":"x"}'}


def test_anthropic_two_tool_uses_finalized_separately():
    """Two consecutive tool_use blocks are each finalized at their own
    content_block_stop into separate tool_calls."""
    chunks = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 4}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "t0", "name": "a"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"x":1}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "t1", "name": "b"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"y":2}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 9}},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="claude-3-5-sonnet")
    assert [tc["id"] for tc in resp.tool_calls] == ["t0", "t1"]
    assert resp.tool_calls[0]["arguments"] == '{"x":1}'
    assert resp.tool_calls[1]["arguments"] == '{"y":2}'


# ── Termination / robustness / empty streams ─────────────────────────────────

def test_done_terminates_stream():
    """Data lines after the literal [DONE] sentinel are ignored."""
    pre = {"choices": [{"index": 0, "delta": {"content": "kept"}, "finish_reason": None}]}
    post = {"choices": [{"index": 0, "delta": {"content": "DROPPED"}, "finish_reason": "stop"}]}
    raw = (
        f"data: {json.dumps(pre)}\n\n"
        "data: [DONE]\n\n"
        f"data: {json.dumps(post)}\n\n"
    ).encode("utf-8")
    resp = reconstruct_from_sse(raw, latency_ms=1.0, model_id="gpt-4o")
    assert resp.content == "kept"
    # the post-[DONE] finish_reason must NOT have been read
    assert resp.stop_reason is None


def test_malformed_json_lines_skipped():
    """Non-JSON data lines are silently skipped; valid chunks still processed."""
    good1 = {"choices": [{"index": 0, "delta": {"content": "A"}, "finish_reason": None}]}
    good2 = {"choices": [{"index": 0, "delta": {"content": "B"}, "finish_reason": "stop"}]}
    raw = (
        f"data: {json.dumps(good1)}\n\n"
        "data: {not valid json at all\n\n"
        "data: \n\n"
        f"data: {json.dumps(good2)}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")
    resp = reconstruct_from_sse(raw, latency_ms=1.0, model_id="gpt-4o")
    assert resp.content == "AB"
    assert resp.stop_reason == "stop"


def test_non_data_lines_ignored():
    """SSE comment/event lines (not starting with 'data: ') are ignored."""
    chunk = {"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}]}
    raw = (
        ": this is an SSE comment\n"
        "event: message\n"
        f"data: {json.dumps(chunk)}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")
    resp = reconstruct_from_sse(raw, latency_ms=1.0, model_id="gpt-4o")
    assert resp.content == "hi"


def test_empty_stream_content_none():
    """A stream with no content deltas yields content None (not '')."""
    chunks = [
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 3, "completion_tokens": 0}},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="gpt-4o")
    assert resp.content is None
    assert resp.tool_calls is None
    assert resp.tokens_in == 3
    assert resp.tokens_out == 0


def test_completely_empty_body():
    """An empty byte body produces an all-empty OpenAI CanonicalResponse."""
    resp = reconstruct_from_sse(b"", latency_ms=3.0, model_id="gpt-4o")
    assert resp.content is None
    assert resp.tool_calls is None
    assert resp.stop_reason is None
    assert resp.tokens_in is None
    assert resp.tokens_out is None
    assert resp.cost_usd is None
    assert resp.latency_ms == 3.0


def test_only_done_sentinel():
    """A stream that is just [DONE] reconstructs to an empty response."""
    resp = reconstruct_from_sse(b"data: [DONE]\n\n", latency_ms=1.0, model_id="gpt-4o")
    assert resp.content is None
    assert resp.tool_calls is None
    assert resp.stop_reason is None


def test_invalid_utf8_does_not_raise():
    """Invalid UTF-8 bytes are decoded with replacement, not raised."""
    raw = b"data: \xff\xfe not json\n\ndata: [DONE]\n\n"
    resp = reconstruct_from_sse(raw, latency_ms=1.0, model_id="gpt-4o")
    # bad line skipped, empty result
    assert resp.content is None


# ── Format detection edge cases ──────────────────────────────────────────────

def test_format_detection_uses_first_chunk_type_key():
    """Presence of a 'type' key on the first JSON chunk routes to the Anthropic
    reconstructor; absence routes to OpenAI."""
    # OpenAI chunk has no top-level 'type' → OpenAI path → content concatenation
    openai_chunk = {"choices": [{"index": 0, "delta": {"content": "oai"}, "finish_reason": "stop"}]}
    resp_oai = reconstruct_from_sse(sse(openai_chunk), latency_ms=1.0, model_id="gpt-4o")
    assert resp_oai.content == "oai"

    # Anthropic first chunk has 'type' → Anthropic path
    anthro_chunks = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "anthro"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 1}},
    ]
    resp_anthro = reconstruct_from_sse(sse(*anthro_chunks), latency_ms=1.0,
                                       model_id="claude-3-5-sonnet")
    assert resp_anthro.content == "anthro"


def test_leading_malformed_lines_before_real_chunk():
    """Format detection skips malformed leading data lines and uses the first
    parseable chunk to decide the format."""
    chunk = {"choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}
    raw = (
        "data: garbage{{{\n\n"
        f"data: {json.dumps(chunk)}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")
    resp = reconstruct_from_sse(raw, latency_ms=1.0, model_id="gpt-4o")
    assert resp.content == "ok"


# ── Anthropic cache tokens + thinking ────────────────────────────────────────

def test_anthropic_cache_tokens_from_message_start():
    """cache_read/cache_creation counts in message_start usage are captured and
    priced with the Anthropic additive convention."""
    chunks = [
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 50,
        }}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="claude-sonnet-4")
    assert resp.cache_read_tokens == 900
    assert resp.cache_write_tokens == 50
    assert resp.tokens_in == 10
    assert resp.tokens_out == 5
    expected = (10 * 3.00 + 900 * 0.30 + 50 * 3.75 + 5 * 15.00) / 1_000_000
    assert resp.cost_usd == round(expected, 8)


def test_anthropic_thinking_deltas_accumulated_separately():
    """thinking_delta events build resp.thinking; text stays in resp.content;
    signature_delta events are skipped without error."""
    chunks = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "let me "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "think"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "abc"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "answer"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="claude-sonnet-4")
    assert resp.thinking == "let me think"
    assert resp.content == "answer"


def test_openai_sse_cached_tokens_in_usage():
    """prompt_tokens_details.cached_tokens in the final usage chunk is captured
    and the cached subset is priced at the discounted rate."""
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 100, "completion_tokens": 5,
                   "prompt_tokens_details": {"cached_tokens": 60}}},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="gpt-4o")
    assert resp.cache_read_tokens == 60
    expected = (40 * 2.50 + 60 * 1.25 + 5 * 10.00) / 1_000_000
    assert resp.cost_usd == round(expected, 8)


# ── OpenAI Responses API SSE ─────────────────────────────────────────────────

def test_responses_sse_reconstructed_from_completed_event():
    """response.* typed events route to the Responses reconstructor (not the
    Anthropic one) and the terminal response.completed payload wins."""
    final = {
        "object": "response", "status": "completed", "model": "gpt-4o",
        "output": [{"type": "message",
                    "content": [{"type": "output_text", "text": "Hello!"}]}],
        "usage": {"input_tokens": 20, "output_tokens": 6},
    }
    chunks = [
        {"type": "response.created", "response": {"object": "response", "status": "in_progress"}},
        {"type": "response.output_text.delta", "delta": "Hel"},
        {"type": "response.output_text.delta", "delta": "lo!"},
        {"type": "response.completed", "response": final},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=2.0, model_id="gpt-4o")
    assert resp.content == "Hello!"
    assert resp.tokens_in == 20
    assert resp.tokens_out == 6
    assert resp.stop_reason == "completed"
    assert resp.cost_usd == compute_cost("gpt-4o", 20, 6)


def test_responses_sse_partial_falls_back_to_text_deltas():
    """Without a terminal event (interrupted stream) the accumulated
    output_text deltas are preserved instead of an empty response."""
    chunks = [
        {"type": "response.created", "response": {"object": "response"}},
        {"type": "response.output_text.delta", "delta": "par"},
        {"type": "response.output_text.delta", "delta": "tial"},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=2.0, model_id="gpt-4o")
    assert resp.content == "partial"
    assert resp.tokens_in is None


def test_responses_sse_function_call_in_completed_event():
    """Function calls in the terminal response object map to tool_calls."""
    final = {
        "object": "response", "status": "completed",
        "output": [{"type": "function_call", "call_id": "c1",
                    "name": "search", "arguments": "{\"q\":1}"}],
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }
    chunks = [
        {"type": "response.created", "response": {}},
        {"type": "response.completed", "response": final},
    ]
    resp = reconstruct_from_sse(sse(*chunks), latency_ms=1.0, model_id="gpt-4o")
    assert resp.tool_calls == [{"id": "c1", "name": "search", "arguments": "{\"q\":1}"}]


# ── Mid-stream error detection ───────────────────────────────────────────────

def test_detect_stream_error_anthropic_error_event():
    """An Anthropic error event after message_start is surfaced."""
    chunks = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    ]
    assert detect_stream_error(sse(*chunks)) == "Overloaded"


def test_detect_stream_error_none_on_clean_streams():
    assert detect_stream_error(openai_sse("hi")) is None
    assert detect_stream_error(anthropic_sse("hi")) is None
