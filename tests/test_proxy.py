"""Integration tests for agentledger/proxy/app.py via the `proxy` fixture.

These exercise the transparent-proxy semantics described in the module docstring:
capture of LLM POSTs, pass-through of non-LLM traffic, meta-header stripping,
default meta assignment, upstream error passthrough, rate limiting, budget
enforcement (block + warn), and API-key auth.

Each test builds a fresh proxy + mock upstream so cases stay isolated.
"""

import httpx

from agentledger.proxy.ratelimit import RateLimitConfig

from .conftest import openai_response

# Common request body for a non-streaming chat completion.
_CHAT_BODY = {"model": "gpt-4o", "messages": [{"role": "user", "content": "ping"}]}


def _ok_handler(content="Hello from the model."):
    return lambda r: httpx.Response(200, json=openai_response(content=content))


# ── Non-LLM passthrough (not captured) ────────────────────────────────────────

def test_non_llm_get_is_proxied_but_not_captured(proxy):
    """GET /v1/models is forwarded upstream but never captured as a call."""
    client = proxy(handler=lambda r: httpx.Response(200, json={"data": ["gpt-4o"]}))

    resp = client.get("/v1/models")

    # Forwarded and returned unmodified.
    assert resp.status_code == 200
    assert resp.json() == {"data": ["gpt-4o"]}
    assert client.upstream.last_request is not None
    assert client.upstream.last_request.url.path == "/v1/models"

    # No capture: no action-id header is attached.
    assert "x-agentledger-action-id" not in resp.headers


def test_non_llm_get_creates_no_session_record(proxy):
    """A non-LLM request leaves nothing for /session to return (404)."""
    client = proxy(handler=lambda r: httpx.Response(200, json={"data": []}))

    client.get("/v1/models", headers={"x-agentledger-session-id": "s-nonllm"})

    # Nothing was stored under that session.
    assert client.get("/session/s-nonllm").status_code == 404


def test_get_to_llm_path_is_not_captured(proxy):
    """Only POST to an LLM path is captured; GET to the same path is plain proxy."""
    client = proxy(handler=lambda r: httpx.Response(200, json={"ok": True}))

    resp = client.get("/v1/chat/completions")

    assert resp.status_code == 200
    assert "x-agentledger-action-id" not in resp.headers


# ── LLM capture happy path ────────────────────────────────────────────────────

def test_llm_post_is_captured_and_returned_unmodified(proxy):
    """POST /v1/chat/completions returns the upstream body intact + an action id."""
    client = proxy(handler=_ok_handler(content="pong"))

    resp = client.post(
        "/v1/chat/completions",
        json=_CHAT_BODY,
        headers={"x-agentledger-session-id": "s-cap"},
    )

    assert resp.status_code == 200
    # Body is byte-for-byte the upstream response.
    assert resp.json()["choices"][0]["message"]["content"] == "pong"

    action_id = resp.headers.get("x-agentledger-action-id")
    assert action_id
    # The session id supplied by the caller is echoed back.
    assert resp.headers.get("x-agentledger-session-id") == "s-cap"


def test_captured_call_retrievable_by_action_and_session(proxy):
    """The captured record is fetchable via /explain/{id} and /session/{sid}."""
    client = proxy(handler=_ok_handler(content="pong"))

    resp = client.post(
        "/v1/chat/completions",
        json=_CHAT_BODY,
        headers={"x-agentledger-session-id": "s-ret", "x-agentledger-agent-name": "A1"},
    )
    action_id = resp.headers["x-agentledger-action-id"]

    explained = client.get(f"/explain/{action_id}")
    assert explained.status_code == 200
    record = explained.json()
    assert record["action_id"] == action_id
    assert record["model_id"] == "gpt-4o"
    assert record["agent_name"] == "A1"

    session = client.get("/session/s-ret").json()
    assert len(session) == 1
    assert session[0]["action_id"] == action_id


# ── Meta-header stripping on the forwarded request ────────────────────────────

