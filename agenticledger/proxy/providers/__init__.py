"""The provider registry: one ordered walk answers every dispatch
question. Order matters and mirrors the pre-adapter dispatch exactly:
the Responses API claims first (its paths and events are the most
specific), Anthropic second, and the OpenAI chat wire is the fallback
for everything else, which is also what gateways speak.

To add a provider: one module implementing base.Provider, one entry
here, an attribution story, and fixtures in tests/fixtures/wire that
the parity harness replays.
"""

from typing import Optional

from .anthropic import AnthropicProvider
from .azure import AzureProvider
from .base import Provider
from .bedrock import BedrockProvider
from .openai import OpenAIProvider
from .responses import ResponsesProvider

PROVIDERS: tuple[Provider, ...] = (
    ResponsesProvider(),
    BedrockProvider(),  # model/<id>/invoke paths; Anthropic-shaped bodies, binary streams
    AnthropicProvider(),
    AzureProvider(),    # OpenAI's wire under a deployment path; claims by path only
    OpenAIProvider(),   # fallback: must stay last
)


def for_path(path: str) -> Provider:
    return next(p for p in PROVIDERS if p.matches_path(path))


def for_response(body: dict) -> Provider:
    return next(p for p in PROVIDERS if p.matches_response(body))


def for_stream(first_chunk: Optional[dict]) -> Provider:
    return next(p for p in PROVIDERS if p.matches_stream(first_chunk))


def streams(path: str, body: dict) -> bool:
    """Whether this request is a streaming call, decided by its adapter."""
    return for_path(path).streams(path, body)


def captures(path: str) -> bool:
    """An adapter claims this path as an LLM call beyond the exact set."""
    return any(p.captures_path(path) for p in PROVIDERS)


def by_name(name: str) -> Optional[Provider]:
    """The adapter whose record label matches (first wire wins)."""
    return next((p for p in PROVIDERS if p.name == name), None)
