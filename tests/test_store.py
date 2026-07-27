"""Unit tests for agenticledger/proxy/store.py (SQLite backend).

Exercises the async ``Store`` via the in-memory ``store`` fixture from
conftest. CanonicalRequest / CanonicalResponse are built directly so the
tests focus purely on the storage layer (save / get / sessions / search /
cost rollups / delete), independent of the HTTP proxy.

Postgres is intentionally not tested (no server available).
"""

import time
import uuid

import pytest

from agenticledger.proxy.normalize import CanonicalRequest, CanonicalResponse

# ── builders ──────────────────────────────────────────────────────────────────

def make_req(
    *,
    messages=None,
    model_id="gpt-4o",
    provider="openai",
    timestamp=None,
    tools=None,
    system_prompt=None,
    temperature=None,
    max_tokens=None,
    tool_results=None,
) -> CanonicalRequest:
    """Build a CanonicalRequest with sensible defaults."""
    return CanonicalRequest(
        messages=messages if messages is not None else [{"role": "user", "content": "hi"}],
        model_id=model_id,
        provider=provider,
        timestamp=timestamp if timestamp is not None else time.time(),
        tools=tools,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        tool_results=tool_results,
    )


def make_resp(
    *,
    content="Hello.",
    tool_calls=None,
    stop_reason="stop",
    tokens_in=10,
    tokens_out=5,
    latency_ms=123.4,
    cost_usd=0.001,
) -> CanonicalResponse:
    """Build a CanonicalResponse with sensible defaults."""
    return CanonicalResponse(
        content=content,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )


def new_id() -> str:
    return str(uuid.uuid4())


async def save_call(store, *, action_id=None, req=None, resp=None, **kwargs):
    """Save a call and return its action_id (helper for terse tests)."""
    action_id = action_id or new_id()
    await store.save(
        action_id,
        req or make_req(),
        resp or make_resp(),
        **kwargs,
    )
    return action_id


# ── save / get round-trip ─────────────────────────────────────────────────────

async def test_save_get_roundtrip_scalar_fields(store):
    """save then get returns the stored scalar fields verbatim."""
    aid = new_id()
    req = make_req(
        model_id="gpt-4o-mini",
        provider="openai",
        system_prompt="be terse",
        temperature=0.7,
        max_tokens=256,
    )
    resp = make_resp(content="pong", stop_reason="stop", tokens_in=11, tokens_out=4, cost_usd=0.0025)
    await store.save(aid, req, resp, session_id="s1", agent_name="A", user_id="u1")

    row = await store.get(aid)
    assert row is not None
    assert row["action_id"] == aid
    assert row["session_id"] == "s1"
    assert row["model_id"] == "gpt-4o-mini"
    assert row["provider"] == "openai"
    assert row["content"] == "pong"
    assert row["stop_reason"] == "stop"
    assert row["tokens_in"] == 11
    assert row["tokens_out"] == 4
    assert row["cost_usd"] == 0.0025
    assert row["agent_name"] == "A"
    assert row["user_id"] == "u1"
    assert row["system_prompt"] == "be terse"
    assert row["temperature"] == 0.7
    assert row["max_tokens"] == 256


async def test_get_returns_parsed_json_fields(store):
    """messages/tools/tool_calls/tool_results come back as lists/dicts, not strings."""
    aid = new_id()
    messages = [{"role": "user", "content": "what's the weather?"}]
    tools = [{"type": "function", "function": {"name": "get_weather"}}]
    tool_results = [{"tool_call_id": "call_1", "content": "sunny"}]
    tool_calls = [{"id": "call_1", "name": "get_weather", "arguments": "{}"}]

    req = make_req(messages=messages, tools=tools, tool_results=tool_results)
    resp = make_resp(tool_calls=tool_calls)
    await store.save(aid, req, resp)

    row = await store.get(aid)
    assert row["messages"] == messages
    assert isinstance(row["messages"], list)
    assert row["tools"] == tools
    assert isinstance(row["tools"], list)
    assert row["tool_calls"] == tool_calls
    assert isinstance(row["tool_calls"], list)
    assert row["tool_results"] == tool_results
    assert isinstance(row["tool_results"], list)


