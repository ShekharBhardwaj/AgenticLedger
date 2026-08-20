"""Integration tests for agenticledger/proxy/app.py via the `proxy` fixture.

These exercise the transparent-proxy semantics described in the module docstring:
capture of LLM POSTs, pass-through of non-LLM traffic, meta-header stripping,
default meta assignment, upstream error passthrough, rate limiting, budget
enforcement (block + warn), and API-key auth.

Each test builds a fresh proxy + mock upstream so cases stay isolated.
"""

import httpx

from agenticledger.proxy.ratelimit import RateLimitConfig

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
    assert "x-agenticledger-action-id" not in resp.headers


def test_non_llm_get_creates_no_session_record(proxy):
    """A non-LLM request leaves nothing for /session to return (404)."""
    client = proxy(handler=lambda r: httpx.Response(200, json={"data": []}))

    client.get("/v1/models", headers={"x-agenticledger-session-id": "s-nonllm"})

    # Nothing was stored under that session.
    assert client.get("/session/s-nonllm").status_code == 404


def test_get_to_llm_path_is_not_captured(proxy):
    """Only POST to an LLM path is captured; GET to the same path is plain proxy."""
    client = proxy(handler=lambda r: httpx.Response(200, json={"ok": True}))

    resp = client.get("/v1/chat/completions")

    assert resp.status_code == 200
    assert "x-agenticledger-action-id" not in resp.headers


# ── LLM capture happy path ────────────────────────────────────────────────────

def test_llm_post_is_captured_and_returned_unmodified(proxy):
    """POST /v1/chat/completions returns the upstream body intact + an action id."""
    client = proxy(handler=_ok_handler(content="pong"))

    resp = client.post(
        "/v1/chat/completions",
        json=_CHAT_BODY,
        headers={"x-agenticledger-session-id": "s-cap"},
    )

    assert resp.status_code == 200
    # Body is byte-for-byte the upstream response.
    assert resp.json()["choices"][0]["message"]["content"] == "pong"

    action_id = resp.headers.get("x-agenticledger-action-id")
    assert action_id
    # The session id supplied by the caller is echoed back.
    assert resp.headers.get("x-agenticledger-session-id") == "s-cap"


def test_captured_call_retrievable_by_action_and_session(proxy):
    """The captured record is fetchable via /explain/{id} and /session/{sid}."""
    client = proxy(handler=_ok_handler(content="pong"))

    resp = client.post(
        "/v1/chat/completions",
        json=_CHAT_BODY,
        headers={"x-agenticledger-session-id": "s-ret", "x-agenticledger-agent-name": "A1"},
    )
    action_id = resp.headers["x-agenticledger-action-id"]

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

def test_agenticledger_headers_stripped_before_forwarding(proxy):
    """All x-agenticledger-* meta headers are removed before hitting upstream."""
    client = proxy(handler=_ok_handler())

    client.post(
        "/v1/chat/completions",
        json=_CHAT_BODY,
        headers={
            "x-agenticledger-session-id": "s-strip",
            "x-agenticledger-user-id": "u1",
            "x-agenticledger-agent-name": "agentX",
            "x-agenticledger-app-id": "app1",
            "x-agenticledger-environment": "prod",
            "authorization": "Bearer sk-test",
        },
    )

    fwd_headers = client.upstream.last_request.headers
    al_keys = [k for k in fwd_headers if k.lower().startswith("x-agenticledger-")]
    assert al_keys == [], f"meta headers leaked upstream: {al_keys}"

    # A normal (non-meta) header is still forwarded untouched.
    assert fwd_headers.get("authorization") == "Bearer sk-test"