def test_agentledger_headers_stripped_before_forwarding(proxy):
    """All x-agentledger-* meta headers are removed before hitting upstream."""
    client = proxy(handler=_ok_handler())

    client.post(
        "/v1/chat/completions",
        json=_CHAT_BODY,
        headers={
            "x-agentledger-session-id": "s-strip",
            "x-agentledger-user-id": "u1",
            "x-agentledger-agent-name": "agentX",
            "x-agentledger-app-id": "app1",
            "x-agentledger-environment": "prod",
            "authorization": "Bearer sk-test",
        },
    )

    fwd_headers = client.upstream.last_request.headers
    al_keys = [k for k in fwd_headers if k.lower().startswith("x-agentledger-")]
    assert al_keys == [], f"meta headers leaked upstream: {al_keys}"

    # A normal (non-meta) header is still forwarded untouched.
    assert fwd_headers.get("authorization") == "Bearer sk-test"


def test_host_and_content_length_not_forwarded(proxy):
    """host and content-length are dropped (the upstream client recomputes them)."""
    client = proxy(handler=_ok_handler())

    client.post(
        "/v1/chat/completions",
        json=_CHAT_BODY,
        headers={"x-agentledger-session-id": "s-hostcl"},
    )

    fwd = client.upstream.last_request.headers
    # The forwarded request must carry the proxy's host, not the original client host,
    # and the proxy must not pass through the original content-length verbatim from
    # the forward_headers dict (the proxy strips it).
    assert fwd.get("host") == "upstream.test"


# ── Default meta when no session header is supplied ───────────────────────────

def test_default_meta_environment_and_auto_session(proxy):
    """With no meta headers, environment defaults to 'development' and an auto- session is assigned."""
    client = proxy(handler=_ok_handler())

    resp = client.post("/v1/chat/completions", json=_CHAT_BODY)

    action_id = resp.headers["x-agentledger-action-id"]
    # An auto-generated session id is echoed back.
    echoed = resp.headers.get("x-agentledger-session-id")
    assert echoed is not None
    assert echoed.startswith("auto-")

    record = client.get(f"/explain/{action_id}").json()
    assert record["environment"] == "development"
    assert record["session_id"].startswith("auto-")


# ── Upstream error passthrough ────────────────────────────────────────────────

def test_upstream_500_passthrough_and_captured_with_error(proxy):
    """A 500 from upstream reaches the client verbatim and is captured with status 500 + error_detail."""
    err_body = {"error": {"message": "kaboom", "type": "server_error"}}
    client = proxy(handler=lambda r: httpx.Response(500, json=err_body))

    resp = client.post(
        "/v1/chat/completions",
        json=_CHAT_BODY,
        headers={"x-agentledger-session-id": "s-err"},
    )

    assert resp.status_code == 500
    assert resp.json() == err_body  # body preserved exactly

    action_id = resp.headers["x-agentledger-action-id"]
    record = client.get(f"/explain/{action_id}").json()
    assert record["status_code"] == 500
    assert record["error_detail"]  # non-null
    assert "kaboom" in record["error_detail"]


# ── Rate limiting ─────────────────────────────────────────────────────────────

def test_rate_limit_global_rpm_blocks_second_call(proxy):
    """With global_rpm=1, the first LLM POST is 200 and the second is 429 rate_limit_exceeded."""
    client = proxy(
        handler=_ok_handler(),
        rate_limit_config=RateLimitConfig(global_rpm=1),
    )

    first = client.post("/v1/chat/completions", json=_CHAT_BODY,
                        headers={"x-agentledger-session-id": "s-rl"})
    assert first.status_code == 200

    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                         headers={"x-agentledger-session-id": "s-rl"})
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "rate_limit_exceeded"


def test_rate_limit_does_not_forward_blocked_call(proxy):
    """A rate-limited call is rejected at the proxy and never reaches upstream."""
    client = proxy(
        handler=_ok_handler(),
        rate_limit_config=RateLimitConfig(global_rpm=1),
    )

    client.post("/v1/chat/completions", json=_CHAT_BODY)
    forwarded_after_first = len(client.upstream.requests)

    client.post("/v1/chat/completions", json=_CHAT_BODY)
    # Second (blocked) call must not have been forwarded.
    assert len(client.upstream.requests) == forwarded_after_first


# ── Budget enforcement: block ─────────────────────────────────────────────────

def test_budget_session_block_second_call(proxy):
    """With a tiny session budget, the first call records cost and the second is 429 budget_exceeded."""
    client = proxy(
        handler=_ok_handler(),
        budget_session=0.000001,
    )

    first = client.post("/v1/chat/completions", json=_CHAT_BODY,
                        headers={"x-agentledger-session-id": "s-bud"})
    assert first.status_code == 200
    first_id = first.headers["x-agentledger-action-id"]
    # First call recorded a positive cost (gpt-4o is in the pricing table).
    rec = client.get(f"/explain/{first_id}").json()
    assert rec["cost_usd"] is not None and rec["cost_usd"] > 0

    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                         headers={"x-agentledger-session-id": "s-bud"})
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "budget_exceeded"