async def test_get_timestamp_is_iso_string(store):
    """The stored unix timestamp is returned as an ISO-8601 UTC string."""
    import datetime

    ts = 1_700_000_000.0
    aid = await save_call(store, req=make_req(timestamp=ts))
    row = await store.get(aid)

    assert isinstance(row["timestamp"], str)
    # Parseable as ISO and round-trips to the same instant.
    parsed = datetime.datetime.fromisoformat(row["timestamp"])
    assert parsed.tzinfo is not None
    assert parsed.timestamp() == pytest.approx(ts)


async def test_get_unknown_returns_none(store):
    """get of an unknown action_id returns None."""
    assert await store.get(new_id()) is None


async def test_null_json_fields_stay_none(store):
    """Optional JSON fields left unset come back as None (not parsed)."""
    aid = await save_call(
        store,
        req=make_req(tools=None, tool_results=None),
        resp=make_resp(tool_calls=None),
    )
    row = await store.get(aid)
    assert row["tools"] is None
    assert row["tool_calls"] is None
    assert row["tool_results"] is None


# ── get_session ───────────────────────────────────────────────────────────────

async def test_get_session_orders_by_timestamp_asc(store):
    """get_session returns a session's calls ordered by timestamp ascending."""
    base = 1_700_000_000.0
    a_late = await save_call(store, req=make_req(timestamp=base + 30), session_id="sess")
    a_early = await save_call(store, req=make_req(timestamp=base + 10), session_id="sess")
    a_mid = await save_call(store, req=make_req(timestamp=base + 20), session_id="sess")

    rows = await store.get_session("sess")
    assert [r["action_id"] for r in rows] == [a_early, a_mid, a_late]


async def test_get_session_only_returns_matching_session(store):
    """get_session does not bleed in calls from other sessions."""
    await save_call(store, session_id="sess-a")
    await save_call(store, session_id="sess-b")
    rows = await store.get_session("sess-a")
    assert len(rows) == 1
    assert rows[0]["session_id"] == "sess-a"


async def test_get_session_unknown_returns_empty(store):
    """get_session for an unknown session returns an empty list."""
    assert await store.get_session("nope") == []


# ── list_sessions ─────────────────────────────────────────────────────────────

async def test_list_sessions_aggregates_per_session(store):
    """list_sessions rolls up call_count and sums tokens/cost/latency per session."""
    base = 1_700_000_000.0
    await save_call(
        store,
        req=make_req(timestamp=base + 1),
        resp=make_resp(tokens_in=10, tokens_out=5, latency_ms=100.0, cost_usd=0.01),
        session_id="s1",
    )
    await save_call(
        store,
        req=make_req(timestamp=base + 2),
        resp=make_resp(tokens_in=20, tokens_out=7, latency_ms=200.0, cost_usd=0.02),
        session_id="s1",
    )

    sessions = await store.list_sessions()
    s1 = next(s for s in sessions if s["session_id"] == "s1")
    assert s1["call_count"] == 2
    assert s1["total_tokens_in"] == 30
    assert s1["total_tokens_out"] == 12
    assert s1["total_latency_ms"] == 300  # 100 + 200, latency stored rounded
    assert s1["total_cost_usd"] == pytest.approx(0.03)


async def test_list_sessions_started_at_is_min_timestamp_iso(store):
    """started_at is the earliest timestamp in the session, as an ISO string."""
    import datetime

    base = 1_700_000_000.0
    await save_call(store, req=make_req(timestamp=base + 50), session_id="s1")
    await save_call(store, req=make_req(timestamp=base + 10), session_id="s1")

    sessions = await store.list_sessions()
    s1 = next(s for s in sessions if s["session_id"] == "s1")
    assert isinstance(s1["started_at"], str)
    assert datetime.datetime.fromisoformat(s1["started_at"]).timestamp() == pytest.approx(base + 10)


