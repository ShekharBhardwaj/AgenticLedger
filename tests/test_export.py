"""Tests for agentledger/proxy/export.py.

Covers the two pure functions:
- ``build_export``: builds a structured compliance export with a SHA-256
  integrity hash, summed totals, sorted-unique model/agent lists, error and
  budget-warning counts, started/ended timestamps, and a call count.
- ``render_html_report``: renders the export as a printable HTML page; must
  HTML-escape all user-controlled content (XSS regression).

These are constructed from plain dict ``calls`` — no proxy/store needed.
"""

import hashlib
import json

import pytest

from agentledger.proxy.export import build_export, render_html_report

# ── helpers ───────────────────────────────────────────────────────────────────

def make_call(**overrides):
    """A representative captured-call dict with sensible defaults."""
    call = {
        "action_id": "act-1",
        "timestamp": "2026-06-19T10:00:00+00:00",
        "model_id": "gpt-4o",
        "agent_name": "Researcher",
        "user_id": "u-1",
        "environment": "prod",
        "stop_reason": "stop",
        "status_code": 200,
        "error_detail": None,
        "tokens_in": 10,
        "tokens_out": 5,
        "cost_usd": 0.001,
        "latency_ms": 250,
        "temperature": 0.7,
        "system_prompt": "You are helpful.",
        "messages": [{"role": "user", "content": "hello"}],
        "content": "hi there",
    }
    call.update(overrides)
    return call


def expected_integrity(calls):
    """Recompute the integrity hash the way the module documents it."""
    calls_json = json.dumps(calls, sort_keys=True, default=str)
    digest = hashlib.sha256(calls_json.encode()).hexdigest()
    return f"sha256:{digest}"


# ── build_export: integrity hash ──────────────────────────────────────────────

def test_integrity_is_sha256_of_sorted_json():
    """integrity == 'sha256:' + sha256 of json.dumps(calls, sort_keys, default=str)."""
    calls = [make_call(action_id="a"), make_call(action_id="b")]
    export = build_export("sess-1", calls)
    assert export["export"]["integrity"] == expected_integrity(calls)
    assert export["export"]["integrity"].startswith("sha256:")


def test_changing_a_call_changes_the_hash():
    """Mutating any call field produces a different integrity hash."""
    calls = [make_call(content="original")]
    h1 = build_export("sess-1", calls)["export"]["integrity"]

    mutated = [make_call(content="tampered")]
    h2 = build_export("sess-1", mutated)["export"]["integrity"]

    assert h1 != h2


def test_hash_independent_of_key_order():
    """sort_keys means key insertion order does not affect the hash."""
    call_a = {"action_id": "x", "timestamp": "t", "model_id": "m"}
    call_b = {"model_id": "m", "timestamp": "t", "action_id": "x"}
    h1 = build_export("s", [dict(call_a)])["export"]["integrity"]
    h2 = build_export("s", [dict(call_b)])["export"]["integrity"]
    assert h1 == h2


def test_hash_handles_non_json_serializable_via_default_str():
    """default=str lets non-JSON-native values (e.g. datetime) hash without crashing."""
    import datetime
    calls = [make_call(timestamp=datetime.datetime(2026, 6, 19, 10, 0, 0))]
    export = build_export("s", calls)  # must not raise
    assert export["export"]["integrity"] == expected_integrity(calls)


# ── build_export: totals ──────────────────────────────────────────────────────

def test_totals_are_summed():
    """cost/tokens/latency totals are the sum across all calls."""
    calls = [
        make_call(cost_usd=0.001, tokens_in=10, tokens_out=5, latency_ms=100),
        make_call(cost_usd=0.002, tokens_in=20, tokens_out=7, latency_ms=250),
    ]
    s = build_export("sess", calls)["session"]
    assert s["total_tokens_in"] == 30
    assert s["total_tokens_out"] == 12
    assert s["total_latency_ms"] == 350
    assert s["total_cost_usd"] == pytest.approx(0.003)


def test_totals_treat_missing_or_none_as_zero():
    """Missing/None numeric fields count as 0 and do not crash the sum."""
    calls = [
        make_call(cost_usd=None, tokens_in=None, tokens_out=None, latency_ms=None),
        {"model_id": "gpt-4o", "timestamp": "t"},  # nothing else present
        make_call(cost_usd=0.005, tokens_in=3, tokens_out=2, latency_ms=40),
    ]
    s = build_export("sess", calls)["session"]
    assert s["total_tokens_in"] == 3
    assert s["total_tokens_out"] == 2
    assert s["total_latency_ms"] == 40
    assert s["total_cost_usd"] == pytest.approx(0.005)