def test_host_and_content_length_not_forwarded(proxy):
    """host and content-length are dropped (the upstream client recomputes them)."""
    client = proxy(handler=_ok_handler())

    client.post(
        "/v1/chat/completions",
        json=_CHAT_BODY,
        headers={"x-agenticledger-session-id": "s-hostcl"},
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

    action_id = resp.headers["x-agenticledger-action-id"]
    # An auto-generated session id is echoed back.
    echoed = resp.headers.get("x-agenticledger-session-id")
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
        headers={"x-agenticledger-session-id": "s-err"},
    )

    assert resp.status_code == 500
    assert resp.json() == err_body  # body preserved exactly

    action_id = resp.headers["x-agenticledger-action-id"]
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
                        headers={"x-agenticledger-session-id": "s-rl"})
    assert first.status_code == 200

    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                         headers={"x-agenticledger-session-id": "s-rl"})
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
                        headers={"x-agenticledger-session-id": "s-bud"})
    assert first.status_code == 200
    first_id = first.headers["x-agenticledger-action-id"]
    # First call recorded a positive cost (gpt-4o is in the pricing table).
    rec = client.get(f"/explain/{first_id}").json()
    assert rec["cost_usd"] is not None and rec["cost_usd"] > 0

    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                         headers={"x-agenticledger-session-id": "s-bud"})
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "budget_exceeded"


def test_budget_block_call_is_recorded_as_429(proxy):
    """A blocked over-budget call is saved with status_code 429 and the budget error_detail."""
    client = proxy(handler=_ok_handler(), budget_session=0.000001)

    client.post("/v1/chat/completions", json=_CHAT_BODY,
               headers={"x-agenticledger-session-id": "s-bud2"})
    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                        headers={"x-agenticledger-session-id": "s-bud2"})
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
               headers={"x-agenticledger-session-id": "s-warn"})
    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                        headers={"x-agenticledger-session-id": "s-warn"})

    # Warn mode never blocks.
    assert second.status_code == 200
    warn_id = second.headers["x-agenticledger-action-id"]

    rec = client.get(f"/explain/{warn_id}").json()
    assert rec["error_detail"] is not None
    assert rec["error_detail"].startswith("budget_warning:")


# ── API-key authentication ────────────────────────────────────────────────────

def test_api_sessions_requires_key_when_configured(proxy, monkeypatch):
    """With AGENTICLEDGER_API_KEY set, /api/sessions is 401 without the key and 200 with it."""
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "secret")
    client = proxy(handler=_ok_handler())

    # Missing key → 401.
    assert client.get("/api/sessions").status_code == 401

    # Correct key → 200.
    ok = client.get("/api/sessions", headers={"x-agenticledger-api-key": "secret"})
    assert ok.status_code == 200


def test_health_needs_no_auth(proxy, monkeypatch):
    """/health is always reachable, even with an API key configured."""
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "secret")
    client = proxy(handler=_ok_handler())

    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_wrong_api_key_rejected(proxy, monkeypatch):
    """A wrong key is rejected just like a missing one."""
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "secret")
    client = proxy(handler=_ok_handler())

    resp = client.get("/api/sessions", headers={"x-agenticledger-api-key": "wrong"})
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
        headers={"x-agenticledger-session-id": "s-stream-err"},
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
        headers={"x-agenticledger-session-id": "s-mid-err"},
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
        headers={"x-agenticledger-session-id": "s-cache"},
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
    assert resp.headers["x-agenticledger-session-id"] == _CC_UUID

    row = client.get(f"/session/{_CC_UUID}").json()[0]
    assert row["framework"] == "claude-code"
    assert row["agent_name"] == "claude-code"
    assert row["session_id"] == _CC_UUID


def test_explicit_headers_win_over_detection(proxy):
    """x-agenticledger-* headers always beat fingerprint detection."""
    from .conftest import anthropic_response

    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))

    client.post(
        "/v1/messages", json=_claude_code_body(),
        headers={
            "user-agent": "claude-cli/2.0.14 (external, cli)",
            "x-agenticledger-session-id": "my-run",
            "x-agenticledger-agent-name": "researcher",
            "x-agenticledger-framework": "my-stack",
        },
    )

    row = client.get("/session/my-run").json()[0]
    assert row["session_id"] == "my-run"
    assert row["agent_name"] == "researcher"
    assert row["framework"] == "my-stack"


