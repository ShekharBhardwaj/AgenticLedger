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
from .base import Provider
from .openai import OpenAIProvider
from .responses import ResponsesProvider

PROVIDERS: tuple[Provider, ...] = (
    ResponsesProvider(),
    AnthropicProvider(),
    OpenAIProvider(),   # fallback: must stay last
)


def for_path(path: str) -> Provider:
    return next(p for p in PROVIDERS if p.matches_path(path))


def for_response(body: dict) -> Provider:
    return next(p for p in PROVIDERS if p.matches_response(body))


def for_stream(first_chunk: Optional[dict]) -> Provider:
    return next(p for p in PROVIDERS if p.matches_stream(first_chunk))


def by_name(name: str) -> Optional[Provider]:
    """The adapter whose record label matches (first wire wins)."""
    return next((p for p in PROVIDERS if p.name == name), None)
