"""Unit tests for agentledger/proxy/normalize.py.

Covers provider detection, request normalization (OpenAI chat, Anthropic
messages, OpenAI Responses API) and response normalization for all three
formats, asserting the canonical schema described in the module docstring.

These are pure unit tests — they import the functions directly and need no
fixtures or network.
"""

import time

from agentledger.proxy.normalize import (
    CanonicalRequest,
    CanonicalResponse,
    detect_provider,
    normalize_request,
    normalize_response,
)
from agentledger.proxy.pricing import compute_cost

# ── detect_provider ───────────────────────────────────────────────────────────

class TestDetectProvider:
    def test_chat_completions_path_is_openai(self):
        """A /v1/chat/completions path normalizes to the openai provider."""
        assert detect_provider("/v1/chat/completions", "gpt-4o") == "openai"

    def test_messages_path_is_anthropic(self):
        """A path containing 'messages' normalizes to the anthropic provider."""
        assert detect_provider("/v1/messages", "claude-3-5-sonnet") == "anthropic"

    def test_responses_path_is_openai(self):
        """The Responses API path (no 'messages') is openai."""
        assert detect_provider("/v1/responses", "gpt-4o") == "openai"

    def test_model_name_is_ignored_claude_on_openai_path(self):
        """A Claude model on a chat/completions path is still openai wire format."""
        assert detect_provider("v1/chat/completions", "claude-3-5-sonnet") == "openai"

    def test_model_name_is_ignored_gpt_on_messages_path(self):
        """A gpt model on a 'messages' path is still anthropic wire format."""
        assert detect_provider("/v1/messages", "gpt-4o") == "anthropic"


# ── normalize_request: OpenAI chat ────────────────────────────────────────────