async def test_list_sessions_ordered_by_started_at_desc(store):
    """Sessions are ordered with the most recently started session first."""
    base = 1_700_000_000.0
    await save_call(store, req=make_req(timestamp=base + 10), session_id="old")
    await save_call(store, req=make_req(timestamp=base + 100), session_id="new")
    await save_call(store, req=make_req(timestamp=base + 50), session_id="mid")

    sessions = await store.list_sessions()
    order = [s["session_id"] for s in sessions]
    assert order == ["new", "mid", "old"]


async def test_list_sessions_respects_limit(store):
    """list_sessions honors the limit argument."""
    base = 1_700_000_000.0
    for i in range(5):
        await save_call(store, req=make_req(timestamp=base + i), session_id=f"s{i}")

    sessions = await store.list_sessions(limit=2)
    assert len(sessions) == 2


async def test_list_sessions_excludes_null_session(store):
    """Calls with no session_id are excluded from the session rollup."""
    await save_call(store, session_id=None)
    await save_call(store, session_id="real")

    sessions = await store.list_sessions()
    ids = [s["session_id"] for s in sessions]
    assert ids == ["real"]


# ── search ────────────────────────────────────────────────────────────────────

async def test_search_matches_message_substring(store):
    """search finds a substring inside the messages JSON."""
    await save_call(store, req=make_req(messages=[{"role": "user", "content": "tell me about photosynthesis"}]))
    await save_call(store, req=make_req(messages=[{"role": "user", "content": "unrelated"}]))

    hits = await store.search("photosynthesis")
    assert len(hits) == 1
    assert "photosynthesis" in hits[0]["messages"][0]["content"]


async def test_search_matches_response_content(store):
    """search matches against the response content column."""
    await save_call(store, resp=make_resp(content="the mitochondria is the powerhouse"))
    hits = await store.search("powerhouse")
    assert len(hits) == 1
    assert "powerhouse" in hits[0]["content"]


async def test_search_matches_system_prompt(store):
    """search matches against the stored system prompt."""
    await save_call(store, req=make_req(system_prompt="you are a pirate"))
    hits = await store.search("pirate")
    assert len(hits) == 1


async def test_search_matches_agent_name_and_user_id(store):
    """search matches against agent_name and user_id columns."""
    await save_call(store, agent_name="ResearchBot")
    await save_call(store, user_id="alice@example.com")

    assert len(await store.search("ResearchBot")) == 1
    assert len(await store.search("alice@example.com")) == 1


async def test_search_is_case_insensitive(store):
    """SQLite LIKE is ASCII case-insensitive; search matches regardless of case."""
    await save_call(store, req=make_req(messages=[{"role": "user", "content": "Photosynthesis"}]))
    hits = await store.search("photosynthesis")
    assert len(hits) == 1


async def test_search_no_match_returns_empty(store):
    """search with no matches returns an empty list."""
    await save_call(store, req=make_req(messages=[{"role": "user", "content": "hello"}]))
    assert await store.search("zzz-nonexistent-zzz") == []


async def test_search_respects_limit(store):
    """search honors the limit argument."""
    for _ in range(4):
        await save_call(store, req=make_req(messages=[{"role": "user", "content": "needle"}]))
    hits = await store.search("needle", limit=2)
    assert len(hits) == 2


# ── cost rollups ──────────────────────────────────────────────────────────────

async def test_get_session_cost_sums_successful_calls(store):
    """get_session_cost sums cost_usd across the session's 200-status calls."""
    await save_call(store, resp=make_resp(cost_usd=0.01), session_id="s1", status_code=200)
    await save_call(store, resp=make_resp(cost_usd=0.02), session_id="s1", status_code=200)
    assert await store.get_session_cost("s1") == pytest.approx(0.03)


async def test_get_session_cost_ignores_non_200(store):
    """get_session_cost excludes rows whose status_code is not 200."""
    await save_call(store, resp=make_resp(cost_usd=0.01), session_id="s1", status_code=200)
    await save_call(store, resp=make_resp(cost_usd=0.50), session_id="s1", status_code=500)
    assert await store.get_session_cost("s1") == pytest.approx(0.01)


async def test_get_session_cost_unknown_is_zero(store):
    """get_session_cost for an unknown session is 0.0."""
    assert await store.get_session_cost("nobody") == 0.0


