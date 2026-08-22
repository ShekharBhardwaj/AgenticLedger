"""The provider adapter contract (0.10 design, contract 3).

One adapter per wire format. Each answers three dispatch questions
(does this PATH belong to me? this response BODY? this STREAM?), owns
its normalization in both directions, and must answer, in writing, how
run and session tags survive its wire: the attribution story.
"""

import json
import time
from typing import Optional, Protocol

from ..normalize import CanonicalRequest, CanonicalResponse


class Provider(Protocol):
    name: str                 # the provider label stored on records
    wire: str                 # unique adapter id (one label may have several wires)
    attribution_story: str    # how /r/<run>/<iter> and x-agenticledger-* survive this wire
    binary_stream: bool       # streams are not SSE text (Bedrock's event stream)

    def matches_path(self, path: str) -> bool: ...
    def captures_path(self, path: str) -> bool: ...   # LLM paths beyond the exact set
    def matches_response(self, body: dict) -> bool: ...
    def matches_stream(self, first_chunk: Optional[dict]) -> bool: ...
    def streams(self, path: str, body: dict) -> bool: ...   # is this request a streaming call?
    def normalize_request(self, body: dict, path: str) -> CanonicalRequest: ...
    def normalize_response(self, body: dict, latency_ms: float, model_id: str) -> CanonicalResponse: ...
    def reconstruct_stream(self, raw: bytes, latency_ms: float, model_id: str) -> CanonicalResponse: ...
    def stream_error(self, raw: bytes) -> Optional[str]: ...   # a failure inside a 200 stream
    def upstream_default(self) -> Optional[str]: ...


# ── Shared building blocks ───────────────────────────────────────────────────

def build_request(*, messages, model, provider, system_prompt, body, max_tokens_key="max_tokens",
                  tools=None) -> CanonicalRequest:
    return CanonicalRequest(
        messages=messages,
        tools=tools,
        model_id=model,
        provider=provider,
        timestamp=time.time(),
        system_prompt=system_prompt,
        temperature=body.get("temperature"),
        max_tokens=body.get(max_tokens_key),
        tool_results=extract_tool_results(messages),
    )


def empty_response(latency_ms: float) -> CanonicalResponse:
    return CanonicalResponse(
        content=None, tool_calls=None, stop_reason=None,
        tokens_in=None, tokens_out=None, latency_ms=latency_ms,
    )


def extract_tool_results(messages: list[dict]) -> Optional[list[dict]]:
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


# ── SSE helpers shared by streaming adapters ─────────────────────────────────

def iter_sse_json(text: str):
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


def first_json_chunk(text: str) -> Optional[dict]:
    return next(iter_sse_json(text), None)


def sse_stream_error(raw: bytes) -> Optional[str]:
    """The message of a mid-stream error event in SSE text, if any."""
    text = raw.decode("utf-8", errors="replace")
    for chunk in iter_sse_json(text):
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