class TestNormalizeRequestOpenAIChat:
    def test_basic_fields_and_provider(self):
        """Model id, provider and a recent timestamp are carried through."""
        before = time.time()
        body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
        req = normalize_request(body, "/v1/chat/completions")
        assert isinstance(req, CanonicalRequest)
        assert req.model_id == "gpt-4o"
        assert req.provider == "openai"
        assert req.timestamp >= before
        assert req.timestamp <= time.time()

    def test_missing_model_defaults_to_unknown(self):
        """A request without a model falls back to the literal 'unknown'."""
        req = normalize_request({"messages": []}, "/v1/chat/completions")
        assert req.model_id == "unknown"

    def test_messages_preserved(self):
        """The message list is preserved verbatim in canonical form."""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        req = normalize_request(
            {"model": "gpt-4o", "messages": msgs}, "/v1/chat/completions"
        )
        assert req.messages == msgs

    def test_system_prompt_extracted_from_system_message(self):
        """A role=system message supplies the system_prompt."""
        body = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ],
        }
        req = normalize_request(body, "/v1/chat/completions")
        assert req.system_prompt == "You are helpful."

    def test_no_system_message_yields_none_system_prompt(self):
        """Without a system message the system_prompt is None."""
        body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
        req = normalize_request(body, "/v1/chat/completions")
        assert req.system_prompt is None

    def test_tools_from_tools_key(self):
        """tools are read from body['tools']."""
        tools = [{"type": "function", "function": {"name": "get_weather"}}]
        body = {"model": "gpt-4o", "messages": [], "tools": tools}
        req = normalize_request(body, "/v1/chat/completions")
        assert req.tools == tools

    def test_tools_from_functions_key(self):
        """tools fall back to the legacy body['functions'] key."""
        functions = [{"name": "get_weather", "parameters": {}}]
        body = {"model": "gpt-4o", "messages": [], "functions": functions}
        req = normalize_request(body, "/v1/chat/completions")
        assert req.tools == functions

    def test_tools_preferred_over_functions(self):
        """When both keys are present, 'tools' wins."""
        tools = [{"type": "function", "function": {"name": "a"}}]
        functions = [{"name": "b"}]
        body = {"model": "gpt-4o", "messages": [], "tools": tools, "functions": functions}
        req = normalize_request(body, "/v1/chat/completions")
        assert req.tools == tools

    def test_no_tools_is_none(self):
        """Absence of tools/functions yields None (not an empty list)."""
        body = {"model": "gpt-4o", "messages": []}
        req = normalize_request(body, "/v1/chat/completions")
        assert req.tools is None

    def test_temperature_and_max_tokens_carried(self):
        """temperature and max_tokens flow into the canonical request."""
        body = {
            "model": "gpt-4o",
            "messages": [],
            "temperature": 0.7,
            "max_tokens": 256,
        }
        req = normalize_request(body, "/v1/chat/completions")
        assert req.temperature == 0.7
        assert req.max_tokens == 256

    def test_missing_temperature_and_max_tokens_are_none(self):
        """Omitted temperature/max_tokens default to None."""
        req = normalize_request({"model": "gpt-4o", "messages": []}, "/v1/chat/completions")
        assert req.temperature is None
        assert req.max_tokens is None

    def test_tool_results_extracted_from_role_tool(self):
        """role=tool messages become canonical tool_results entries."""
        body = {
            "model": "gpt-4o",
            "messages": [
                {"role": "user", "content": "weather?"},
                {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
            ],
        }
        req = normalize_request(body, "/v1/chat/completions")
        assert req.tool_results == [{"tool_call_id": "call_1", "content": "sunny"}]

    def test_no_tool_results_is_none(self):
        """No tool messages yields tool_results == None."""
        body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
        req = normalize_request(body, "/v1/chat/completions")
        assert req.tool_results is None


# ── normalize_request: Anthropic ──────────────────────────────────────────────

class TestNormalizeRequestAnthropic:
    def test_provider_detected(self):
        """A 'messages' path is anthropic."""
        req = normalize_request(
            {"model": "claude-3-5-sonnet", "messages": []}, "/v1/messages"
        )
        assert req.provider == "anthropic"

    def test_top_level_system_becomes_system_prompt(self):
        """Anthropic top-level string system becomes the system_prompt."""
        body = {
            "model": "claude-3-5-sonnet",
            "system": "Be terse.",
            "messages": [{"role": "user", "content": "hi"}],
        }
        req = normalize_request(body, "/v1/messages")
        assert req.system_prompt == "Be terse."

    def test_top_level_system_prepended_as_system_message(self):
        """The system string is also prepended as a role=system message."""
        body = {
            "model": "claude-3-5-sonnet",
            "system": "Be terse.",
            "messages": [{"role": "user", "content": "hi"}],
        }
        req = normalize_request(body, "/v1/messages")
        assert req.messages[0] == {"role": "system", "content": "Be terse."}
        assert req.messages[1] == {"role": "user", "content": "hi"}

    def test_non_string_system_not_used_as_prompt_but_still_prepended(self):
        """A non-string system (e.g. block list) is prepended but system_prompt is None."""
        system_blocks = [{"type": "text", "text": "Be terse."}]
        body = {
            "model": "claude-3-5-sonnet",
            "system": system_blocks,
            "messages": [{"role": "user", "content": "hi"}],
        }
        req = normalize_request(body, "/v1/messages")
        assert req.system_prompt is None
        assert req.messages[0] == {"role": "system", "content": system_blocks}

    def test_anthropic_tool_result_blocks_extracted(self):
        """tool_result blocks inside a user message become tool_results."""
        body = {
            "model": "claude-3-5-sonnet",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "42",
                        }
                    ],
                }
            ],
        }
        req = normalize_request(body, "/v1/messages")
        assert req.tool_results == [{"tool_use_id": "toolu_1", "content": "42"}]

    def test_anthropic_string_content_user_message_no_tool_results(self):
        """A plain-text user message yields no tool_results."""
        body = {
            "model": "claude-3-5-sonnet",
            "messages": [{"role": "user", "content": "hello"}],
        }
        req = normalize_request(body, "/v1/messages")
        assert req.tool_results is None

    def test_tools_carried(self):
        """Anthropic tools are carried from body['tools']."""
        tools = [{"name": "get_weather", "input_schema": {}}]
        body = {"model": "claude-3-5-sonnet", "messages": [], "tools": tools}
        req = normalize_request(body, "/v1/messages")
        assert req.tools == tools


# ── normalize_request: OpenAI Responses API ───────────────────────────────────

