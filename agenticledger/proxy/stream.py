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

from typing import Optional

from .normalize import CanonicalResponse


def reconstruct_from_sse(body: bytes, latency_ms: float, model_id: str = "") -> CanonicalResponse:
    """Reconstruct a CanonicalResponse from raw SSE bytes. The format is
    detected from the first meaningful data line by the provider registry
    (Responses-API events carry "response.*" types, so they are checked
    before the generic Anthropic type check; OpenAI chunks are the fallback)."""
    from . import providers
    text = body.decode("utf-8", errors="replace")
    return providers.for_stream(_first_json_chunk(text)).reconstruct_stream(text, latency_ms, model_id)


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


# ── Helpers ──────────────────────────────────────────────────────────────────

from .providers.base import first_json_chunk as _first_json_chunk  # noqa: E402
from .providers.base import iter_sse_json as _iter_sse_json  # noqa: E402
