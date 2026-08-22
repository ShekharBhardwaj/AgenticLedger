"""OpenAI chat-completions wire: also what LiteLLM, OpenRouter, LM Studio,
and most gateways speak. The registry's fallback adapter."""

from typing import Optional

from ..normalize import CanonicalRequest, CanonicalResponse
from ..pricing import compute_cost, has_price
from .base import build_request, empty_response, iter_sse_json


class OpenAIProvider:
    name = "openai"
    wire = "openai-chat"
    attribution_story = (
        "Tags ride the inbound URL (/r/<run>/<iter>/v1/...) and the "
        "x-agenticledger-* headers; both are stripped before forwarding. "
        "No signing, no body changes: the outbound request is the inbound one."
    )

    def matches_path(self, path: str) -> bool:
        return True  # fallback: anything the other adapters did not claim

    def captures_path(self, path: str) -> bool:
        return False  # the exact LLM-path set decides for this wire

    def _model_id(self, body: dict, path: str) -> str:
        return body.get("model", "unknown")

    @staticmethod
    def _effective_model(model_id: str, body: dict) -> str:
        # Azure requests name a DEPLOYMENT, and the real model id arrives in
        # the response; prefer it whenever the request's id is not priceable.
        # For plain OpenAI the request id prices, so nothing changes.
        if model_id and has_price(model_id):
            return model_id
        return body.get("model") or model_id

    def matches_response(self, body: dict) -> bool:
        return True  # fallback: choices, or nothing recognizable

    def matches_stream(self, first_chunk: Optional[dict]) -> bool:
        return True  # fallback

    def upstream_default(self) -> Optional[str]:
        return "https://api.openai.com"

    def normalize_request(self, body: dict, path: str) -> CanonicalRequest:
        model = self._model_id(body, path)
        messages = list(body.get("messages", []))
        system_prompt: Optional[str] = None
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
        choices = body.get("choices")
        if not choices:
            return empty_response(latency_ms)
        choice = choices[0]
        msg = choice.get("message", {})
        content = msg.get("content")
        model_id = self._effective_model(model_id, body)

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

    def reconstruct_stream(self, text: str, latency_ms: float, model_id: str) -> CanonicalResponse:
        text_parts: list[str] = []
        tool_calls: dict[int, dict] = {}  # index → {id, name, arguments}
        stop_reason: Optional[str] = None
        tokens_in: Optional[int] = None
        tokens_out: Optional[int] = None
        cache_read: Optional[int] = None
        cache_write: Optional[int] = None

        for chunk in iter_sse_json(text):
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
