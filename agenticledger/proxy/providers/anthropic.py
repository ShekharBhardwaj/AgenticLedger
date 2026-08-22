"""Anthropic Messages wire: Claude Code, OpenClaw, the Anthropic SDKs."""

from typing import Optional

from ..normalize import CanonicalRequest, CanonicalResponse
from ..pricing import compute_cost
from .base import build_request, empty_response, iter_sse_json


class AnthropicProvider:
    name = "anthropic"
    wire = "anthropic-messages"
    attribution_story = (
        "Tags ride the inbound URL (/r/<run>/<iter>/v1/messages) and the "
        "x-agenticledger-* headers; both are stripped before forwarding. "
        "Claude Code also carries its own session id in metadata.user_id, "
        "which detection reads. No signing, no body changes."
    )

    def matches_path(self, path: str) -> bool:
        # Path is authoritative — it reflects the actual wire format in use.
        # Do NOT use model name: a Claude model routed through LiteLLM on
        # /v1/chat/completions uses OpenAI wire format, not Anthropic format.
        return "messages" in path

    def matches_response(self, body: dict) -> bool:
        count_tokens = ("input_tokens" in body and "content" not in body
                        and "choices" not in body)
        return count_tokens or (bool(body.get("content")) and not body.get("choices"))

    def matches_stream(self, first_chunk: Optional[dict]) -> bool:
        return bool(first_chunk) and first_chunk.get("type") is not None

    def upstream_default(self) -> Optional[str]:
        return "https://api.anthropic.com"

    def normalize_request(self, body: dict, path: str) -> CanonicalRequest:
        model = body.get("model", "unknown")
        messages = list(body.get("messages", []))
        system_prompt: Optional[str] = None
        # Anthropic puts the system prompt as a top-level key — either a plain
        # string or a list of content blocks (Claude Code sends blocks). Either
        # way the text lands in system_prompt so drift diffs and replay have it.
        system = body.get("system")
        if system:
            if isinstance(system, str):
                system_prompt = system
            elif isinstance(system, list):
                system_prompt = "\n".join(
                    block.get("text", "") for block in system
                    if isinstance(block, dict) and block.get("type") == "text"
                ) or None
            messages = [{"role": "system", "content": system}] + messages
        else:
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "system":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        system_prompt = content
                    break
        tools: Optional[list[dict]] = body.get("tools") or body.get("functions") or None
        return build_request(messages=messages, model=model, provider=self.name,
                             system_prompt=system_prompt, body=body, tools=tools)

    def normalize_response(self, body: dict, latency_ms: float, model_id: str) -> CanonicalResponse:
        # Anthropic count_tokens format: {"input_tokens": N}. A free metering
        # call — the count is kept in `content` and marked via stop_reason,
        # with zero cost and no tokens_in/out so aggregates are unaffected.
        if "input_tokens" in body and "content" not in body and "choices" not in body:
            return CanonicalResponse(
                content=f"input_tokens: {body['input_tokens']}",
                tool_calls=None,
                stop_reason="count_tokens",
                tokens_in=None,
                tokens_out=None,
                latency_ms=latency_ms,
                cost_usd=0.0,
            )

        content_blocks = body.get("content")
        if not content_blocks:
            return empty_response(latency_ms)
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

    def reconstruct_stream(self, text: str, latency_ms: float, model_id: str) -> CanonicalResponse:
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

        for chunk in iter_sse_json(text):
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