def test_framework_header_stripped_before_upstream(proxy):
    """The new x-agenticledger-framework header never leaks upstream."""
    client = proxy(handler=_ok_handler())

    client.post(
        "/v1/chat/completions", json=_CHAT_BODY,
        headers={"x-agenticledger-framework": "my-stack"},
    )
    fwd = client.upstream.last_request.headers
    assert "x-agenticledger-framework" not in fwd


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
                headers={"x-agenticledger-session-id": "s-loop"})
    client.post("/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [_U, _A, _T]},
                headers={"x-agenticledger-session-id": "s-loop"})

    rows = client.get("/session/s-loop").json()
    assert len(rows) == 2
    assert rows[0]["thread_id"] == rows[1]["thread_id"]
    assert (rows[0]["step_index"], rows[1]["step_index"]) == (1, 2)
    assert rows[1]["prev_action_id"] == rows[0]["action_id"]


def test_explicit_run_headers_stored_and_listed(proxy):
    """x-agenticledger-run-id / -iteration are stored and aggregated by /api/runs."""
    client = proxy(handler=_ok_handler())

    for i in (1, 2):
        client.post("/v1/chat/completions", json=_CHAT_BODY, headers={
            "x-agenticledger-session-id": f"iter-{i}",
            "x-agenticledger-run-id": "overnight-1",
            "x-agenticledger-iteration": str(i),
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
                           headers={"x-agenticledger-session-id": "s-stuck"})
        assert resp.status_code == 200
        msgs = msgs + [dict(_A), {**_T, "content": f"result {i}"}]

    forwarded_before = len(client.upstream.requests)
    resp = client.post("/v1/chat/completions",
                       json={"model": "gpt-4o", "messages": list(msgs)},
                       headers={"x-agenticledger-session-id": "s-stuck"})
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
                           headers={"x-agenticledger-session-id": "s-warn"})
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

    action_id = resp.headers["x-agenticledger-action-id"]
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
                headers={"x-agenticledger-run-id": "night-2",
                         "x-agenticledger-iteration": "1"})

    status = client.get("/api/runs/night-2").json()
    assert status["status"] == "complete"
    assert status["iterations"] == 1
    assert "promise_seen" not in status  # folded into status

    assert client.get("/api/runs/unknown-run").status_code == 404


def test_tool_executions_endpoint(proxy):
    """Paired tool executions are persisted and served per session."""
    from .conftest import openai_tool_call

    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(
        tool_calls=[openai_tool_call(name="grep", arguments='{"q":"bug"}')],
    )))

    client.post("/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [_U]},
                headers={"x-agenticledger-session-id": "s-tools"})
    client.post("/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [
                    _U,
                    {"role": "assistant", "content": None,
                     "tool_calls": [{"id": "call_1", "type": "function",
                                     "function": {"name": "grep", "arguments": '{"q":"bug"}'}}]},
                    {"role": "tool", "tool_call_id": "call_1", "content": "found it"},
                ]},
                headers={"x-agenticledger-session-id": "s-tools"})

    tools = client.get("/api/sessions/s-tools/tools").json()
    assert len(tools) == 1
    assert tools[0]["tool_name"] == "grep"
    assert tools[0]["latency_ms"] >= 0
    assert tools[0]["issued_by_action_id"] != tools[0]["resolved_by_action_id"]


# ── Run iterations endpoint + SPA serving ─────────────────────────────────────

def test_run_iterations_endpoint(proxy):
    client = proxy(handler=_ok_handler())
    for i in (1, 1, 2):
        client.post("/v1/chat/completions", json=_CHAT_BODY, headers={
            "x-agenticledger-session-id": f"ri-{i}",
            "x-agenticledger-run-id": "iter-run",
            "x-agenticledger-iteration": str(i),
        })

    its = client.get("/api/runs/iter-run/iterations").json()
    assert [it["iteration"] for it in its] == [1, 2]
    assert its[0]["call_count"] == 2
    assert its[1]["call_count"] == 1
    assert its[0]["cost_usd"] > 0
    # Each iteration carries a session id for the Loop Lens click-through,
    # and says how many sessions it really holds (#86).
    assert its[0]["session_id"] == "ri-1"
    assert its[1]["session_id"] == "ri-2"
    assert [it["session_count"] for it in its] == [1, 1]

    # A reused iteration number across a DIFFERENT session (rerun run id,
    # or merged identical loops) must be counted honestly, not shown as
    # one session.
    client.post("/v1/chat/completions", json=_CHAT_BODY, headers={
        "x-agenticledger-session-id": "ri-other",
        "x-agenticledger-run-id": "iter-run",
        "x-agenticledger-iteration": "1",
    })
    its = client.get("/api/runs/iter-run/iterations").json()
    assert its[0]["call_count"] == 3
    assert its[0]["session_count"] == 2


