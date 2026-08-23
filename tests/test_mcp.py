"""Tests for agenticledger/proxy/mcp.py — the MCP JSON-RPC tool server.

Exercised end-to-end through the ``proxy`` fixture by POSTing JSON-RPC envelopes
to ``/mcp``. ``handle_mcp`` reads ``app.state.store``, so for the data-bearing
tool calls we first push a real LLM call through the proxy
(``POST /v1/chat/completions`` with a session header) to populate the store,
then assert the tools surface that captured record.

Each tool returns either a JSON-RPC ``result`` (with MCP ``content`` blocks) or a
JSON-RPC ``error`` with the spec-defined code:
    -32700 parse error, -32601 method/tool not found, -32602 invalid params.
"""

import json

import httpx
import pytest

from .conftest import openai_response

# ── helpers ───────────────────────────────────────────────────────────────────

def _rpc(client, method, params=None, id_=1):
    """POST a JSON-RPC 2.0 request to /mcp and return (status_code, parsed_body)."""
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    resp = client.post("/mcp", json=body)
    return resp.status_code, resp.json()


def _call_tool(client, name, arguments=None, id_=1):
    """Invoke a tool via tools/call; return (status_code, parsed_body)."""
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return _rpc(client, "tools/call", params, id_=id_)


def _text(result):
    """Extract the single text block out of an MCP content result."""
    blocks = result["content"]
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    return blocks[0]["text"]


def _capture(client, *, content="captured output", session="s-1", agent=None, model="gpt-4o"):
    """Push one real LLM call through the proxy so the store has a record.

    Returns the x-agenticledger-action-id of the captured call.
    """
    client.upstream.set(lambda r: httpx.Response(200, json=openai_response(content=content, model=model)))
    headers = {"x-agenticledger-session-id": session}
    if agent:
        headers["x-agenticledger-agent-name"] = agent
    resp = client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hello world"}]},
        headers=headers,
    )
    assert resp.status_code == 200
    return resp.headers["x-agenticledger-action-id"]


# ── lifecycle / protocol methods ──────────────────────────────────────────────

def test_initialize_returns_protocol_and_server_info(proxy):
    """initialize -> protocolVersion 2024-11-05 and serverInfo.name 'agenticledger'."""
    client = proxy()
    status, body = _rpc(client, "initialize")
    assert status == 200
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    result = body["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"]["name"] == "agenticledger"
    assert "version" in result["serverInfo"]
    assert "tools" in result["capabilities"]


def test_tools_list_returns_exactly_the_six_tools(proxy):
    """tools/list -> exactly the 6 tools, including the loop-run pair."""
    client = proxy()
    status, body = _rpc(client, "tools/list")
    assert status == 200
    tools = body["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"list_sessions", "explain", "get_session", "search",
                     "list_runs", "get_run_status"}
    assert len(tools) == 6
    # Each tool advertises a JSON-schema for its inputs.
    for t in tools:
        assert t["inputSchema"]["type"] == "object"
        assert "description" in t


def test_notifications_initialized_returns_empty_object(proxy):
    """notifications/initialized -> bare empty object (it is a notification)."""
    client = proxy()
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert resp.status_code == 200
    assert resp.json() == {}


# ── error envelopes ───────────────────────────────────────────────────────────

def test_unknown_method_is_method_not_found(proxy):
    """An unrecognised method -> JSON-RPC error code -32601."""
    client = proxy()
    status, body = _rpc(client, "does/not/exist", id_=7)
    assert status == 200
    assert body["id"] == 7
    assert body["error"]["code"] == -32601
    assert "error" in body and "result" not in body


