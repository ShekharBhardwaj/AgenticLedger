"""AWS Bedrock, Converse wire: what modern boto3 agents speak
(bedrock-runtime's converse / converse_stream), model-agnostic where
InvokeModel is per-model.

The strategy is boundary conversion: Converse's shapes are translated
into the Anthropic shapes the rest of the pipeline already understands
(messages with typed content blocks, tool_use / tool_result, usage with
separate cache counts), so loop stitching, tool pairing, drift diffs,
and pricing reuse the proven machinery instead of growing a parallel
path.

The wire, in brief:

- POST model/<modelId>/converse — JSON in, JSON out. Messages carry
  content BLOCK LISTS ({"text": …}, {"toolUse": …}, {"toolResult": …});
  the system prompt is a top-level list of {"text": …}; sampling knobs
  live under inferenceConfig; tools under toolConfig[].toolSpec.
- POST model/<modelId>/converse-stream — AWS binary event stream, but
  unlike invoke-with-response-stream the frame payloads are DIRECT JSON
  per event type (:event-type header: messageStart, contentBlockStart,
  contentBlockDelta, contentBlockStop, messageStop, metadata), not
  base64-wrapped provider events.
- usage counts cache traffic separately (cacheReadInputTokens /
  cacheWriteInputTokens, inputTokens excluding them) — Anthropic
  semantics, so costs price the same way InvokeModel's do.

Signing is the upstream layer's job, shared with the InvokeModel wire:
any path the bedrock-named adapters claim is re-signed with the
ledger's own AWS credentials.
"""

import json
from contextlib import suppress
from typing import Iterator, Optional

from ..normalize import CanonicalRequest, CanonicalResponse
from ..pricing import compute_cost
from . import eventstream
from .base import build_request, empty_response
from .bedrock import _MARKER


def _to_anthropic_blocks(content: list) -> list:
    """Converse content blocks in Anthropic dress, so downstream machinery
    (chain hashing, tool pairing, drift diffs) sees one dialect."""
    out = []
    for block in content or []:
        if not isinstance(block, dict):
            out.append(block)
        elif "text" in block:
            out.append({"type": "text", "text": block["text"]})
        elif "toolUse" in block:
            tu = block["toolUse"]
            out.append({"type": "tool_use", "id": tu.get("toolUseId"),
                        "name": tu.get("name"), "input": tu.get("input")})
        elif "toolResult" in block:
            tr = block["toolResult"]
            converted = {"type": "tool_result", "tool_use_id": tr.get("toolUseId"),
                         "content": _to_anthropic_blocks(tr.get("content"))}
            if tr.get("status") == "error":
                converted["is_error"] = True
            out.append(converted)
        else:
            # Image/document/video blocks: keep the block's KIND for the
            # record without hauling raw media bytes into the ledger.
            kind = next(iter(block), "unknown")
            out.append({"type": kind})
    return out