def test_spa_served_or_explains_absence(proxy):
    """/app serves the built SPA when assets exist, else a clear 404."""
    import pathlib

    client = proxy(handler=_ok_handler())
    built = (pathlib.Path("agenticledger/proxy/static/index.html")).is_file()
    resp = client.get("/app")
    if built:
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
    else:
        assert resp.status_code == 404
        assert "npm run build" in resp.json()["detail"]


def test_spa_asset_unknown_file_404s(proxy):
    """The asset route only serves real files from the build directory.
    (Encoded-slash traversal can't even reach it — a single-segment path
    param never matches a decoded slash, so such URLs fall through to the
    upstream proxy; the resolve() guard is defense-in-depth.)"""
    client = proxy(handler=_ok_handler())
    assert client.get("/app/assets/nope.js").status_code == 404


# ── count_tokens capture ──────────────────────────────────────────────────────

_COUNT_BODY = {"model": "claude-sonnet-5", "messages": [{"role": "user", "content": "hi"}]}


def _count_tokens_handler(r):
    if r.url.path.endswith("/count_tokens"):
        return httpx.Response(200, json={"input_tokens": 2095})
    return httpx.Response(200, json=openai_response())


def test_count_tokens_is_captured_and_returned_unmodified(proxy):
    """POST /v1/messages/count_tokens is forwarded intact and recorded with a
    stop_reason marker, the count in content, and neither tokens nor cost."""
    client = proxy(handler=_count_tokens_handler)

    resp = client.post("/v1/messages/count_tokens", json=_COUNT_BODY,
                       headers={"x-agenticledger-session-id": "s-count"})

    assert resp.status_code == 200
    assert resp.json() == {"input_tokens": 2095}
    action_id = resp.headers["x-agenticledger-action-id"]

    record = client.get(f"/explain/{action_id}").json()
    assert record["stop_reason"] == "count_tokens"
    assert record["model_id"] == "claude-sonnet-5"
    assert record["provider"] == "anthropic"
    assert record["content"] == "input_tokens: 2095"
    assert record["tokens_in"] is None and record["tokens_out"] is None
    assert record["cost_usd"] == 0.0


def test_count_tokens_does_not_pollute_session_aggregates(proxy):
    """A count_tokens call adds nothing to a session's token/cost totals."""
    client = proxy(handler=_count_tokens_handler)

    client.post("/v1/messages/count_tokens", json=_COUNT_BODY,
                headers={"x-agenticledger-session-id": "s-count-agg"})

    row = next(s for s in client.get("/api/sessions").json()
               if s["session_id"] == "s-count-agg")
    assert row["call_count"] == 1
    assert (row["total_tokens_in"] or 0) == 0
    assert (row["total_tokens_out"] or 0) == 0
    assert (row["total_cost_usd"] or 0) == 0


def test_count_tokens_exempt_from_rate_limit_and_budget(proxy):
    """count_tokens is free: it must neither consume rate-limit quota nor be
    blocked once a budget is exhausted."""
    client = proxy(
        handler=_count_tokens_handler,
        rate_limit_config=RateLimitConfig(global_rpm=1),
        budget_session=0.000001,
    )

    # Exhaust both the rate limit and the session budget with one paid call.
    first = client.post("/v1/chat/completions", json=_CHAT_BODY,
                        headers={"x-agenticledger-session-id": "s-count-free"})
    assert first.status_code == 200

    # count_tokens still passes…
    counted = client.post("/v1/messages/count_tokens", json=_COUNT_BODY,
                          headers={"x-agenticledger-session-id": "s-count-free"})
    assert counted.status_code == 200

    # …while a paid call is now rejected.
    blocked = client.post("/v1/chat/completions", json=_CHAT_BODY,
                          headers={"x-agenticledger-session-id": "s-count-free"})
    assert blocked.status_code == 429