def test_invalid_json_body_is_parse_error_http_400(proxy):
    """A body that is not valid JSON -> error code -32700 and HTTP 400."""
    client = proxy()
    resp = client.post(
        "/mcp",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == -32700
    assert body["id"] is None


# ── tools/call: list_sessions ─────────────────────────────────────────────────

def test_list_sessions_surfaces_captured_session(proxy):
    """tools/call list_sessions returns text content listing the captured session."""
    client = proxy()
    _capture(client, session="s-list", agent="Planner")
    status, body = _call_tool(client, "list_sessions")
    assert status == 200
    text = _text(body["result"])
    sessions = json.loads(text)
    assert any(s["session_id"] == "s-list" for s in sessions)
    row = next(s for s in sessions if s["session_id"] == "s-list")
    assert row["call_count"] == 1
    assert row["agent_name"] == "Planner"


def test_list_sessions_empty_store_returns_empty_list(proxy):
    """With no captured calls, list_sessions returns an empty JSON array."""
    client = proxy()
    status, body = _call_tool(client, "list_sessions")
    assert status == 200
    assert json.loads(_text(body["result"])) == []


# ── tools/call: explain ───────────────────────────────────────────────────────

def test_explain_returns_full_record(proxy):
    """explain(action_id) returns the captured trace record for that call."""
    client = proxy()
    action_id = _capture(client, content="the answer is 42", session="s-explain", model="gpt-4o")
    status, body = _call_tool(client, "explain", {"action_id": action_id})
    assert status == 200
    record = json.loads(_text(body["result"]))
    assert record["action_id"] == action_id
    assert record["model_id"] == "gpt-4o"
    assert record["content"] == "the answer is 42"


def test_explain_missing_action_id_is_invalid_params(proxy):
    """explain with no action_id argument -> error -32602."""
    client = proxy()
    status, body = _call_tool(client, "explain", {})
    assert status == 200
    assert body["error"]["code"] == -32602


def test_explain_empty_action_id_is_invalid_params(proxy):
    """explain with a blank/whitespace action_id -> error -32602."""
    client = proxy()
    status, body = _call_tool(client, "explain", {"action_id": "   "})
    assert status == 200
    assert body["error"]["code"] == -32602


def test_explain_unknown_action_id_is_invalid_params(proxy):
    """explain with a syntactically-fine but unknown action_id -> error -32602."""
    client = proxy()
    status, body = _call_tool(client, "explain", {"action_id": "no-such-action"})
    assert status == 200
    assert body["error"]["code"] == -32602


# ── tools/call: get_session ───────────────────────────────────────────────────

def test_get_session_returns_ordered_records(proxy):
    """get_session(session_id) returns the calls in time order — compact
    summaries by default, full records on include_messages=true."""
    client = proxy()
    a1 = _capture(client, content="first", session="s-chain")
    a2 = _capture(client, content="second", session="s-chain")
    status, body = _call_tool(client, "get_session", {"session_id": "s-chain"})
    assert status == 200
    records = json.loads(_text(body["result"]))
    assert [r["action_id"] for r in records] == [a1, a2]
    assert [r["index"] for r in records] == [1, 2]
    assert [r["content_preview"] for r in records] == ["first", "second"]
    assert "messages" not in records[0]                     # summaries by default

    status, body = _call_tool(client, "get_session",
                              {"session_id": "s-chain", "include_messages": True})
    full = json.loads(_text(body["result"]))
    assert [r["content"] for r in full] == ["first", "second"]
    assert full[0]["messages"]                              # opt-in firehose


def test_get_session_missing_id_is_invalid_params(proxy):
    """get_session with a blank session_id -> error -32602."""
    client = proxy()
    status, body = _call_tool(client, "get_session", {"session_id": ""})
    assert status == 200
    assert body["error"]["code"] == -32602


def test_get_session_unknown_id_is_invalid_params(proxy):
    """get_session for a session with no records -> error -32602."""
    client = proxy()
    status, body = _call_tool(client, "get_session", {"session_id": "nope"})
    assert status == 200
    assert body["error"]["code"] == -32602


# ── tools/call: search ────────────────────────────────────────────────────────

def test_search_returns_matches(proxy):
    """search(query) returns records whose captured text matches the query."""
    client = proxy()
    action_id = _capture(client, content="quantum entanglement notes", session="s-search")
    status, body = _call_tool(client, "search", {"query": "entanglement"})
    assert status == 200
    results = json.loads(_text(body["result"]))
    assert any(r["action_id"] == action_id for r in results)


def test_search_matches_on_prompt_messages(proxy):
    """search matches against the request prompt, not only the response content."""
    client = proxy()
    # The prompt sent by _capture contains "hello world".
    _capture(client, content="irrelevant", session="s-search2")
    status, body = _call_tool(client, "search", {"query": "hello world"})
    assert status == 200
    results = json.loads(_text(body["result"]))
    assert len(results) >= 1


def test_search_no_match_returns_friendly_text(proxy):
    """A query with no hits returns a 'No results' text block, not an error."""
    client = proxy()
    _capture(client, content="something", session="s-search3")
    status, body = _call_tool(client, "search", {"query": "zzz-definitely-absent-zzz"})
    assert status == 200
    text = _text(body["result"])
    assert "No results" in text
    assert "error" not in body


def test_search_missing_query_is_invalid_params(proxy):
    """search with a blank query -> error -32602."""
    client = proxy()
    status, body = _call_tool(client, "search", {"query": "  "})
    assert status == 200
    assert body["error"]["code"] == -32602


# ── tools/call: unknown tool ──────────────────────────────────────────────────

def test_unknown_tool_name_is_method_not_found(proxy):
    """tools/call with an unrecognised tool name -> error -32601."""
    client = proxy()
    status, body = _call_tool(client, "frobnicate", {})
    assert status == 200
    assert body["error"]["code"] == -32601


# ── limit clamping ────────────────────────────────────────────────────────────

class _LimitSpyStore:
    """Wraps a real Store and records the limit passed to clamped methods."""

    def __init__(self, inner):
        self._inner = inner
        self.list_sessions_limit = None
        self.search_limit = None

    async def list_sessions(self, limit=50):
        self.list_sessions_limit = limit
        return await self._inner.list_sessions(limit=limit)

    async def search(self, query, limit=50):
        self.search_limit = limit
        return await self._inner.search(query, limit=limit)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.mark.parametrize(
    "requested, expected",
    [(0, 1), (-5, 1), (1, 1), (50, 50), (100, 100), (1000, 100)],
)
def test_list_sessions_clamps_limit(proxy, requested, expected):
    """list_sessions clamps the requested limit into [1, 100] before querying."""
    client = proxy()
    spy = _LimitSpyStore(client.app.state.store)
    client.app.state.store = spy
    status, body = _call_tool(client, "list_sessions", {"limit": requested})
    assert status == 200
    assert spy.list_sessions_limit == expected


@pytest.mark.parametrize(
    "requested, expected",
    [(0, 1), (-5, 1), (1, 1), (100, 100), (1000, 100)],
)
def test_search_clamps_limit(proxy, requested, expected):
    """search clamps the requested limit into [1, 100] before querying."""
    client = proxy()
    spy = _LimitSpyStore(client.app.state.store)
    client.app.state.store = spy
    status, body = _call_tool(client, "search", {"query": "x", "limit": requested})
    assert status == 200
    assert spy.search_limit == expected


def test_mcp_run_tools(proxy):
    """list_runs and get_run_status expose loop runs with derived status."""
    import httpx as _httpx

    from .conftest import openai_response as _openai_response

    client = proxy(handler=lambda r: _httpx.Response(200, json=_openai_response()))
    for i in (1, 2):
        client.post("/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                    headers={"x-agenticledger-session-id": f"it-{i}",
                             "x-agenticledger-run-id": "mcp-run",
                             "x-agenticledger-iteration": str(i)})

    status, body = _rpc(client, "tools/call",
                        {"name": "list_runs", "arguments": {}})
    assert status == 200
    text = body["result"]["content"][0]["text"]
    assert "mcp-run" in text and '"status": "running"' in text

    status, body = _rpc(client, "tools/call",
                        {"name": "get_run_status", "arguments": {"run_id": "mcp-run"}})
    assert status == 200
    text = body["result"]["content"][0]["text"]
    assert '"iterations": 2' in text

    status, body = _rpc(client, "tools/call",
                        {"name": "get_run_status", "arguments": {"run_id": "nope"}})
    assert "error" in body