class TestNormalizeRequestResponses:
    def test_string_input_becomes_single_user_message(self):
        """A string `input` becomes one user message."""
        body = {"model": "gpt-4o", "input": "Tell me a joke."}
        req = normalize_request(body, "/v1/responses")
        assert req.messages == [{"role": "user", "content": "Tell me a joke."}]
        assert req.provider == "openai"

    def test_list_input_preserved(self):
        """A list `input` of message objects is preserved."""
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        body = {"model": "gpt-4o", "input": msgs}
        req = normalize_request(body, "/v1/responses")
        assert req.messages == msgs

    def test_instructions_prepended_and_set_as_system_prompt(self):
        """instructions are prepended as a system message and set as system_prompt."""
        body = {
            "model": "gpt-4o",
            "instructions": "You are a poet.",
            "input": "Write a line.",
        }
        req = normalize_request(body, "/v1/responses")
        assert req.system_prompt == "You are a poet."
        assert req.messages[0] == {"role": "system", "content": "You are a poet."}
        assert req.messages[1] == {"role": "user", "content": "Write a line."}

    def test_no_instructions_yields_none_system_prompt(self):
        """Without instructions, system_prompt is None and no system message is added."""
        body = {"model": "gpt-4o", "input": "hi"}
        req = normalize_request(body, "/v1/responses")
        assert req.system_prompt is None
        assert req.messages == [{"role": "user", "content": "hi"}]

    def test_max_output_tokens_maps_to_max_tokens(self):
        """Responses API max_output_tokens maps onto canonical max_tokens."""
        body = {"model": "gpt-4o", "input": "hi", "max_output_tokens": 512}
        req = normalize_request(body, "/v1/responses")
        assert req.max_tokens == 512

    def test_temperature_carried(self):
        """temperature is carried for the Responses API too."""
        body = {"model": "gpt-4o", "input": "hi", "temperature": 0.2}
        req = normalize_request(body, "/v1/responses")
        assert req.temperature == 0.2

    def test_tools_carried(self):
        """tools are carried; functions key is not consulted here."""
        tools = [{"type": "function", "name": "f"}]
        body = {"model": "gpt-4o", "input": "hi", "tools": tools}
        req = normalize_request(body, "/v1/responses")
        assert req.tools == tools


# ── normalize_response: OpenAI chat ───────────────────────────────────────────

class TestNormalizeResponseOpenAI:
    def test_content_and_stop_reason(self):
        """content comes from choices[0].message.content; stop_reason from finish_reason."""
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "Hi there"},
                 "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        resp = normalize_response(body, latency_ms=12.5, model_id="gpt-4o")
        assert isinstance(resp, CanonicalResponse)
        assert resp.content == "Hi there"
        assert resp.stop_reason == "stop"
        assert resp.latency_ms == 12.5

    def test_token_counts(self):
        """tokens_in/out come from usage.prompt_tokens/completion_tokens."""
        body = {
            "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        assert resp.tokens_in == 100
        assert resp.tokens_out == 40

    def test_tool_calls_mapped(self):
        """tool_calls map function.name/function.arguments into name/arguments + id."""
        body = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_42",
                                "type": "function",
                                "function": {
                                    "name": "get_weather",
                                    "arguments": '{"city":"SF"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        assert resp.tool_calls == [
            {"id": "call_42", "name": "get_weather", "arguments": '{"city":"SF"}'}
        ]
        assert resp.content is None
        assert resp.stop_reason == "tool_calls"

    def test_no_tool_calls_is_none(self):
        """Without tool_calls the field is None, not an empty list."""
        body = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        assert resp.tool_calls is None

    def test_cost_computed_for_known_model(self):
        """cost_usd is set for a known model and matches the pricing table."""
        body = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        expected = compute_cost("gpt-4o", 1000, 500)
        assert resp.cost_usd == expected
        assert resp.cost_usd is not None and resp.cost_usd > 0

    def test_cost_none_for_unknown_model(self):
        """An unknown model id leaves cost_usd as None."""
        body = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="totally-made-up-model")
        assert resp.cost_usd is None

    def test_missing_usage_yields_none_tokens(self):
        """When usage is absent, token fields are None."""
        body = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        assert resp.tokens_in is None
        assert resp.tokens_out is None


