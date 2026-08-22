"""OpenAI Responses API wire (`/v1/responses`): `input` instead of
`messages`, `response.*` stream events. Records carry the openai label,
because that is the provider being billed."""

from typing import Optional

from ..normalize import CanonicalRequest, CanonicalResponse
from ..pricing import compute_cost
from .base import build_request, iter_sse_json


class ResponsesProvider:
    name = "openai"
    wire = "openai-responses"
    attribution_story = (
        "Same as the chat wire: tags ride the inbound URL and headers and are "
        "stripped before forwarding; nothing in the body is touched."
    )

    def matches_path(self, path: str) -> bool:
        return "responses" in path

    def matches_response(self, body: dict) -> bool:
        return body.get("object") == "response" and "output" in body

    def matches_stream(self, first_chunk: Optional[dict]) -> bool:
        first_type = (first_chunk or {}).get("type")
        return isinstance(first_type, str) and first_type.startswith("response.")

    def upstream_default(self) -> Optional[str]:
        return "https://api.openai.com"

    def normalize_request(self, body: dict, path: str) -> CanonicalRequest:
        """Normalize OpenAI Responses API request format."""
        model = body.get("model", "unknown")
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
        return build_request(messages=messages, model=model, provider=self.name,
                             system_prompt=instructions, body=body,
                             max_tokens_key="max_output_tokens", tools=tools)

    def normalize_response(self, body: dict, latency_ms: float, model_id: str) -> CanonicalResponse:
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

    def reconstruct_stream(self, text: str, latency_ms: float, model_id: str) -> CanonicalResponse:
        """Reconstruct from Responses-API events (``response.*`` types).

        The terminal event (``response.completed`` / ``response.incomplete`` /
        ``response.failed``) carries the full response object, so reconstruction
        delegates to the non-streaming normalizer. Interrupted streams fall back
        to the accumulated ``response.output_text.delta`` events.
        """
        final: Optional[dict] = None
        text_parts: list[str] = []

        for chunk in iter_sse_json(text):
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
            return self.normalize_response(final, latency_ms, model_id or final.get("model", ""))

        return CanonicalResponse(
            content="".join(text_parts) or None,
            tool_calls=None,
            stop_reason=None,
            tokens_in=None,
            tokens_out=None,
            latency_ms=latency_ms,
        )