async def test_get_agent_cost_sums_since_cutoff(store):
    """get_agent_cost sums only the agent's successful calls at/after since_ts."""
    base = 1_700_000_000.0
    # before cutoff — excluded
    await save_call(store, req=make_req(timestamp=base - 100), resp=make_resp(cost_usd=0.05),
                    agent_name="bot", status_code=200)
    # after cutoff — included
    await save_call(store, req=make_req(timestamp=base + 100), resp=make_resp(cost_usd=0.01),
                    agent_name="bot", status_code=200)
    await save_call(store, req=make_req(timestamp=base + 200), resp=make_resp(cost_usd=0.02),
                    agent_name="bot", status_code=200)

    assert await store.get_agent_cost("bot", base) == pytest.approx(0.03)


async def test_get_agent_cost_ignores_non_200(store):
    """get_agent_cost excludes non-200 rows even within the window."""
    base = 1_700_000_000.0
    await save_call(store, req=make_req(timestamp=base + 1), resp=make_resp(cost_usd=0.01),
                    agent_name="bot", status_code=200)
    await save_call(store, req=make_req(timestamp=base + 2), resp=make_resp(cost_usd=0.99),
                    agent_name="bot", status_code=429)
    assert await store.get_agent_cost("bot", base) == pytest.approx(0.01)


async def test_get_agent_cost_scoped_to_agent(store):
    """get_agent_cost only counts the named agent."""
    base = 1_700_000_000.0
    await save_call(store, req=make_req(timestamp=base + 1), resp=make_resp(cost_usd=0.01),
                    agent_name="alpha", status_code=200)
    await save_call(store, req=make_req(timestamp=base + 1), resp=make_resp(cost_usd=0.50),
                    agent_name="beta", status_code=200)
    assert await store.get_agent_cost("alpha", base) == pytest.approx(0.01)


async def test_get_period_cost_sums_since_cutoff(store):
    """get_period_cost sums all successful calls at/after since_ts regardless of agent/session."""
    base = 1_700_000_000.0
    await save_call(store, req=make_req(timestamp=base - 1), resp=make_resp(cost_usd=0.05))  # excluded
    await save_call(store, req=make_req(timestamp=base + 1), resp=make_resp(cost_usd=0.01))
    await save_call(store, req=make_req(timestamp=base + 2), resp=make_resp(cost_usd=0.02))
    assert await store.get_period_cost(base) == pytest.approx(0.03)


async def test_get_period_cost_ignores_non_200(store):
    """get_period_cost excludes non-200 rows."""
    base = 1_700_000_000.0
    await save_call(store, req=make_req(timestamp=base + 1), resp=make_resp(cost_usd=0.01), status_code=200)
    await save_call(store, req=make_req(timestamp=base + 2), resp=make_resp(cost_usd=0.99), status_code=503)
    assert await store.get_period_cost(base) == pytest.approx(0.01)


# ── delete_session ────────────────────────────────────────────────────────────

async def test_delete_session_returns_rows_deleted(store):
    """delete_session returns the number of rows removed."""
    await save_call(store, session_id="doomed")
    await save_call(store, session_id="doomed")
    await save_call(store, session_id="doomed")

    deleted = await store.delete_session("doomed")
    assert deleted == 3


async def test_delete_session_removes_rows(store):
    """After delete_session, get_session returns an empty list."""
    aid = await save_call(store, session_id="doomed")
    await store.delete_session("doomed")

    assert await store.get_session("doomed") == []
    assert await store.get(aid) is None


async def test_delete_session_leaves_other_sessions(store):
    """delete_session only removes the targeted session's rows."""
    keep = await save_call(store, session_id="keep")
    await save_call(store, session_id="drop")

    deleted = await store.delete_session("drop")
    assert deleted == 1
    survivors = await store.get_session("keep")
    assert len(survivors) == 1
    assert survivors[0]["action_id"] == keep


async def test_delete_unknown_session_returns_zero(store):
    """Deleting an unknown session deletes nothing and returns 0."""
    assert await store.delete_session("ghost") == 0
