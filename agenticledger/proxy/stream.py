"""
SSE chunk accumulation for streaming LLM responses.

Reconstructs a CanonicalResponse from accumulated SSE chunks in any of the
three wire formats the proxy captures:

- OpenAI chat completions (``choices[].delta`` chunks) — also what LiteLLM
  emits regardless of the underlying model.
- Anthropic native (``message_start`` / ``content_block_delta`` events, each
  carrying a ``type`` field).
- OpenAI Responses API (``response.*`` typed events, emitted by the OpenAI
  Agents SDK among others).
"""

import json
from typing import Optional

from .normalize import CanonicalResponse, _normalize_responses_response
from .pricing import compute_cost


def reconstruct_from_sse(body: bytes, latency_ms: float, model_id: str = "") -> CanonicalResponse:
    """Reconstruct a CanonicalResponse from raw SSE bytes."""
    text = body.decode("utf-8", errors="replace")

    # Detect format from first meaningful data line. Responses-API events also
    # carry a "type" field ("response.created", ...), so check them before the
    # generic Anthropic type check.
    first_chunk = _first_json_chunk(text)
    first_type = (first_chunk or {}).get("type")
    if isinstance(first_type, str) and first_type.startswith("response."):
        return _reconstruct_responses(text, latency_ms, model_id)
    if first_chunk and first_type is not None:
        return _reconstruct_anthropic(text, latency_ms, model_id)
    return _reconstruct_openai(text, latency_ms, model_id)


def detect_stream_error(body: bytes) -> Optional[str]:
    """Return the message of a mid-stream error event, if the stream carries one.

    Providers can fail after a 200 status is sent (e.g. Anthropic
    ``overloaded_error`` after ``message_start``); without this check such
    calls would be recorded as clean 200s with silently truncated content.
    """
    text = body.decode("utf-8", errors="replace")
    for chunk in _iter_sse_json(text):
        err = None
        if chunk.get("type") in ("error", "response.failed"):
            err = chunk.get("error") or (chunk.get("response") or {}).get("error")
        elif isinstance(chunk.get("error"), dict):
            err = chunk["error"]
        if isinstance(err, dict):
            return err.get("message") or str(err)[:300]
        if err:
            return str(err)[:300]
    return None


# ── OpenAI SSE format ────────────────────────────────────────────────────────

def _reconstruct_openai(text: str, latency_ms: float, model_id: str = "") -> CanonicalResponse:
    text_parts: list[str] = []
    tool_calls: dict[int, dict] = {}  # index → {id, name, arguments}
    stop_reason: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cache_read: Optional[int] = None
    cache_write: Optional[int] = None

    for chunk in _iter_sse_json(text):
        choices = chunk.get("choices", [])
        if choices:
            choice = choices[0]
            delta = choice.get("delta", {})

            if delta.get("content"):
                text_parts.append(delta["content"])

            if choice.get("finish_reason"):
                stop_reason = choice["finish_reason"]

            for tc_delta in delta.get("tool_calls", []):
                idx = tc_delta.get("index", 0)
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": None, "name": None, "arguments": ""}
                if tc_delta.get("id"):
                    tool_calls[idx]["id"] = tc_delta["id"]
                fn = tc_delta.get("function", {})
                if fn.get("name"):
                    tool_calls[idx]["name"] = fn["name"]
                if fn.get("arguments"):
                    tool_calls[idx]["arguments"] += fn["arguments"]

        usage = chunk.get("usage", {})
        if usage:
            tokens_in = usage.get("prompt_tokens")
            tokens_out = usage.get("completion_tokens")
            cache_read = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            cache_write = usage.get("cache_creation_input_tokens")

    return CanonicalResponse(
        content="".join(text_parts) or None,
        tool_calls=[
            {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
            for tc in tool_calls.values()
        ] or None,
        stop_reason=stop_reason,
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


# ── Anthropic native SSE format ──────────────────────────────────────────────

def _reconstruct_anthropic(text: str, latency_ms: float, model_id: str = "") -> CanonicalResponse:
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[dict] = []
    current_tool: Optional[dict] = None
    stop_reason: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    cache_read: Optional[int] = None
    cache_write: Optional[int] = None

    def _read_usage(usage: dict) -> None:
        nonlocal tokens_in, tokens_out, cache_read, cache_write
        if usage.get("input_tokens") is not None:
            tokens_in = usage["input_tokens"]
        if usage.get("output_tokens") is not None:
            tokens_out = usage["output_tokens"]
        if usage.get("cache_read_input_tokens") is not None:
            cache_read = usage["cache_read_input_tokens"]
        if usage.get("cache_creation_input_tokens") is not None:
            cache_write = usage["cache_creation_input_tokens"]

    for chunk in _iter_sse_json(text):
        event_type = chunk.get("type")

        if event_type == "content_block_start":
            block = chunk.get("content_block", {})
            if block.get("type") == "tool_use":
                current_tool = {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "arguments": "",
                }

        elif event_type == "content_block_delta":
            delta = chunk.get("delta", {})
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                text_parts.append(delta.get("text", ""))
            elif delta_type == "thinking_delta":
                thinking_parts.append(delta.get("thinking", ""))
            elif delta_type == "input_json_delta" and current_tool is not None:
                current_tool["arguments"] += delta.get("partial_json", "")
            # signature_delta carries no user-visible content — skip.

        elif event_type == "content_block_stop":
            if current_tool is not None:
                tool_calls.append(current_tool)
                current_tool = None

        elif event_type == "message_delta":
            delta = chunk.get("delta", {})
            if delta.get("stop_reason"):
                stop_reason = delta["stop_reason"]
            _read_usage(chunk.get("usage", {}))

        elif event_type == "message_start":
            _read_usage(chunk.get("message", {}).get("usage", {}))

    return CanonicalResponse(
        content="".join(text_parts) or None,
        tool_calls=tool_calls or None,
        stop_reason=stop_reason,
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
        thinking="".join(thinking_parts) or None,
    )


# ── OpenAI Responses API SSE format ──────────────────────────────────────────

def _reconstruct_responses(text: str, latency_ms: float, model_id: str = "") -> CanonicalResponse:
    """Reconstruct from Responses-API events (``response.*`` types).

    The terminal event (``response.completed`` / ``response.incomplete`` /
    ``response.failed``) carries the full response object, so reconstruction
    delegates to the non-streaming normalizer. Interrupted streams fall back
    to the accumulated ``response.output_text.delta`` events.
    """
    final: Optional[dict] = None
    text_parts: list[str] = []

    for chunk in _iter_sse_json(text):
        event_type = chunk.get("type", "")
        if event_type in ("response.completed", "response.incomplete", "response.failed"):
            resp = chunk.get("response")
            if isinstance(resp, dict):
                final = resp
        elif event_type == "response.output_text.delta":
            delta = chunk.get("delta")
            if isinstance(delta, str):
                text_parts.append(delta)

    if final is not None:
        return _normalize_responses_response(final, latency_ms, model_id or final.get("model", ""))

    return CanonicalResponse(
        content="".join(text_parts) or None,
        tool_calls=None,
        stop_reason=None,
        tokens_in=None,
        tokens_out=None,
        latency_ms=latency_ms,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _iter_sse_json(text: str):
    """Yield parsed JSON objects from SSE data lines."""
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def _first_json_chunk(text: str) -> Optional[dict]:
    return next(_iter_sse_json(text), None)
