"""Smoke tests that validate the shared test harness itself.

If these fail, the fixtures in conftest are broken and every other test module
built on them is unreliable — so keep this file minimal and self-contained.
"""

import httpx2 as httpx

from .conftest import openai_response, openai_sse, stream_response


def test_non_streaming_capture_and_retrieval(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(content="pong")))

    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "ping"}]},
        headers={"x-agenticledger-session-id": "s-smoke", "x-agenticledger-agent-name": "Tester"},
    )

    # Upstream response is returned unmodified, plus an action-id header is added.
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "pong"
    action_id = resp.headers["x-agenticledger-action-id"]
    assert action_id

    # The forwarded request reached the mock upstream with the body intact.
    assert client.upstream.last_json()["model"] == "gpt-4o"

    # The call was captured and is retrievable by action id and by session.
    explained = client.get(f"/explain/{action_id}").json()
    assert explained["model_id"] == "gpt-4o"
    assert explained["agent_name"] == "Tester"

    session = client.get("/session/s-smoke").json()
    assert len(session) == 1
    assert session[0]["action_id"] == action_id


def test_streaming_capture(proxy):
    client = proxy(handler=lambda r: stream_response(openai_sse("hello")))

    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "gpt-4o", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-agenticledger-session-id": "s-stream"},
    ) as resp:
        body = b"".join(resp.iter_bytes())

    assert b"[DONE]" in body  # stream passed through unmodified
    session = client.get("/session/s-stream").json()
    assert len(session) == 1
    # The streamed deltas were reconstructed into the final content.
    assert session[0]["content"] == "hello"