def test_count_tokens_does_not_pollute_loop_inference(proxy):
    """A count_tokens call with the same message history must not consume a
    step in the thread the real call belongs to."""
    def handler(r):
        if r.url.path.endswith("/count_tokens"):
            return httpx.Response(200, json={"input_tokens": 512})
        return httpx.Response(200, json=openai_response())

    client = proxy(handler=handler)
    body = {"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "hi"}]}
    client.post("/v1/messages/count_tokens", json=body,
                headers={"x-agenticledger-session-id": "s-ct-loop"})
    client.post("/v1/messages", json=body,
                headers={"x-agenticledger-session-id": "s-ct-loop"})

    rows = client.get("/session/s-ct-loop").json()
    count_row = next(r for r in rows if r["stop_reason"] == "count_tokens")
    real_row = next(r for r in rows if r["stop_reason"] != "count_tokens")
    assert count_row["thread_id"] is None
    assert real_row["step_index"] == 1


def test_run_flags_drilldown_endpoint(proxy):
    """/api/runs/{id}/flags returns the flagged calls with context."""
    from .conftest import openai_tool_call

    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response(
        tool_calls=[openai_tool_call(name="grep", arguments='{"q":"bug"}')],
    )))

    msgs = [_U]
    for i in range(3):
        client.post("/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": list(msgs)},
                    headers={"x-agenticledger-session-id": "s-flags",
                             "x-agenticledger-run-id": "flag-run",
                             "x-agenticledger-iteration": "1"})
        msgs = msgs + [dict(_A), {**_T, "content": f"result {i}"}]

    flags = client.get("/api/runs/flag-run/flags").json()
    assert len(flags) == 1
    f = flags[0]
    assert f["loop_flags"] == '["repeat_tool_call"]'
    assert f["session_id"] == "s-flags"
    assert f["iteration"] == 1
    assert f["tool_calls"][0]["name"] == "grep"


def test_home_serves_the_web_app(proxy):
    """/ serves the web app; the classic dashboard is gone (#46)."""
    client = proxy(handler=_ok_handler())

    home = client.get("/")
    assert home.status_code in (200, 404)  # 404 = source checkout without build
    if home.status_code == 200:
        assert "text/html" in home.headers["content-type"]
    # /classic is no longer a ledger route — it falls through to the
    # catch-all upstream proxy like any other unknown path.


def test_claude_code_utility_calls_stay_out_of_loop_inference(proxy):
    """A small-max_tokens Claude Code call (title/summary housekeeping) is
    captured but not chained into threads."""
    from .conftest import anthropic_response

    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))
    headers = {"user-agent": "claude-cli/2.0.14 (external, cli)",
               "x-agenticledger-session-id": "s-util"}

    client.post("/v1/messages",
                json={"model": "claude-3-5-haiku", "max_tokens": 512,
                      "messages": [{"role": "user", "content": "Summarize this conversation"}]},
                headers=headers)
    client.post("/v1/messages",
                json={"model": "claude-sonnet-4", "max_tokens": 32000,
                      "messages": [{"role": "user", "content": "fix the bug"}]},
                headers=headers)

    rows = client.get("/session/s-util").json()
    utility = next(r for r in rows if r["max_tokens"] == 512)
    real = next(r for r in rows if r["max_tokens"] == 32000)
    assert utility["thread_id"] is None
    assert utility["step_index"] is None
    assert real["thread_id"] is not None
    assert real["step_index"] == 1