# ── normalize_response: Anthropic ─────────────────────────────────────────────

class TestNormalizeResponseAnthropic:
    def test_text_block_becomes_content(self):
        """A text content block becomes the canonical content."""
        body = {
            "content": [{"type": "text", "text": "Hello from Claude"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 15, "output_tokens": 9},
        }
        resp = normalize_response(body, latency_ms=3.0, model_id="claude-3-5-sonnet")
        assert resp.content == "Hello from Claude"
        assert resp.stop_reason == "end_turn"

    def test_tokens_from_usage(self):
        """tokens_in/out come from usage.input_tokens/output_tokens."""
        body = {
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 21, "output_tokens": 7},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="claude-3-5-sonnet")
        assert resp.tokens_in == 21
        assert resp.tokens_out == 7

    def test_tool_use_blocks_become_tool_calls(self):
        """tool_use blocks map to tool_calls with arguments == the input dict."""
        body = {
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "get_weather",
                    "input": {"city": "SF"},
                },
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="claude-3-5-sonnet")
        assert resp.content == "Let me check."
        assert resp.tool_calls == [
            {"id": "toolu_1", "name": "get_weather", "arguments": {"city": "SF"}}
        ]
        assert resp.stop_reason == "tool_use"

    def test_no_tool_use_is_none(self):
        """A text-only Anthropic response has tool_calls == None."""
        body = {
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="claude-3-5-sonnet")
        assert resp.tool_calls is None

    def test_cost_for_known_anthropic_model(self):
        """cost_usd is computed for a known Claude model."""
        body = {
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1000, "output_tokens": 1000},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="claude-3-5-sonnet")
        assert resp.cost_usd == compute_cost("claude-3-5-sonnet", 1000, 1000)
        assert resp.cost_usd is not None and resp.cost_usd > 0


# ── normalize_response: OpenAI Responses API ──────────────────────────────────

class TestNormalizeResponseResponses:
    def test_output_text_becomes_content(self):
        """An output message with an output_text block supplies content."""
        body = {
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "A poem."}],
                }
            ],
            "usage": {"input_tokens": 12, "output_tokens": 4},
        }
        resp = normalize_response(body, latency_ms=2.0, model_id="gpt-4o")
        assert resp.content == "A poem."
        assert resp.stop_reason == "completed"
        assert resp.tokens_in == 12
        assert resp.tokens_out == 4

    def test_function_call_items_become_tool_calls(self):
        """function_call output items map to canonical tool_calls."""
        body = {
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "get_weather",
                    "arguments": '{"city":"SF"}',
                }
            ],
            "usage": {"input_tokens": 8, "output_tokens": 2},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        assert resp.tool_calls == [
            {"id": "call_1", "name": "get_weather", "arguments": '{"city":"SF"}'}
        ]
        assert resp.content is None

    def test_function_call_falls_back_to_id(self):
        """When call_id is missing, the item id is used for the tool call id."""
        body = {
            "object": "response",
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_99",
                    "name": "f",
                    "arguments": "{}",
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        assert resp.tool_calls == [{"id": "fc_99", "name": "f", "arguments": "{}"}]

    def test_status_maps_to_stop_reason(self):
        """The response status maps to stop_reason."""
        body = {
            "object": "response",
            "status": "incomplete",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "x"}]}
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        assert resp.stop_reason == "incomplete"

    def test_responses_requires_output_key(self):
        """object==response without an 'output' key is NOT treated as Responses API."""
        # No 'output' and no 'choices'/'content' -> empty canonical response.
        body = {"object": "response", "status": "completed"}
        resp = normalize_response(body, latency_ms=4.0, model_id="gpt-4o")
        assert resp.content is None
        assert resp.tool_calls is None
        assert resp.stop_reason is None


# ── normalize_response: unknown / empty bodies ────────────────────────────────