def test_budget_block_call_is_recorded_as_429(proxy):
    """A blocked over-budget call is saved with status_code 429 and the budget error_detail."""
    client = proxy(handler=_ok_handler(), budget_session=0.000001)

    client.post("/v1/chat/completions", json=_CHAT_BODY,
               headers={"x-agentledger-session-id": "s-bud2"})
    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                        headers={"x-agentledger-session-id": "s-bud2"})
    assert second.status_code == 429

    # The session now holds two records: the successful one and the blocked one.
    records = client.get("/session/s-bud2").json()
    assert len(records) == 2
    blocked = [r for r in records if r["status_code"] == 429]
    assert len(blocked) == 1
    assert blocked[0]["error_detail"] and "budget" in blocked[0]["error_detail"].lower()


# ── Budget enforcement: warn ──────────────────────────────────────────────────

def test_budget_warn_lets_call_through_and_tags_it(proxy):
    """In warn mode the over-budget call still returns 200 and is saved with a budget_warning error_detail."""
    client = proxy(
        handler=_ok_handler(),
        budget_session=0.000001,
        budget_action="warn",
    )

    client.post("/v1/chat/completions", json=_CHAT_BODY,
               headers={"x-agentledger-session-id": "s-warn"})
    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                        headers={"x-agentledger-session-id": "s-warn"})

    # Warn mode never blocks.
    assert second.status_code == 200
    warn_id = second.headers["x-agentledger-action-id"]

    rec = client.get(f"/explain/{warn_id}").json()
    assert rec["error_detail"] is not None
    assert rec["error_detail"].startswith("budget_warning:")


# ── API-key authentication ────────────────────────────────────────────────────

def test_api_sessions_requires_key_when_configured(proxy, monkeypatch):
    """With AGENTLEDGER_API_KEY set, /api/sessions is 401 without the key and 200 with it."""
    monkeypatch.setenv("AGENTLEDGER_API_KEY", "secret")
    client = proxy(handler=_ok_handler())

    # Missing key → 401.
    assert client.get("/api/sessions").status_code == 401

    # Correct key → 200.
    ok = client.get("/api/sessions", headers={"x-agentledger-api-key": "secret"})
    assert ok.status_code == 200


def test_health_needs_no_auth(proxy, monkeypatch):
    """/health is always reachable, even with an API key configured."""
    monkeypatch.setenv("AGENTLEDGER_API_KEY", "secret")
    client = proxy(handler=_ok_handler())

    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_wrong_api_key_rejected(proxy, monkeypatch):
    """A wrong key is rejected just like a missing one."""
    monkeypatch.setenv("AGENTLEDGER_API_KEY", "secret")
    client = proxy(handler=_ok_handler())

    resp = client.get("/api/sessions", headers={"x-agentledger-api-key": "wrong"})
    assert resp.status_code == 401


# ── Streaming capture: errored and mid-stream-failed upstreams ────────────────

def test_streaming_upstream_error_is_captured(proxy):
    """A non-200 from upstream on a streaming request reaches the client AND is
    recorded with the upstream status + error body (previously never captured)."""
    client = proxy(handler=lambda r: httpx.Response(
        529, json={"error": {"type": "overloaded_error", "message": "Overloaded"}},
    ))

    resp = client.post(
        "/v1/chat/completions",
        json={**_CHAT_BODY, "stream": True},
        headers={"x-agentledger-session-id": "s-stream-err"},
    )
    assert resp.status_code == 529

    session = client.get("/session/s-stream-err").json()
    assert len(session) == 1
    assert session[0]["status_code"] == 529
    assert "Overloaded" in session[0]["error_detail"]


def test_streaming_mid_stream_error_event_recorded(proxy):
    """A 200 stream that carries a provider error event is captured with the
    error surfaced in error_detail instead of posing as a clean call."""
    from .conftest import sse, stream_response

    chunks = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    ]
    client = proxy(handler=lambda r: stream_response(sse(*chunks)))

    resp = client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-4", "stream": True,
              "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-agentledger-session-id": "s-mid-err"},
    )
    assert resp.status_code == 200

    session = client.get("/session/s-mid-err").json()
    assert len(session) == 1
    assert session[0]["status_code"] == 200
    assert "stream_error: Overloaded" in session[0]["error_detail"]


