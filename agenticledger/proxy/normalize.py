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

from dataclasses import dataclass
from typing import Optional


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
    """The provider label for a request path. Path is authoritative: it
    reflects the wire format in use (a Claude model routed through LiteLLM
    on /v1/chat/completions speaks OpenAI). Dispatch lives in the provider
    registry; this keeps the historical entry point."""
    from . import providers  # lazy: the adapters import this module's dataclasses
    return providers.for_path(path).name


def normalize_request(body: dict, path: str) -> CanonicalRequest:
    from . import providers
    return providers.for_path(path).normalize_request(body, path)


def normalize_response(body: dict, latency_ms: float, model_id: str = "") -> CanonicalResponse:
    from . import providers
    return providers.for_response(body).normalize_response(body, latency_ms, model_id)


def _extract_tool_results(messages: list[dict]) -> Optional[list[dict]]:
    from .providers.base import extract_tool_results
    return extract_tool_results(messages)
