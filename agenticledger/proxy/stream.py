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


def reconstruct_from_sse(body: bytes, latency_ms: float, model_id: str = "",
                         path: Optional[str] = None) -> CanonicalResponse:
    """Reconstruct a CanonicalResponse from a raw response stream. With a
    path, an adapter that owns a binary stream format (Bedrock) decodes its
    own bytes; otherwise the format is sniffed from the first SSE data line
    by the provider registry (Responses-API events carry "response.*"
    types, so they are checked before the generic Anthropic type check;
    OpenAI chunks are the fallback)."""
    from . import providers
    if path is not None:
        adapter = providers.for_path(path)
        if adapter.binary_stream:
            return adapter.reconstruct_stream(body, latency_ms, model_id)
    text = body.decode("utf-8", errors="replace")
    return providers.for_stream(_first_json_chunk(text)).reconstruct_stream(body, latency_ms, model_id)


def detect_stream_error(body: bytes, path: Optional[str] = None) -> Optional[str]:
    """Return the message of a mid-stream error event, if the stream carries one.

    Providers can fail after a 200 status is sent (e.g. Anthropic
    ``overloaded_error`` after ``message_start``); without this check such
    calls would be recorded as clean 200s with silently truncated content.
    """
    from . import providers
    if path is not None:
        adapter = providers.for_path(path)
        if adapter.binary_stream:
            return adapter.stream_error(body)
    text = body.decode("utf-8", errors="replace")
    return providers.for_stream(_first_json_chunk(text)).stream_error(body)


from .providers.base import first_json_chunk as _first_json_chunk  # noqa: E402