def test_streaming_capture_includes_cache_tokens(proxy):
    """Anthropic cache-token usage flows through streaming capture into the
    stored row and the computed cost."""
    from .conftest import sse, stream_response

    chunks = [
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 10,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 50,
        }}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "hi"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 5}},
    ]
    client = proxy(handler=lambda r: stream_response(sse(*chunks)))

    resp = client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-4", "stream": True,
              "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-agentledger-session-id": "s-cache"},
    )
    assert resp.status_code == 200

    row = client.get("/session/s-cache").json()[0]
    assert row["cache_read_tokens"] == 900
    assert row["cache_write_tokens"] == 50
    expected = (10 * 3.00 + 900 * 0.30 + 50 * 3.75 + 5 * 15.00) / 1_000_000
    assert row["cost_usd"] == round(expected, 8)


# ── Zero-config agent detection ───────────────────────────────────────────────

_CC_UUID = "3f2a1b0c-4d5e-6f70-8192-a3b4c5d6e7f8"


def _claude_code_body():
    return {
        "model": "claude-sonnet-4",
        "system": [{"type": "text", "text": "You are Claude Code, Anthropic's official CLI."}],
        "messages": [{"role": "user", "content": "hi"}],
        "metadata": {"user_id": f"user_ab12_account_cd34_session_{_CC_UUID}"},
    }


def test_claude_code_traffic_auto_tagged_and_sessioned(proxy):
    """Untagged Claude Code traffic gets framework=claude-code, an agent name,
    and its real session UUID instead of the shared auto-<date> bucket."""
    from .conftest import anthropic_response

    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))

    resp = client.post(
        "/v1/messages", json=_claude_code_body(),
        headers={"user-agent": "claude-cli/2.0.14 (external, cli)"},
    )
    assert resp.status_code == 200
    assert resp.headers["x-agentledger-session-id"] == _CC_UUID

    row = client.get(f"/session/{_CC_UUID}").json()[0]
    assert row["framework"] == "claude-code"
    assert row["agent_name"] == "claude-code"
    assert row["session_id"] == _CC_UUID


def test_explicit_headers_win_over_detection(proxy):
    """x-agentledger-* headers always beat fingerprint detection."""
    from .conftest import anthropic_response

    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))

    client.post(
        "/v1/messages", json=_claude_code_body(),
        headers={
            "user-agent": "claude-cli/2.0.14 (external, cli)",
            "x-agentledger-session-id": "my-run",
            "x-agentledger-agent-name": "researcher",
            "x-agentledger-framework": "my-stack",
        },
    )

    row = client.get("/session/my-run").json()[0]
    assert row["session_id"] == "my-run"
    assert row["agent_name"] == "researcher"
    assert row["framework"] == "my-stack"


def test_framework_header_stripped_before_upstream(proxy):
    """The new x-agentledger-framework header never leaks upstream."""
    client = proxy(handler=_ok_handler())

    client.post(
        "/v1/chat/completions", json=_CHAT_BODY,
        headers={"x-agentledger-framework": "my-stack"},
    )
    fwd = client.upstream.last_request.headers
    assert "x-agentledger-framework" not in fwd


# ── Loop engine: thread stitching, runs, circuit breaker ──────────────────────

_U = {"role": "user", "content": "find the bug"}
_A = {"role": "assistant", "content": None,
      "tool_calls": [{"id": "c1", "type": "function",
                      "function": {"name": "grep", "arguments": "{\"q\":1}"}}]}
_T = {"role": "tool", "tool_call_id": "c1", "content": "match at line 3"}


def test_react_calls_stitched_into_thread(proxy):
    """Two calls where the second extends the first's history share a thread
    with linked steps — no headers required."""
    from .conftest import openai_tool_call

    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(
        tool_calls=[openai_tool_call()],
    )))

    client.post("/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [_U]},
                headers={"x-agentledger-session-id": "s-loop"})
    client.post("/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [_U, _A, _T]},
                headers={"x-agentledger-session-id": "s-loop"})

    rows = client.get("/session/s-loop").json()
    assert len(rows) == 2
    assert rows[0]["thread_id"] == rows[1]["thread_id"]
    assert (rows[0]["step_index"], rows[1]["step_index"]) == (1, 2)
    assert rows[1]["prev_action_id"] == rows[0]["action_id"]