def test_get_session_defaults_to_summaries_not_megabytes(proxy):
    """A session tool that dumps full transcripts into a model's context is
    hostile — found live when a real MCP client met a 1.6MB session.
    Default rows carry everything ABOUT each call plus the sizes of what
    was withheld; include_messages=true opts into the firehose."""
    big = "x" * 50_000
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    for i in range(3):
        client.post("/v1/chat/completions",
                    json={"model": "gpt-4o",
                          "messages": [{"role": "user", "content": big + str(i)}]},
                    headers={"x-agenticledger-session-id": "fat-sess"})

    def call(args):
        resp = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "get_session", "arguments": args}})
        return resp.json()["result"]["content"][0]["text"]

    slim = call({"session_id": "fat-sess"})
    assert len(slim) < 20_000                      # summaries, not transcripts
    rows = json.loads(slim)
    assert rows[0]["index"] == 1
    assert rows[0]["withheld_bytes"]["messages"] > 50_000
    assert "content_preview" in rows[0] and "action_id" in rows[0]

    fat = call({"session_id": "fat-sess", "include_messages": True})
    assert len(fat) > 150_000                      # the opt-in firehose


def test_search_defaults_to_summaries_too(proxy):
    """Same disease as get_session, found by the same audit: three search
    hits once weighed 498KB. Hits are summaries unless asked otherwise."""
    big = "needle " + "x" * 40_000
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    for i in range(3):
        client.post("/v1/chat/completions",
                    json={"model": "gpt-4o",
                          "messages": [{"role": "user", "content": big + str(i)}]},
                    headers={"x-agenticledger-session-id": f"needle-{i}"})

    def call(args):
        resp = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "search", "arguments": args}})
        return resp.json()["result"]["content"][0]["text"]

    slim = call({"query": "needle"})
    assert len(slim) < 20_000
    rows = json.loads(slim)
    assert len(rows) == 3 and rows[0]["withheld_bytes"]["messages"] > 40_000

    fat = call({"query": "needle", "include_messages": True})
    assert len(fat) > 120_000


def test_mcp_status_agrees_with_the_api_on_a_revived_run(proxy):
    """The MCP surface once mirrored the status derivation and drifted,
    keeping the permanent-end-marker bug alive there. Both surfaces now
    share one derivation: a run that speaks after its end marker reads
    running on BOTH."""
    import httpx as _httpx

    from .conftest import openai_response as _openai_response

    client = proxy(handler=lambda r: _httpx.Response(200, json=_openai_response()))
    hdr = {"x-agenticledger-run-id": "phoenix-mcp", "x-agenticledger-session-id": "pm-1"}
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    client.post("/v1/chat/completions", json=body, headers=hdr)
    client.post("/api/runs/phoenix-mcp/end")
    client.post("/v1/chat/completions", json=body,
                headers={**hdr, "x-agenticledger-session-id": "pm-2"})

    assert client.get("/api/runs/phoenix-mcp").json()["status"] == "running"
    status, resp = _rpc(client, "tools/call",
                        {"name": "get_run_status", "arguments": {"run_id": "phoenix-mcp"}})
    assert status == 200
    assert '"status": "running"' in resp["result"]["content"][0]["text"]