def test_run_complete_webhook_fires_morning_report(proxy, monkeypatch):
    """The completion promise triggers a run_complete webhook with the run's
    full summary — the morning report for overnight loops."""
    import agenticledger.proxy.alerts as alerts_mod
    from agenticledger.proxy.alerts import AlertConfig

    fired = []

    async def _collect(url, payload):
        fired.append(payload)

    monkeypatch.setattr(alerts_mod, "_fire", _collect)

    client = proxy(
        handler=lambda r: httpx.Response(200, json=openai_response(
            content="done. ALL TASKS COMPLETE",
        )),
        completion_promise=r"ALL TASKS COMPLETE",
        alert_config=AlertConfig(
            webhook_url="https://hooks.example.test/alert",
            cost_per_call=None, latency_ms=None, error_rate=None, daily_spend=None,
        ),
    )

    for i in (1, 2):
        client.post("/v1/chat/completions", json=_CHAT_BODY, headers={
            "x-agenticledger-session-id": f"mr-{i}",
            "x-agenticledger-run-id": "night-run",
            "x-agenticledger-iteration": str(i),
        })

    reports = [p for p in fired if p.get("type") == "run_complete"]
    assert reports, f"no run_complete among {[p.get('type') for p in fired]}"
    report = reports[-1]
    assert report["run_id"] == "night-run"
    assert report["call_count"] >= 1
    assert "complete" in report["message"]


def test_budget_user_daily_block(proxy):
    """AGENTICLEDGER_BUDGET_USER: the same user_id is blocked across
    different sessions once their daily spend crosses the cap."""
    client = proxy(handler=_ok_handler(), budget_user=0.000001)

    first = client.post("/v1/chat/completions", json=_CHAT_BODY,
                        headers={"x-agenticledger-session-id": "u-s1",
                                 "x-agenticledger-user-id": "shekhar"})
    assert first.status_code == 200

    # New session, same user — still blocked: the budget follows the user.
    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                         headers={"x-agenticledger-session-id": "u-s2",
                                  "x-agenticledger-user-id": "shekhar"})
    assert second.status_code == 429
    assert second.json()["error"]["type"] == "budget_exceeded"
    assert "User daily budget" in second.json()["error"]["message"]

    # A different user is unaffected.
    other = client.post("/v1/chat/completions", json=_CHAT_BODY,
                        headers={"x-agenticledger-session-id": "u-s3",
                                 "x-agenticledger-user-id": "someone-else"})
    assert other.status_code == 200


def test_budget_daily_block_carries_retry_after(proxy):
    """Issue #27: daily-window budget blocks tell clients when retrying can
    actually succeed (seconds until UTC midnight) instead of inviting a storm."""
    client = proxy(handler=_ok_handler(), budget_daily=0.000001)
    client.post("/v1/chat/completions", json=_CHAT_BODY,
                headers={"x-agenticledger-session-id": "ra-1"})
    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                         headers={"x-agenticledger-session-id": "ra-2"})
    assert second.status_code == 429
    retry_after = int(second.headers["retry-after"])
    assert 0 < retry_after <= 86400


def test_budget_status_402_option(proxy):
    """Issue #27: AGENTICLEDGER_BUDGET_STATUS=402 opts into Payment Required,
    which clients never retry. Session budgets never reset → no Retry-After."""
    client = proxy(handler=_ok_handler(), budget_session=0.000001, budget_status=402)
    client.post("/v1/chat/completions", json=_CHAT_BODY,
                headers={"x-agenticledger-session-id": "s-402"})
    second = client.post("/v1/chat/completions", json=_CHAT_BODY,
                         headers={"x-agenticledger-session-id": "s-402"})
    assert second.status_code == 402
    assert "retry-after" not in second.headers


def test_finished_run_reads_ended_and_lists_models(proxy):
    """Issues #17 + #18: a run whose last call is older than the run-gap
    window reads 'ended' (not 'running' forever), and run aggregates carry
    the distinct models."""
    import time as _time
    client = proxy(handler=_ok_handler(), loop_run_gap_seconds=0.01)
    client.post("/v1/chat/completions", json=_CHAT_BODY,
                headers={"x-agenticledger-session-id": "er-s",
                         "x-agenticledger-run-id": "er-run",
                         "x-agenticledger-iteration": "1"})
    _time.sleep(0.05)
    run = client.get("/api/runs/er-run").json()
    assert run["status"] == "ended"
    assert "gpt-4o" in (run.get("models") or "")