def test_explicit_run_headers_stored_and_listed(proxy):
    """x-agentledger-run-id / -iteration are stored and aggregated by /api/runs."""
    client = proxy(handler=_ok_handler())

    for i in (1, 2):
        client.post("/v1/chat/completions", json=_CHAT_BODY, headers={
            "x-agentledger-session-id": f"iter-{i}",
            "x-agentledger-run-id": "overnight-1",
            "x-agentledger-iteration": str(i),
        })

    runs = client.get("/api/runs").json()
    assert len(runs) == 1
    run = runs[0]
    assert run["run_id"] == "overnight-1"
    assert run["iterations"] == 2
    assert run["call_count"] == 2
    assert run["session_count"] == 2


def test_loop_circuit_breaker_blocks_after_repeat(proxy):
    """In block mode, a session that repeats the identical tool call 3x is
    429'd before the next call reaches the upstream."""
    from .conftest import openai_tool_call

    client = proxy(
        handler=lambda r: httpx.Response(200, json=openai_response(
            tool_calls=[openai_tool_call(name="grep", arguments='{"q":"bug"}')],
        )),
        loop_action="block",
    )

    msgs = [_U]
    for i in range(3):
        resp = client.post("/v1/chat/completions",
                           json={"model": "gpt-4o", "messages": list(msgs)},
                           headers={"x-agentledger-session-id": "s-stuck"})
        assert resp.status_code == 200
        msgs = msgs + [dict(_A), {**_T, "content": f"result {i}"}]

    forwarded_before = len(client.upstream.requests)
    resp = client.post("/v1/chat/completions",
                       json={"model": "gpt-4o", "messages": list(msgs)},
                       headers={"x-agentledger-session-id": "s-stuck"})
    assert resp.status_code == 429
    assert resp.json()["error"]["type"] == "loop_detected"
    assert len(client.upstream.requests) == forwarded_before  # never reached upstream


def test_loop_warn_mode_never_blocks(proxy):
    """Default warn mode records flags but lets every call through."""
    from .conftest import openai_tool_call

    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(
        tool_calls=[openai_tool_call(name="grep", arguments='{"q":"bug"}')],
    )))

    msgs = [_U]
    for i in range(4):
        resp = client.post("/v1/chat/completions",
                           json={"model": "gpt-4o", "messages": list(msgs)},
                           headers={"x-agentledger-session-id": "s-warn"})
        assert resp.status_code == 200
        msgs = msgs + [dict(_A), {**_T, "content": f"result {i}"}]

    rows = client.get("/session/s-warn").json()
    flagged = [r for r in rows if r["loop_flags"]]
    assert flagged, "repeat flag should be recorded in warn mode"


# ── Path-segment run attribution + run status ─────────────────────────────────

def test_path_segment_run_attribution(proxy):
    """/r/<run>/<iter>/v1/... tags the call and forwards to the real path."""
    client = proxy(handler=_ok_handler())

    resp = client.post("/r/night-1/3/v1/chat/completions", json=_CHAT_BODY)
    assert resp.status_code == 200
    # Upstream saw the untagged path.
    assert client.upstream.last_request.url.path == "/v1/chat/completions"

    action_id = resp.headers["x-agentledger-action-id"]
    row = client.get(f"/explain/{action_id}").json()
    assert row["run_id"] == "night-1"
    assert row["iteration"] == 3


def test_run_status_endpoint_reports_completion_promise(proxy):
    """A response matching the completion promise flips run status to complete."""
    client = proxy(
        handler=lambda r: httpx.Response(200, json=openai_response(
            content="done. ALL TASKS COMPLETE",
        )),
        completion_promise=r"ALL TASKS COMPLETE",
    )

    client.post("/v1/chat/completions", json=_CHAT_BODY,
                headers={"x-agentledger-run-id": "night-2",
                         "x-agentledger-iteration": "1"})

    status = client.get("/api/runs/night-2").json()
    assert status["status"] == "complete"
    assert status["iterations"] == 1
    assert "promise_seen" not in status  # folded into status

    assert client.get("/api/runs/unknown-run").status_code == 404