class TestNormalizeResponseEmpty:
    def test_empty_body_all_none_with_latency(self):
        """An empty body yields a fully-None response carrying only the latency."""
        resp = normalize_response({}, latency_ms=7.5, model_id="gpt-4o")
        assert resp.content is None
        assert resp.tool_calls is None
        assert resp.stop_reason is None
        assert resp.tokens_in is None
        assert resp.tokens_out is None
        assert resp.cost_usd is None
        assert resp.latency_ms == 7.5

    def test_unknown_shape_body_all_none(self):
        """A body with no recognized keys yields all-None canonical fields."""
        resp = normalize_response({"foo": "bar"}, latency_ms=1.0, model_id="gpt-4o")
        assert resp.content is None
        assert resp.tool_calls is None
        assert resp.stop_reason is None
        assert resp.tokens_in is None
        assert resp.tokens_out is None

    def test_default_model_id_argument(self):
        """model_id defaults to '' so cost is None when omitted."""
        body = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        resp = normalize_response(body, latency_ms=1.0)
        assert resp.cost_usd is None


# ── Prompt-cache tokens + thinking blocks ────────────────────────────────────

class TestCacheAndThinking:
    def test_anthropic_cache_tokens_extracted_and_priced(self):
        """cache_read/cache_creation usage fields are captured and billed
        additively (Anthropic reports them outside input_tokens)."""
        body = {
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 100, "output_tokens": 10,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 200,
            },
        }
        resp = normalize_response(body, latency_ms=5.0, model_id="claude-sonnet-4")
        assert resp.cache_read_tokens == 1000
        assert resp.cache_write_tokens == 200
        expected = (100 * 3.00 + 1000 * 0.30 + 200 * 3.75 + 10 * 15.00) / 1_000_000
        assert resp.cost_usd == round(expected, 8)

    def test_openai_cached_tokens_extracted_and_discounted(self):
        """prompt_tokens_details.cached_tokens is the cached subset of
        prompt_tokens — extracted and priced at the discounted rate."""
        body = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1000, "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 400},
            },
        }
        resp = normalize_response(body, latency_ms=5.0, model_id="gpt-4o")
        assert resp.cache_read_tokens == 400
        expected = (600 * 2.50 + 400 * 1.25 + 10 * 10.00) / 1_000_000
        assert resp.cost_usd == round(expected, 8)

    def test_openai_null_prompt_tokens_details_tolerated(self):
        """An explicit null prompt_tokens_details must not crash extraction."""
        body = {
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                      "prompt_tokens_details": None},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        assert resp.cache_read_tokens is None

    def test_responses_api_cached_tokens_extracted(self):
        """Responses API reports the cached subset under input_tokens_details."""
        body = {
            "object": "response", "status": "completed",
            "output": [{"type": "message",
                        "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 500, "output_tokens": 5,
                      "input_tokens_details": {"cached_tokens": 200}},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="gpt-4o")
        assert resp.cache_read_tokens == 200
        expected = (300 * 2.50 + 200 * 1.25 + 5 * 10.00) / 1_000_000
        assert resp.cost_usd == round(expected, 8)

    def test_anthropic_thinking_block_captured_alongside_text(self):
        """A thinking block lands in resp.thinking; text stays in resp.content."""
        body = {
            "content": [
                {"type": "thinking", "thinking": "step by step...", "signature": "sig"},
                {"type": "text", "text": "final answer"},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        resp = normalize_response(body, latency_ms=1.0, model_id="claude-sonnet-4")
        assert resp.thinking == "step by step..."
        assert resp.content == "final answer"


# ── count_tokens ──────────────────────────────────────────────────────────────

class TestNormalizeResponseCountTokens:
    def test_count_tokens_shape_is_marked_and_free(self):
        """{"input_tokens": N} (Anthropic count_tokens) is marked via stop_reason,
        keeps the count in content, and carries no tokens/cost to sum."""
        resp = normalize_response({"input_tokens": 2095}, latency_ms=12.0)
        assert resp.stop_reason == "count_tokens"
        assert resp.content == "input_tokens: 2095"
        assert resp.tokens_in is None and resp.tokens_out is None
        assert resp.cost_usd == 0.0
        assert resp.tool_calls is None

    def test_real_anthropic_response_not_mistaken_for_count_tokens(self):
        """A normal messages response (input_tokens nested under usage) still
        routes to the Anthropic branch."""
        body = {
            "content": [{"type": "text", "text": "hi"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        resp = normalize_response(body, latency_ms=1.0)
        assert resp.stop_reason == "end_turn"
        assert resp.tokens_in == 5