def test_run_end_marker_flips_status_immediately(proxy):
    """Issue #29: the runner's exit signal marks the run ended at once —
    no waiting for the inactivity window."""
    client = proxy(handler=_ok_handler())  # default 900s gap
    client.post("/v1/chat/completions", json=_CHAT_BODY,
                headers={"x-agenticledger-session-id": "em-s",
                         "x-agenticledger-run-id": "em-run",
                         "x-agenticledger-iteration": "1"})
    # Fresh call → inference alone would say "running".
    assert client.get("/api/runs/em-run").json()["status"] == "running"
    assert client.post("/api/runs/em-run/end").status_code == 200
    assert client.get("/api/runs/em-run").json()["status"] == "ended"
    # Completion promise / flags still outrank the marker; unknown run 404s.
    assert client.post("/api/runs/nope/end").status_code == 404


def test_failed_calls_name_their_status_and_endpoint(proxy):
    """A red badge with no reason is a dead end — every failure says what
    happened and where, even when the provider sends an empty body."""
    client = proxy(handler=lambda r: httpx.Response(404, content=b""))
    resp = client.post("/v1/messages?beta=true",
                       json={"model": "claude-opus-5", "max_tokens": 16,
                             "messages": [{"role": "user", "content": "quota"}]},
                       headers={"x-agenticledger-session-id": "err-1"})
    assert resp.status_code == 404
    rec = client.get("/session/err-1").json()[0]
    assert "upstream 404" in rec["error_detail"]
    assert "/v1/messages" in rec["error_detail"]
    assert "no error body" in rec["error_detail"]


def test_failed_stream_names_its_endpoint_too(proxy):
    client = proxy(handler=lambda r: httpx.Response(404, content=b""))
    resp = client.post("/v1/messages",
                       json={"model": "claude-opus-5", "max_tokens": 16, "stream": True,
                             "messages": [{"role": "user", "content": "quota"}]},
                       headers={"x-agenticledger-session-id": "err-2"})
    assert resp.status_code == 404
    rec = client.get("/session/err-2").json()[0]
    assert "upstream 404" in rec["error_detail"] and "/v1/messages" in rec["error_detail"]


def test_anthropic_call_to_openai_upstream_explains_itself(proxy):
    """The most confusing misconfiguration there is: the provider answers 404
    with no body and the agent says 'that model does not exist'."""
    client = proxy(handler=lambda r: httpx.Response(404, content=b""),
                   upstream_url="https://api.openai.com")
    client.post("/v1/messages",
                json={"model": "claude-opus-5", "max_tokens": 16,
                      "messages": [{"role": "user", "content": "hi"}]},
                headers={"x-agenticledger-session-id": "mismatch-1"})
    detail = client.get("/session/mismatch-1").json()[0]["error_detail"]
    assert "anthropic-format request" in detail
    assert "api.openai.com" in detail and "upstream_url" in detail


def test_matching_and_unknown_upstreams_stay_quiet(proxy):
    from agenticledger.proxy.app import wire_format_mismatch

    assert wire_format_mismatch("v1/messages", "https://api.anthropic.com") == ""
    assert wire_format_mismatch("v1/chat/completions", "https://api.openai.com") == ""
    # Gateways and local servers speak whatever they like — not our business.
    assert wire_format_mismatch("v1/chat/completions", "http://localhost:1234") == ""
    assert wire_format_mismatch("v1/messages", "http://localhost:4000") == ""
    assert "openai-format" in wire_format_mismatch(
        "v1/chat/completions", "https://api.anthropic.com")