def test_total_cost_is_none_when_zero():
    """Total cost of exactly zero is reported as None, not 0."""
    calls = [make_call(cost_usd=0), make_call(cost_usd=None)]
    s = build_export("sess", calls)["session"]
    assert s["total_cost_usd"] is None


def test_total_latency_is_rounded():
    """Total latency is rounded to an integer."""
    calls = [make_call(latency_ms=100.4), make_call(latency_ms=100.4)]
    s = build_export("sess", calls)["session"]
    assert s["total_latency_ms"] == 201  # 200.8 rounds to 201


# ── build_export: models & agents ─────────────────────────────────────────────

def test_models_are_sorted_unique():
    """models is a sorted list of distinct model ids."""
    calls = [
        make_call(model_id="gpt-4o"),
        make_call(model_id="claude-3-5-sonnet"),
        make_call(model_id="gpt-4o"),  # duplicate
    ]
    s = build_export("sess", calls)["session"]
    assert s["models"] == ["claude-3-5-sonnet", "gpt-4o"]


def test_agents_are_sorted_unique():
    """agents is a sorted list of distinct agent names."""
    calls = [
        make_call(agent_name="Zeta"),
        make_call(agent_name="Alpha"),
        make_call(agent_name="Alpha"),  # duplicate
    ]
    s = build_export("sess", calls)["session"]
    assert s["agents"] == ["Alpha", "Zeta"]


def test_models_and_agents_skip_missing():
    """Calls with no model_id/agent_name are excluded from the lists."""
    calls = [
        make_call(model_id=None, agent_name=None),
        make_call(model_id="gpt-4o", agent_name="Bot"),
    ]
    s = build_export("sess", calls)["session"]
    assert s["models"] == ["gpt-4o"]
    assert s["agents"] == ["Bot"]


# ── build_export: error & warning counts ──────────────────────────────────────

def test_error_count_counts_non_200():
    """error_count counts calls whose status_code != 200."""
    calls = [
        make_call(status_code=200),
        make_call(status_code=500),
        make_call(status_code=429),
        make_call(status_code=200),
    ]
    s = build_export("sess", calls)["session"]
    assert s["error_count"] == 2


def test_missing_status_code_is_treated_as_200():
    """A call with no status_code is not counted as an error (defaults to 200)."""
    calls = [{"model_id": "m", "timestamp": "t"}]  # no status_code
    s = build_export("sess", calls)["session"]
    assert s["error_count"] == 0


def test_warning_count_counts_budget_warning_prefix():
    """warning_count counts error_detail values starting with 'budget_warning:'."""
    calls = [
        make_call(error_detail="budget_warning: approaching cap"),
        make_call(error_detail="budget_warning:another"),
        make_call(error_detail="some other error"),
        make_call(error_detail=None),
    ]
    s = build_export("sess", calls)["session"]
    assert s["warning_count"] == 2


# ── build_export: timestamps, call_count, session id ──────────────────────────

def test_started_and_ended_from_first_and_last_call():
    """started_at/ended_at come from the first/last call's timestamp."""
    calls = [
        make_call(timestamp="2026-06-19T10:00:00+00:00"),
        make_call(timestamp="2026-06-19T10:05:00+00:00"),
        make_call(timestamp="2026-06-19T10:10:00+00:00"),
    ]
    s = build_export("sess", calls)["session"]
    assert s["started_at"] == "2026-06-19T10:00:00+00:00"
    assert s["ended_at"] == "2026-06-19T10:10:00+00:00"


def test_call_count_and_session_id():
    """call_count equals len(calls) and session_id is echoed back."""
    calls = [make_call(), make_call(), make_call()]
    s = build_export("my-session", calls)["session"]
    assert s["call_count"] == 3
    assert s["session_id"] == "my-session"


def test_export_metadata_present():
    """The export block carries generator name and an integrity field."""
    export = build_export("s", [make_call()])
    assert export["export"]["generator"] == "Agentic Ledger"
    assert "generated_at" in export["export"]
    assert export["calls"] == [make_call()] or len(export["calls"]) == 1


# ── build_export: empty calls ─────────────────────────────────────────────────

