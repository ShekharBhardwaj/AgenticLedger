"""
Normalize provider-native request/response formats to Agentic Ledger's
canonical internal schema.

Canonical request:  { messages, tools, model_id, provider, timestamp,
                      system_prompt, temperature, max_tokens, tool_results }
Canonical response: { content, tool_calls, stop_reason, tokens_in,
                      tokens_out, latency_ms, cost_usd,
                      cache_read_tokens, cache_write_tokens, thinking }

Token semantics follow the provider's wire format: Anthropic's tokens_in
EXCLUDES cache reads/writes (they are reported separately), OpenAI's
tokens_in INCLUDES cached tokens (cache_read_tokens is the cached subset).
compute_cost() understands both conventions via its provider argument.

Never store provider-native formats as source of truth.
"""

import time
from dataclasses import dataclass
from typing import Optional

from .pricing import compute_cost


@dataclass
class CanonicalRequest:
    messages: list[dict]
    model_id: str
    provider: str
    timestamp: float
    tools: Optional[list[dict]] = None
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tool_results: Optional[list[dict]] = None  # results fed into this call


@dataclass
class CanonicalResponse:
    content: Optional[str]
    tool_calls: Optional[list[dict]]
    stop_reason: Optional[str]
    tokens_in: Optional[int]
    tokens_out: Optional[int]
    latency_ms: float
    cost_usd: Optional[float] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    thinking: Optional[str] = None


def detect_provider(path: str, model: str) -> str:  # noqa: ARG001
    # Path is authoritative — it reflects the actual wire format in use.
    # Do NOT use model name: a Claude model routed through LiteLLM on
    # /v1/chat/completions uses OpenAI wire format, not Anthropic format.
    if "messages" in path:
        return "anthropic"
    return "openai"


def normalize_request(body: dict, path: str) -> CanonicalRequest:
    model = body.get("model", "unknown")
    provider = detect_provider(path, model)

    # OpenAI Responses API uses `input` instead of `messages`
    if "responses" in path:
        return _normalize_responses_request(body, model, provider)

    messages = list(body.get("messages", []))
    system_prompt: Optional[str] = None

    # Anthropic puts the system prompt as a top-level key
    system = body.get("system")
    if system and provider == "anthropic":
        system_prompt = system if isinstance(system, str) else None
        messages = [{"role": "system", "content": system}] + messages
    else:
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                content = msg.get("content", "")
                if isinstance(content, str):
                    system_prompt = content
                break

    tools: Optional[list[dict]] = body.get("tools") or body.get("functions") or None

    return CanonicalRequest(
        messages=messages,
        tools=tools,
        model_id=model,
        provider=provider,
        timestamp=time.time(),
        system_prompt=system_prompt,
        temperature=body.get("temperature"),
        max_tokens=body.get("max_tokens"),
        tool_results=_extract_tool_results(messages),
    )


def _normalize_responses_request(body: dict, model: str, provider: str) -> CanonicalRequest:
    """Normalize OpenAI Responses API request format."""
    instructions = body.get("instructions")
    raw_input = body.get("input", [])

    # input can be a string or a list of message objects
    if isinstance(raw_input, str):
        messages = [{"role": "user", "content": raw_input}]
    else:
        messages = list(raw_input)

    if instructions:
        messages = [{"role": "system", "content": instructions}] + messages

    tools: Optional[list[dict]] = body.get("tools") or None

    return CanonicalRequest(
        messages=messages,
        tools=tools,
        model_id=model,
        provider=provider,
        timestamp=time.time(),
        system_prompt=instructions,
        temperature=body.get("temperature"),
        max_tokens=body.get("max_output_tokens"),
        tool_results=_extract_tool_results(messages),
    )