def test_red_means_your_agent_had_a_problem(proxy):
    """#58: probes and provider hiccups are not agent failures. The error
    count keeps meaning something."""
    calls = {"n": 0}

    def handler(r):
        calls["n"] += 1
        return httpx.Response({1: 404, 2: 429, 3: 500}.get(calls["n"], 200),
                              content=b"", headers={})

    client = proxy(handler=handler)
    h = {"x-agenticledger-session-id": "sem-1"}
    # 1: a quota probe fails (404) — routine, not an error.
    client.post("/v1/messages", json={"model": "claude-opus-5", "max_tokens": 8,
                                      "messages": [{"role": "user", "content": "quota"}]},
                headers=h)
    # 2: a real request hits an upstream 429 — transient, not an agent error.
    client.post("/v1/messages", json={"model": "claude-opus-5", "max_tokens": 64,
                                      "system": "be helpful",
                                      "messages": [{"role": "user", "content": "do real work please"}]},
                headers=h)
    # 3: a real request gets a 500 — that IS an error.
    client.post("/v1/messages", json={"model": "claude-opus-5", "max_tokens": 64,
                                      "system": "be helpful",
                                      "messages": [{"role": "user", "content": "do real work please"}]},
                headers=h)
    rows = client.get("/session/sem-1").json()
    details = [r["error_detail"] for r in rows]
    assert any(d.startswith("probe: ") for d in details)
    assert any(d.startswith("transient: ") for d in details)
    sess = {x["session_id"]: x for x in client.get("/api/sessions").json()}["sem-1"]
    assert sess["error_count"] == 1        # only the 500
    assert sess["blocked_count"] == 0      # transient/probe are not "blocked"
    report = client.get("/api/reports?days=1").json()
    assert report["totals"]["error_calls"] == 1


# ── Zero-config upstream routing (0.8.1) ─────────────────────────────────────
#
# With no upstream configured, the proxy answers the knock: Anthropic-format
# paths go to Anthropic, OpenAI-format paths keep the OpenAI-format default.
# An explicitly configured upstream never creates the second client, so it
# always wins (covered by every other test in this file, which pins one).

def _swap_anthropic_client(client, log):
    from .conftest import anthropic_response

    def _handler(request: httpx.Request) -> httpx.Response:
        log.append(str(request.url))
        return httpx.Response(200, json=anthropic_response())

    client.app.state.client_anthropic = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler),
        base_url="https://api.anthropic.com",
        timeout=httpx.Timeout(120.0),
    )


def test_auto_mode_routes_anthropic_knock_to_anthropic(proxy):
    client = proxy(upstream_auto=True)
    anthropic_seen: list[str] = []
    _swap_anthropic_client(client, anthropic_seen)

    resp = client.post("/v1/messages", json={
        "model": "claude-opus-5", "max_tokens": 2048,
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 200
    assert anthropic_seen and "api.anthropic.com" in anthropic_seen[0]
    # The default (OpenAI-format) upstream never saw the call.
    assert client.upstream.last_request is None


def test_auto_mode_keeps_openai_knock_on_default(proxy):
    client = proxy(upstream_auto=True)
    anthropic_seen: list[str] = []
    _swap_anthropic_client(client, anthropic_seen)

    resp = client.post("/v1/chat/completions", json={
        "model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}],
    })
    assert resp.status_code == 200
    assert anthropic_seen == []
    assert client.upstream.last_json()["model"] == "gpt-4o"


def test_auto_mode_never_emits_wire_format_mismatch(proxy):
    """In auto mode the destination matches the knock by construction, so a
    404 is a real 404 and must not carry the mismatch hint."""
    client = proxy(upstream_auto=True)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"message": "no such model"}})

    client.app.state.client_anthropic = httpx.AsyncClient(
        transport=httpx.MockTransport(_handler),
        base_url="https://api.anthropic.com",
        timeout=httpx.Timeout(120.0),
    )
    client.post("/v1/messages", json={
        "model": "claude-nonexistent", "max_tokens": 16,
        "messages": [{"role": "user", "content": "hi"}],
        "metadata": {"user_id": "session_11111111-2222-3333-4444-555555555555"},
    }, headers={"x-agenticledger-session-id": "auto-404"})
    detail = client.get("/session/auto-404").json()[0]["error_detail"]
    assert "upstream 404" in detail
    assert "wire" not in detail and "format request" not in detail
