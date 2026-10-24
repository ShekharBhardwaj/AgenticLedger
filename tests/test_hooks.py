"""Claude Code hooks become agent events: the half of the record the
proxy cannot see, joined to the proxied calls on the session id. The
endpoint never fails the agent, retention bounds the events, and the
ingest key gates them like telemetry."""

import time

import httpx2 as httpx

from agenticledger.proxy.hooks_ingest import event_from_hook

from .conftest import anthropic_response

SESSION = "11111111-2222-4333-8444-555555555555"


def _hook(event: str, **fields) -> dict:
    return {"session_id": SESSION, "transcript_path": "/tmp/x.jsonl", "cwd": "/work",
            "permission_mode": "default", "hook_event_name": event, **fields}


def test_hooks_are_recorded_and_read_back_in_order(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))
    posts = [
        _hook("SessionStart", source="startup", model="claude-sonnet-5"),
        _hook("UserPromptSubmit", prompt="fix the failing test", prompt_id="p1"),
        _hook("PostToolUse", tool_name="Bash", tool_use_id="toolu_1",
              tool_input={"command": "python -m unittest -q"},
              tool_response={"stdout": "FAILED (failures=2)", "exit_code": 1}, duration_ms=842),
        _hook("PermissionDenied", tool_name="Bash", tool_use_id="toolu_2",
              tool_input={"command": "rm -rf /"}, reason="[Irreversible Local Destruction]"),
        _hook("PostToolUseFailure", tool_name="Edit", tool_use_id="toolu_3",
              tool_input={"file_path": "a.py"}, error="Exit code 1\nno such file", duration_ms=12),
        _hook("SubagentStart", agent_id="ag-9", agent_type="Explore"),
        _hook("SubagentStop", agent_id="ag-9", agent_type="Explore", last_assistant_message="done"),
        _hook("PreCompact", trigger="auto"),
        _hook("Stop", last_assistant_message="All tests pass."),
        _hook("SessionEnd", reason="prompt_input_exit"),
        _hook("PreToolUse", tool_name="Read", tool_input={"file_path": "a.py"}),   # not recorded
    ]
    for body in posts:
        resp = client.post("/v1/hooks/claude-code", json=body)
        assert resp.status_code == 200 and resp.content == b""
    events = client.get(f"/api/sessions/{SESSION}/events").json()
    assert [e["event"] for e in events] == [
        "SessionStart", "UserPromptSubmit", "PostToolUse", "PermissionDenied", "PostToolUseFailure",
        "SubagentStart", "SubagentStop", "PreCompact", "Stop", "SessionEnd"]
    bash = events[2]
    assert bash["tool_name"] == "Bash" and bash["outcome"] == "ok" and bash["duration_ms"] == 842
    assert "unittest" in bash["tool_input"] and "failures=2" in bash["tool_response"]
    denied = events[3]
    assert denied["outcome"] == "denied" and "Irreversible" in denied["reason"]
    failed = events[4]
    assert failed["outcome"] == "failed" and failed["reason"].startswith("Exit code 1")
    assert events[5]["agent_id"] == "ag-9" and events[6]["detail"]["last_assistant_message"] == "done"
    assert events[1]["detail"]["prompt"] == "fix the failing test"
    assert events[9]["detail"]["reason"] == "prompt_input_exit"
    assert all(e["source"] == "hook" for e in events)


def test_bad_or_irrelevant_posts_never_fail_the_agent(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))
    assert client.post("/v1/hooks/claude-code", content=b"not json",
                       headers={"content-type": "application/json"}).status_code == 200
    assert client.post("/v1/hooks/claude-code", json={"hook_event_name": "PostToolUse"}).status_code == 200
    assert client.post("/v1/hooks/claude-code", json=[1, 2]).status_code == 200
    assert client.get(f"/api/sessions/{SESSION}/events").json() == []


def test_long_tool_output_is_cut_and_says_so(proxy):
    from agenticledger.proxy.hooks_ingest import CONTENT_CAP
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))
    big = "x" * (CONTENT_CAP + 500)
    client.post("/v1/hooks/claude-code", json=_hook("PostToolUse", tool_name="Read",
                                                     tool_input={"file_path": "big.txt"}, tool_response=big))
    ev = client.get(f"/api/sessions/{SESSION}/events").json()[0]
    assert len(ev["tool_response"]) < CONTENT_CAP + 100 and "cut by the ledger" in ev["tool_response"]


