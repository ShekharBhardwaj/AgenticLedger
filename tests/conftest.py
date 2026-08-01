"""
Shared pytest fixtures and helpers for the Agentic Ledger test suite.

The proxy forwards every request to ``app.state.client`` (an ``httpx.AsyncClient``
pointed at the upstream LLM). Tests swap that client for one backed by an
``httpx.MockTransport`` so no network call is ever made — the mock upstream
records every forwarded request and returns whatever the test configures.

Two layers of fixtures are provided:

* ``proxy``  — factory that builds a fully wired proxy (TestClient + mock upstream)
               with configurable ``create_app`` kwargs (budgets, rate limits, …).
* ``store``  — a bare in-memory ``Store`` for unit-testing the storage layer.

Plus a set of builder helpers (``openai_response``, ``anthropic_response``,
``openai_sse``, ``anthropic_sse``, …) so individual test modules don't each
reinvent provider wire formats.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

import httpx
import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from agenticledger.proxy.app import create_app
from agenticledger.proxy.store import Store

UPSTREAM_URL = "http://upstream.test"


# ── Mock upstream LLM provider ────────────────────────────────────────────────

class MockUpstream:
    """A configurable stand-in for the upstream LLM API.

    Every request the proxy forwards is recorded in ``self.requests``. The
    response is decided by ``handler`` — either a fixed ``httpx.Response`` or a
    callable ``(httpx.Request) -> httpx.Response``. Reconfigure mid-test with
    ``upstream.set(...)``.
    """

    def __init__(self, handler: Optional[Any] = None) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def set(self, handler: Any) -> None:
        self._handler = handler

    @property
    def last_request(self) -> Optional[httpx.Request]:
        return self.requests[-1] if self.requests else None

    def last_json(self) -> Any:
        """Parsed JSON body of the most recent forwarded request."""
        req = self.last_request
        return json.loads(req.content) if req and req.content else None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        handler = self._handler
        if handler is None:
            return httpx.Response(200, json={})
        if callable(handler):
            return handler(request)
        return handler  # a fixed httpx.Response


# ── Proxy factory fixture ─────────────────────────────────────────────────────

class ProxyClient(TestClient):
    """A TestClient with a handle to its mock upstream."""

    upstream: MockUpstream


@pytest.fixture
def proxy() -> Callable[..., ProxyClient]:
    """Factory: build a wired proxy TestClient backed by a mock upstream.

    Usage::

        def test_capture(proxy):
            client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
            resp = client.post("/v1/chat/completions", json={...})
            assert client.upstream.last_json()["model"] == "gpt-4o"

    Any ``create_app`` keyword (``budget_session``, ``rate_limit_config``, …)
    can be passed straight through::

        client = proxy(budget_session=0.001)
    """
    opened: list[TestClient] = []

    def _make(handler: Optional[Any] = None, **app_kwargs: Any) -> ProxyClient:
        upstream = MockUpstream(handler)
        # Tests may declare a specific upstream (e.g. to exercise wire-format
        # mismatch detection); traffic still goes to the mock transport.
        app_kwargs.setdefault("upstream_url", UPSTREAM_URL)
        app = create_app(dsn="sqlite:///:memory:", **app_kwargs)
        tc: ProxyClient = TestClient(app)  # type: ignore[assignment]
        tc.__enter__()  # runs lifespan → sets app.state.store and app.state.client
        # Replace the real upstream client with one backed by the mock transport.
        app.state.client = httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            base_url=UPSTREAM_URL,
            timeout=httpx.Timeout(120.0),
        )
        tc.upstream = upstream
        opened.append(tc)
        return tc

    yield _make

    for tc in opened:
        tc.__exit__(None, None, None)


# ── Bare store fixture (storage-layer unit tests) ─────────────────────────────

@pytest_asyncio.fixture
async def store() -> Store:
    """A fresh in-memory SQLite Store, closed on teardown."""
    s = await Store.connect("sqlite:///:memory:")
    try:
        yield s
    finally:
        await s.close()


# ── Wire-format builders ──────────────────────────────────────────────────────

def openai_response(
    content: str = "Hello from the model.",
    *,
    model: str = "gpt-4o",
    tool_calls: Optional[list[dict]] = None,
    prompt_tokens: int = 12,
    completion_tokens: int = 8,
    finish_reason: str = "stop",
) -> dict:
    """A minimal OpenAI ``chat.completion`` response body."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
        message["content"] = None
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def openai_tool_call(name: str = "get_weather", arguments: str = '{"city":"SF"}', call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def anthropic_response(
    text: str = "Hello from Claude.",
    *,
    model: str = "claude-3-5-sonnet",
    tool_uses: Optional[list[dict]] = None,
    input_tokens: int = 15,
    output_tokens: int = 9,
    stop_reason: str = "end_turn",
) -> dict:
    """A minimal Anthropic ``messages`` response body."""
    content: list[dict[str, Any]] = []
    if text is not None:
        content.append({"type": "text", "text": text})
    for tu in tool_uses or []:
        content.append({"type": "tool_use", **tu})
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def sse(*chunks: Any) -> bytes:
    """Encode dict chunks as an SSE byte stream terminated by ``[DONE]``."""
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


def openai_sse(text: str = "Hi", *, prompt_tokens: int = 5, completion_tokens: int = 2) -> bytes:
    """An OpenAI-format streamed completion split into per-character deltas."""
    chunks: list[dict] = []
    for ch in text:
        chunks.append({"choices": [{"index": 0, "delta": {"content": ch}, "finish_reason": None}]})
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    chunks.append({
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    })
    return sse(*chunks)


def anthropic_sse(text: str = "Hi", *, input_tokens: int = 5, output_tokens: int = 2) -> bytes:
    """An Anthropic-native SSE stream (message_start … content_block_delta …)."""
    chunks: list[dict] = [
        {"type": "message_start", "message": {"usage": {"input_tokens": input_tokens}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
    ]
    for ch in text:
        chunks.append({"type": "content_block_delta", "index": 0,
                       "delta": {"type": "text_delta", "text": ch}})
    chunks.append({"type": "content_block_stop", "index": 0})
    chunks.append({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                   "usage": {"output_tokens": output_tokens}})
    return sse(*chunks)


def stream_response(body: bytes) -> httpx.Response:
    """Wrap raw SSE bytes in a streaming-content-type httpx.Response."""
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})