def test_empty_calls_produces_sensible_zeros_and_none():
    """An empty session exports zeros/None without crashing."""
    export = build_export("empty-sess", [])
    s = export["session"]
    assert s["session_id"] == "empty-sess"
    assert s["started_at"] is None
    assert s["ended_at"] is None
    assert s["call_count"] == 0
    assert s["error_count"] == 0
    assert s["warning_count"] == 0
    assert s["models"] == []
    assert s["agents"] == []
    assert s["total_tokens_in"] == 0
    assert s["total_tokens_out"] == 0
    assert s["total_latency_ms"] == 0
    assert s["total_cost_usd"] is None
    assert export["calls"] == []
    # Integrity hash of an empty list is still well-defined.
    assert export["export"]["integrity"] == expected_integrity([])


# ── render_html_report: basic content ─────────────────────────────────────────

def test_report_contains_session_id_and_models():
    """The rendered HTML includes the session id and each call's model."""
    calls = [make_call(model_id="gpt-4o"), make_call(model_id="claude-3-5-sonnet")]
    export = build_export("session-xyz", calls)
    html_out = render_html_report(export)
    assert "session-xyz" in html_out
    assert "gpt-4o" in html_out
    assert "claude-3-5-sonnet" in html_out
    assert html_out.lstrip().startswith("<!DOCTYPE html>")


def test_report_renders_empty_session():
    """An export with no calls still renders valid HTML mentioning the session."""
    export = build_export("empty", [])
    html_out = render_html_report(export)
    assert "empty" in html_out
    assert "<!DOCTYPE html>" in html_out


# ── render_html_report: XSS escaping regression ───────────────────────────────

XSS = "<script>alert(1)</script>"
ESCAPED = "&lt;script&gt;"


def test_content_is_html_escaped():
    """Malicious content is HTML-escaped, never emitted raw."""
    calls = [make_call(content=XSS)]
    html_out = render_html_report(build_export("s", calls))
    assert ESCAPED in html_out
    assert XSS not in html_out


def test_system_prompt_is_html_escaped():
    """Malicious system_prompt is HTML-escaped, never emitted raw."""
    calls = [make_call(system_prompt=XSS)]
    html_out = render_html_report(build_export("s", calls))
    assert ESCAPED in html_out
    assert XSS not in html_out


def test_agent_name_is_html_escaped():
    """Malicious agent_name is HTML-escaped, never emitted raw."""
    calls = [make_call(agent_name=XSS)]
    html_out = render_html_report(build_export("s", calls))
    assert ESCAPED in html_out
    assert XSS not in html_out


def test_session_id_is_html_escaped():
    """Malicious session id is HTML-escaped in the report (title + summary)."""
    html_out = render_html_report(build_export(XSS, [make_call()]))
    assert ESCAPED in html_out
    assert XSS not in html_out


def test_user_message_input_is_html_escaped():
    """Malicious last-user-message content is HTML-escaped, never emitted raw."""
    calls = [make_call(messages=[{"role": "user", "content": XSS}])]
    html_out = render_html_report(build_export("s", calls))
    assert ESCAPED in html_out
    assert XSS not in html_out


# ── render_html_report: rich call shapes render without error ─────────────────

def test_renders_with_tool_calls_tool_results_and_handoffs():
    """tool_calls, tool_results, and handoff_from/handoff_to all render cleanly."""
    calls = [
        make_call(
            tool_calls=[{"name": "get_weather", "arguments": '{"city":"SF"}'}],
            tool_results=[{"tool_call_id": "call_1", "content": "sunny"}],
            handoff_from="Planner",
            handoff_to="Executor",
        )
    ]
    html_out = render_html_report(build_export("s", calls))
    assert "get_weather" in html_out
    assert "sunny" in html_out
    assert "Planner" in html_out
    assert "Executor" in html_out
    assert "Handoff" in html_out


def test_tool_call_payloads_are_html_escaped():
    """Malicious tool-call name/arguments and tool-result content are escaped."""
    calls = [
        make_call(
            tool_calls=[{"name": XSS, "arguments": XSS}],
            tool_results=[{"tool_use_id": "x", "content": XSS}],
        )
    ]
    html_out = render_html_report(build_export("s", calls))
    assert ESCAPED in html_out
    assert XSS not in html_out


def test_renders_error_and_warning_calls():
    """Error and budget-warning calls render their status/detail sections."""
    calls = [
        make_call(status_code=500, error_detail="upstream exploded"),
        make_call(status_code=200, error_detail="budget_warning: 80% of cap used"),
    ]
    html_out = render_html_report(build_export("s", calls))
    assert "upstream exploded" in html_out
    assert "budget" in html_out.lower()
    assert "80% of cap used" in html_out