def test_events_meet_the_proxied_calls_on_the_session_id(proxy):
    """Claude Code's session UUID rides metadata.user_id on the model call
    and session_id on the hook: one session row holds both."""
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))
    body = {"model": "claude-sonnet-5", "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
            "metadata": {"user_id": '{"device_id":"d","account_uuid":"a","session_id":"%s"}' % SESSION}}
    assert client.post("/v1/messages", json=body,
                       headers={"user-agent": "claude-cli/2.1.220 (external, cli)"}).status_code == 200
    client.post("/v1/hooks/claude-code", json=_hook("PostToolUse", tool_name="Bash",
                                                     tool_input={"command": "ls"}, tool_response="a.py"))
    assert len(client.get(f"/session/{SESSION}").json()) == 1
    assert len(client.get(f"/api/sessions/{SESSION}/events").json()) == 1


def test_ingest_key_gates_hooks_like_telemetry(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "shh")
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))
    assert client.post("/v1/hooks/claude-code", json=_hook("Stop")).status_code == 401
    assert client.post("/v1/hooks/claude-code", json=_hook("Stop"),
                       headers={"x-agenticledger-ingest-key": "wrong"}).status_code == 403
    assert client.post("/v1/hooks/claude-code", json=_hook("Stop"),
                       headers={"x-agenticledger-ingest-key": "shh"}).status_code == 200


def test_retention_and_deletion_cover_agent_events(proxy):
    """The retention worker (0-day window, fast tick) purges agent events
    the way it purges calls, and deleting a session takes its events too."""
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   retention_days=0, retention_interval_seconds=0.05)
    client.post("/v1/hooks/claude-code", json=_hook("Stop"))
    client.post("/v1/hooks/claude-code", json=dict(_hook("Stop"), session_id="other-session"))
    deadline = time.time() + 3.0
    while time.time() < deadline:
        mine = client.get(f"/api/sessions/{SESSION}/events").json()
        other = client.get("/api/sessions/other-session/events").json()
        if mine == [] and other == []:
            break
        time.sleep(0.05)
    assert mine == [] and other == [], "retention worker did not purge the agent events"


def test_deleting_a_session_takes_its_agent_events(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()))
    client.post("/v1/hooks/claude-code", json=_hook("Stop"))
    client.post("/v1/messages", json={"model": "claude-sonnet-5", "max_tokens": 8,
                                      "messages": [{"role": "user", "content": "hi"}]},
                headers={"x-agenticledger-session-id": SESSION})
    assert len(client.get(f"/api/sessions/{SESSION}/events").json()) == 1
    assert client.delete(f"/api/sessions/{SESSION}", headers={"sec-fetch-site": "same-origin"}).status_code == 200
    assert client.get(f"/api/sessions/{SESSION}/events").json() == []


async def test_hook_only_session_lists_beside_proxied_ones(tmp_path):
    """Behind a managed gateway the proxy sees no call, only the hooks.
    The session still appears in the list, with its event count, and
    sorts by its last event."""
    from agenticledger.proxy.store import Store
    store = await Store.connect(f"sqlite:///{tmp_path / 'h.db'}")
    try:
        now = time.time()
        ev = [event_from_hook({"hook_event_name": "PostToolUse", "session_id": "gw-1",
                               "tool_name": "Bash", "tool_input": {"command": "ls"},
                               "tool_response": "a"}, now - 10),
              event_from_hook({"hook_event_name": "Stop", "session_id": "gw-1"}, now - 5)]
        await store.save_agent_events(ev)
        rows = await store.list_event_only_sessions()
        assert [r["session_id"] for r in rows] == ["gw-1"]
        assert rows[0]["call_count"] == 0 and rows[0]["event_count"] == 2
        assert rows[0]["agent_name"] == "claude-code"
        assert rows[0]["last_call_at"].startswith("20")  # ISO, like the proxied rows
    finally:
        await store.close()