def normalize_response(body: dict, latency_ms: float, model_id: str = "") -> CanonicalResponse:
    # OpenAI Responses API format
    if body.get("object") == "response" and "output" in body:
        return _normalize_responses_response(body, latency_ms, model_id)

    # OpenAI / LiteLLM format
    choices = body.get("choices")
    if choices:
        choice = choices[0]
        msg = choice.get("message", {})
        content = msg.get("content")

        raw_tcs = msg.get("tool_calls") or []
        tool_calls: Optional[list[dict]] = [
            {
                "id": tc.get("id"),
                "name": tc["function"]["name"],
                "arguments": tc["function"]["arguments"],
            }
            for tc in raw_tcs
        ] or None

        usage = body.get("usage", {})
        tokens_in = usage.get("prompt_tokens")
        tokens_out = usage.get("completion_tokens")
        cache_read = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        # LiteLLM forwards Anthropic cache writes under the native key.
        cache_write = usage.get("cache_creation_input_tokens")
        return CanonicalResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=choice.get("finish_reason"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                model_id, tokens_in, tokens_out,
                cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                provider="openai",
            ),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

    # Anthropic format
    content_blocks = body.get("content")
    if content_blocks:
        text = next(
            (b["text"] for b in content_blocks if b.get("type") == "text"), None
        )
        thinking = next(
            (b.get("thinking") for b in content_blocks if b.get("type") == "thinking"),
            None,
        )
        tool_calls = [
            {"id": b.get("id"), "name": b.get("name"), "arguments": b.get("input")}
            for b in content_blocks
            if b.get("type") == "tool_use"
        ] or None

        usage = body.get("usage", {})
        tokens_in = usage.get("input_tokens")
        tokens_out = usage.get("output_tokens")
        cache_read = usage.get("cache_read_input_tokens")
        cache_write = usage.get("cache_creation_input_tokens")
        return CanonicalResponse(
            content=text,
            tool_calls=tool_calls,
            stop_reason=body.get("stop_reason"),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                model_id, tokens_in, tokens_out,
                cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                provider="anthropic",
            ),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            thinking=thinking,
        )

    return CanonicalResponse(
        content=None,
        tool_calls=None,
        stop_reason=None,
        tokens_in=None,
        tokens_out=None,
        latency_ms=latency_ms,
    )


def _normalize_responses_response(body: dict, latency_ms: float, model_id: str) -> CanonicalResponse:
    """Normalize OpenAI Responses API response format."""
    output = body.get("output", [])
    text: Optional[str] = None
    tool_calls: list[dict] = []

    for item in output:
        item_type = item.get("type")
        if item_type == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text" and text is None:
                    text = block.get("text")
        elif item_type == "function_call":
            tool_calls.append({
                "id": item.get("call_id") or item.get("id"),
                "name": item.get("name"),
                "arguments": item.get("arguments"),
            })

    usage = body.get("usage", {})
    tokens_in = usage.get("input_tokens")
    tokens_out = usage.get("output_tokens")
    cache_read = (usage.get("input_tokens_details") or {}).get("cached_tokens")
    return CanonicalResponse(
        content=text,
        tool_calls=tool_calls or None,
        stop_reason=body.get("status"),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        cost_usd=compute_cost(
            model_id, tokens_in, tokens_out,
            cache_read_tokens=cache_read, provider="openai",
        ),
        cache_read_tokens=cache_read,
    )


def _extract_tool_results(messages: list[dict]) -> Optional[list[dict]]:
    """Extract tool execution results from the message history sent to the model."""
    results = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        # OpenAI: role=tool
        if msg.get("role") == "tool":
            results.append({
                "tool_call_id": msg.get("tool_call_id"),
                "content": msg.get("content"),
            })
        # Anthropic: tool_result blocks inside a user message
        elif msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        result = {
                            "tool_use_id": block.get("tool_use_id"),
                            "content": block.get("content"),
                        }
                        # Anthropic marks failed executions explicitly.
                        if block.get("is_error") is not None:
                            result["is_error"] = block.get("is_error")
                        results.append(result)
    return results or None