class BedrockConverseProvider:
    name = "bedrock"
    wire = "bedrock-converse"
    binary_stream = True
    attribution_story = (
        "Tags ride the inbound URL (/r/<run>/<iter>/model/<id>/converse) and "
        "the x-agenticledger-* headers; both are stripped before forwarding. "
        "The inbound SigV4 signature is stripped too, and the ledger re-signs "
        "the rebuilt request with its own AWS credentials as the very last "
        "step — the same wall the InvokeModel wire proves."
    )

    def matches_path(self, path: str) -> bool:
        return _MARKER in path and path.rstrip("/").endswith(("/converse", "/converse-stream"))

    def captures_path(self, path: str) -> bool:
        return self.matches_path(path)

    def matches_response(self, body: dict) -> bool:
        # Converse is unmistakable by body: camelCase stopReason and an
        # output.message envelope no other wire uses. Claiming it here is
        # what routes non-streaming JSON normalization to this adapter.
        return "stopReason" in body or (
            isinstance(body.get("output"), dict) and "message" in body["output"])

    def matches_stream(self, first_chunk: Optional[dict]) -> bool:
        return False  # binary; reached by path

    def streams(self, path: str, body: dict) -> bool:
        return path.rstrip("/").endswith("/converse-stream")

    def upstream_default(self) -> Optional[str]:
        return None  # per-region host, resolved by the upstream layer

    def _model_id(self, body: dict, path: str) -> str:
        from urllib.parse import unquote
        tail = path.split(_MARKER, 1)[1] if _MARKER in path else ""
        return unquote(tail.split("/", 1)[0]) or "unknown"

    def normalize_request(self, body: dict, path: str) -> CanonicalRequest:
        model = self._model_id(body, path)
        messages = [
            {"role": m.get("role"), "content": _to_anthropic_blocks(m.get("content"))}
            for m in body.get("messages", []) if isinstance(m, dict)
        ]
        system = body.get("system")
        system_prompt = None
        if isinstance(system, list):
            system_prompt = "\n".join(
                b.get("text", "") for b in system if isinstance(b, dict) and "text" in b
            ) or None
            if system_prompt:
                messages = [{"role": "system", "content": system_prompt}] + messages
        tools = [
            {"name": spec.get("name"), "description": spec.get("description"),
             "input_schema": (spec.get("inputSchema") or {}).get("json")}
            for t in ((body.get("toolConfig") or {}).get("tools") or [])
            if isinstance(t, dict) and (spec := t.get("toolSpec")) is not None
        ] or None
        # Sampling knobs live under inferenceConfig; build_request reads a
        # flat body, so hand it one.
        cfg = body.get("inferenceConfig") or {}
        shim = {"temperature": cfg.get("temperature"), "max_tokens": cfg.get("maxTokens")}
        return build_request(messages=messages, model=model, provider=self.name,
                             system_prompt=system_prompt, body=shim, tools=tools)

    def _finish(self, *, text, tool_calls, stop_reason, usage, latency_ms,
                model_id) -> CanonicalResponse:
        tokens_in = usage.get("inputTokens")
        tokens_out = usage.get("outputTokens")
        cache_read = usage.get("cacheReadInputTokens")
        cache_write = usage.get("cacheWriteInputTokens")
        return CanonicalResponse(
            content=text,
            tool_calls=tool_calls or None,
            stop_reason=stop_reason,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                model_id, tokens_in, tokens_out,
                cache_read_tokens=cache_read, cache_write_tokens=cache_write,
                provider="anthropic",  # Converse counts cache traffic apart, as Anthropic does
            ),
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
        )

    def normalize_response(self, body: dict, latency_ms: float, model_id: str) -> CanonicalResponse:
        message = (body.get("output") or {}).get("message") or {}
        blocks = _to_anthropic_blocks(message.get("content"))
        if not blocks and not body.get("usage"):
            return empty_response(latency_ms)
        text = next((b["text"] for b in blocks if b.get("type") == "text"), None)
        tool_calls = [
            {"id": b.get("id"), "name": b.get("name"), "arguments": b.get("input")}
            for b in blocks if b.get("type") == "tool_use"
        ]
        return self._finish(text=text, tool_calls=tool_calls,
                            stop_reason=body.get("stopReason"),
                            usage=body.get("usage") or {},
                            latency_ms=latency_ms, model_id=model_id)

    def _events(self, raw: bytes) -> Iterator[tuple[str, dict]]:
        """(event_type, payload) per frame. Converse-stream payloads are
        direct JSON — no base64 'bytes' wrapper."""
        for headers, payload in eventstream.iter_frames(raw):
            try:
                body = json.loads(payload) if payload else {}
            except json.JSONDecodeError:
                continue
            etype = headers.get(":event-type") or ""
            if headers.get(":message-type") == "exception" or etype in ("exception", "error"):
                yield "error", {"type": headers.get(":exception-type") or "exception",
                                "message": body.get("message") or str(body)[:300]}
                continue
            yield etype, body

    def reconstruct_stream(self, raw: bytes, latency_ms: float, model_id: str) -> CanonicalResponse:
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        current_tool: Optional[dict] = None
        stop_reason: Optional[str] = None
        usage: dict = {}
        for etype, body in self._events(raw):
            if etype == "contentBlockStart":
                tu = (body.get("start") or {}).get("toolUse")
                if tu:
                    current_tool = {"id": tu.get("toolUseId"), "name": tu.get("name"),
                                    "arguments": ""}
            elif etype == "contentBlockDelta":
                delta = body.get("delta") or {}
                if "text" in delta:
                    text_parts.append(delta["text"])
                elif "toolUse" in delta and current_tool is not None:
                    current_tool["arguments"] += delta["toolUse"].get("input") or ""
            elif etype == "contentBlockStop":
                if current_tool is not None:
                    # On parse failure the raw string stays — better than losing the call.
                    with suppress(json.JSONDecodeError):
                        current_tool["arguments"] = json.loads(current_tool["arguments"] or "{}")
                    tool_calls.append(current_tool)
                    current_tool = None
            elif etype == "messageStop":
                stop_reason = body.get("stopReason")
            elif etype == "metadata":
                usage = body.get("usage") or usage
        if not text_parts and not tool_calls and not usage:
            return empty_response(latency_ms)
        return self._finish(text="".join(text_parts) or None, tool_calls=tool_calls,
                            stop_reason=stop_reason, usage=usage,
                            latency_ms=latency_ms, model_id=model_id)

    def stream_error(self, raw: bytes) -> Optional[str]:
        for etype, body in self._events(raw):
            if etype == "error":
                return body.get("message") or str(body)[:300]
        return None
