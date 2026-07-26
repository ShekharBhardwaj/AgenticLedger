"""Unit tests for agentledger/proxy/loops.py — loop & run inference.

LoopTracker stitches raw calls into ReAct threads (message-chain prefix
matching), groups fresh-context sessions into Ralph runs (shared system-prompt
hash within a time gap), counts user turns, and raises stuck-loop flags.
"""

import json

from agentledger.proxy.loops import LoopTracker
from agentledger.proxy.normalize import CanonicalRequest, CanonicalResponse


def _req(messages, system_prompt=None, ts=0.0):
    return CanonicalRequest(
        messages=messages, model_id="gpt-4o", provider="openai",
        timestamp=ts, system_prompt=system_prompt,
    )


def _resp(tool_calls=None):
    return CanonicalResponse(
        content="ok", tool_calls=tool_calls, stop_reason="stop",
        tokens_in=1, tokens_out=1, latency_ms=1.0,
    )


def _meta(session="s1", **kw):
    return {"session_id": session, "run_id": None, "iteration": None, **kw}


U1 = {"role": "user", "content": "find the bug"}
A1 = {"role": "assistant", "content": None,
      "tool_calls": [{"id": "c1", "function": {"name": "grep", "arguments": "{\"q\":1}"}}]}
T1 = {"role": "tool", "tool_call_id": "c1", "content": "match at line 3"}
A2 = {"role": "assistant", "content": None,
      "tool_calls": [{"id": "c2", "function": {"name": "read", "arguments": "{\"f\":2}"}}]}
T2 = {"role": "tool", "tool_call_id": "c2", "content": "file contents"}


def test_react_chain_stitched_into_one_thread():
    tracker = LoopTracker()
    f1 = tracker.annotate("a1", _req([U1]), _resp([{"name": "grep", "arguments": "{}"}]), _meta())
    f2 = tracker.annotate("a2", _req([U1, A1, T1]), _resp([{"name": "read", "arguments": "{}"}]), _meta())
    f3 = tracker.annotate("a3", _req([U1, A1, T1, A2, T2]), _resp(), _meta())

    assert f1["thread_id"] == f2["thread_id"] == f3["thread_id"]
    assert (f1["step_index"], f2["step_index"], f3["step_index"]) == (1, 2, 3)
    assert f1["prev_action_id"] is None
    assert f2["prev_action_id"] == "a1"
    assert f3["prev_action_id"] == "a2"


def test_diverging_history_starts_a_new_thread():
    tracker = LoopTracker()
    f1 = tracker.annotate("a1", _req([U1]), _resp(), _meta())
    other = {"role": "user", "content": "different conversation"}
    f2 = tracker.annotate("a2", _req([other]), _resp(), _meta())
    assert f1["thread_id"] != f2["thread_id"]
    assert f2["step_index"] == 1


def test_turn_index_counts_real_user_turns_only():
    tracker = LoopTracker()
    tool_result_msg = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "out"},
    ]}
    f = tracker.annotate(
        "a1",
        _req([U1, {"role": "assistant", "content": "x"}, tool_result_msg,
              {"role": "user", "content": "follow-up"}]),
        _resp(), _meta(),
    )
    assert f["turn_index"] == 2  # U1 + follow-up; the tool_result carrier doesn't count


def test_repeat_tool_call_flag_and_block():
    tracker = LoopTracker(repeat_threshold=3)
    same = [{"name": "grep", "arguments": "{\"q\": \"bug\"}"}]
    msgs = [U1]
    flags = []
    for i in range(3):
        f = tracker.annotate(f"a{i}", _req(list(msgs)), _resp(list(same)), _meta())
        flags.append(f["loop_flags"])
        msgs = msgs + [dict(A1), dict(T1)] * 1  # extend so calls stay in-thread
        msgs[-1] = {**T1, "content": f"result {i}"}  # results differ; tool call doesn't

    assert flags[0] is None and flags[1] is None
    assert json.loads(flags[2]) == ["repeat_tool_call"]
    assert "same tool call" in tracker.check_block("s1")
    assert tracker.check_block("other-session") is None


def test_step_budget_flag():
    tracker = LoopTracker(max_steps=2)
    msgs = [U1]
    tracker.annotate("a1", _req(list(msgs)), _resp(), _meta())
    f2 = tracker.annotate("a2", _req(msgs + [A1, T1]), _resp(), _meta())
    assert "step_budget_exceeded" in (f2["loop_flags"] or "")
    assert "step budget" in tracker.check_block("s1")


def test_ralph_sessions_grouped_into_one_run():
    """Fresh-context spawns (new session, same system prompt) within the gap
    are grouped as iterations of one run."""
    now = [1000.0]
    tracker = LoopTracker(run_gap_seconds=600, clock=lambda: now[0])
    sys_prompt = "You are working from PROMPT.md. Complete one task."

    f1 = tracker.annotate("a1", _req([U1], system_prompt=sys_prompt), _resp(), _meta("sess-1"))
    now[0] += 120
    f2 = tracker.annotate("a2", _req([U1], system_prompt=sys_prompt), _resp(), _meta("sess-2"))
    now[0] += 120
    f3 = tracker.annotate("a3", _req([U1], system_prompt=sys_prompt), _resp(), _meta("sess-3"))

    assert f1["run_id"] == f2["run_id"] == f3["run_id"]
    assert f1["run_id"].startswith("auto-run-")
    assert (f1["iteration"], f2["iteration"], f3["iteration"]) == (1, 2, 3)


def test_ralph_grouping_expires_after_gap():
    now = [1000.0]
    tracker = LoopTracker(run_gap_seconds=600, clock=lambda: now[0])
    sys_prompt = "loop prompt"
    f1 = tracker.annotate("a1", _req([U1], system_prompt=sys_prompt), _resp(), _meta("sess-1"))
    now[0] += 3600  # beyond the gap — a new run begins
    f2 = tracker.annotate("a2", _req([U1], system_prompt=sys_prompt), _resp(), _meta("sess-2"))
    assert f1["run_id"] != f2["run_id"]
    assert f2["iteration"] == 1


def test_explicit_run_headers_override_inference():
    tracker = LoopTracker()
    f = tracker.annotate(
        "a1", _req([U1], system_prompt="x"), _resp(),
        _meta("s1", run_id="my-run", iteration=7),
    )
    assert f["run_id"] == "my-run"
    assert f["iteration"] == 7


def test_annotate_never_raises_on_garbage():
    tracker = LoopTracker()
    weird = _req([None, "raw", {"role": None, "content": {"a": object()}}])
    fields = tracker.annotate("a1", weird, _resp(), {"session_id": None})
    assert set(fields) == {
        "thread_id", "step_index", "turn_index", "prev_action_id",
        "run_id", "iteration", "loop_flags", "tool_executions",
    }


def test_completion_promise_flagged():
    tracker = LoopTracker(completion_promise=r"ALL TASKS COMPLETE")
    resp = CanonicalResponse(
        content="Everything passes. ALL TASKS COMPLETE", tool_calls=None,
        stop_reason="stop", tokens_in=1, tokens_out=1, latency_ms=1.0,
    )
    f = tracker.annotate("a1", _req([U1]), resp, _meta())
    assert "completion_promise" in f["loop_flags"]
    # The promise is a good outcome — it must never trip the circuit breaker.
    assert tracker.check_block("s1") is None


def test_invalid_promise_regex_disables_detection():
    tracker = LoopTracker(completion_promise="([unclosed")
    f = tracker.annotate("a1", _req([U1]), _resp(), _meta())
    assert f["loop_flags"] is None


def test_tool_executions_paired_across_calls():
    """A tool call issued by call N is resolved by the results in call N+1,
    with wall-clock latency derived from the gap between the calls."""
    tracker = LoopTracker()
    resp1 = CanonicalResponse(
        content=None,
        tool_calls=[{"id": "c1", "name": "grep", "arguments": '{"q":"bug"}'}],
        stop_reason="tool_calls", tokens_in=1, tokens_out=1, latency_ms=500.0,
    )
    f1 = tracker.annotate("a1", _req([U1], ts=100.0), resp1, _meta())
    assert f1["tool_executions"] == []

    req2 = CanonicalRequest(
        messages=[U1, A1, T1], model_id="gpt-4o", provider="openai",
        timestamp=103.0,
        tool_results=[{"tool_call_id": "c1", "content": "match at line 3"}],
    )
    f2 = tracker.annotate("a2", req2, _resp(), _meta())
    assert len(f2["tool_executions"]) == 1
    ex = f2["tool_executions"][0]
    assert ex["tool_name"] == "grep"
    assert ex["issued_by_action_id"] == "a1"
    assert ex["resolved_by_action_id"] == "a2"
    # req2 at t=103.0, tool issued at t=100.0 + 0.5s response → ~2500ms
    assert ex["latency_ms"] == 2500
    assert ex["is_error"] is None


def test_tool_execution_error_flag_from_anthropic_result():
    tracker = LoopTracker()
    resp1 = CanonicalResponse(
        content=None,
        tool_calls=[{"id": "tu1", "name": "bash", "arguments": {"cmd": "ls"}}],
        stop_reason="tool_use", tokens_in=1, tokens_out=1, latency_ms=100.0,
    )
    tracker.annotate("a1", _req([U1], ts=10.0), resp1, _meta())
    req2 = CanonicalRequest(
        messages=[U1, {"role": "assistant", "content": [{"type": "tool_use", "id": "tu1"}]},
                  {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1",
                                                "content": "no such dir", "is_error": True}]}],
        model_id="claude-sonnet-4", provider="anthropic", timestamp=11.0,
        tool_results=[{"tool_use_id": "tu1", "content": "no such dir", "is_error": True}],
    )
    f2 = tracker.annotate("a2", req2, _resp(), _meta())
    assert f2["tool_executions"][0]["is_error"] is True


def test_unmatched_tool_results_ignored():
    """Results for calls the tracker never saw (proxy restart) are skipped."""
    tracker = LoopTracker()
    req = CanonicalRequest(
        messages=[U1, A1, T1], model_id="gpt-4o", provider="openai", timestamp=1.0,
        tool_results=[{"tool_call_id": "unknown", "content": "x"}],
    )
    f = tracker.annotate("a1", req, _resp(), _meta())
    assert f["tool_executions"] == []


def test_compaction_continuation_relinks_thread():
    """A rewritten history carrying the continuation marker stays on the same
    thread with a context_compaction flag, instead of minting a phantom one."""
    tracker = LoopTracker()
    f1 = tracker.annotate("a1", _req([U1, A1, T1]), _resp(), _meta())
    compacted = _req([
        {"role": "user", "content": "This session is being continued from a previous "
                                    "conversation that ran out of context. Summary: ..."},
    ])
    f2 = tracker.annotate("a2", compacted, _resp(), _meta())
    assert f2["thread_id"] == f1["thread_id"]
    assert f2["step_index"] == 2
    assert f2["prev_action_id"] == "a1"
    assert "context_compaction" in f2["loop_flags"]
    # Informational — must not trip the circuit breaker.
    assert tracker.check_block("s1") is None


def test_compaction_marker_in_block_list_content():
    tracker = LoopTracker()
    tracker.annotate("a1", _req([U1]), _resp(), _meta())
    compacted = _req([
        {"role": "user", "content": [
            {"type": "text", "text": "This session is being continued from a previous conversation."},
        ]},
    ])
    f2 = tracker.annotate("a2", compacted, _resp(), _meta())
    assert f2["step_index"] == 2


def test_fresh_conversation_without_marker_still_forks():
    tracker = LoopTracker()
    f1 = tracker.annotate("a1", _req([U1, A1, T1]), _resp(), _meta())
    f2 = tracker.annotate("a2", _req([{"role": "user", "content": "brand new task"}]),
                          _resp(), _meta())
    assert f2["thread_id"] != f1["thread_id"]


def _cc_req(model, max_tokens):
    r = CanonicalRequest(
        messages=[U1], model_id=model, provider="anthropic", timestamp=0.0,
    )
    r.max_tokens = max_tokens
    return r


def test_utility_call_detection():
    """Shapes observed on the wire from claude-cli/2.1.220 (2026-07)."""
    from agentledger.proxy.loops import is_utility_call
    cc = {"framework": "claude-code"}

    # Startup "quota" probe: max_tokens=1 on the MAIN model — utility.
    assert is_utility_call(_cc_req("claude-opus-5", 1), cc) is True
    # Haiku-class title/summary housekeeping — utility.
    assert is_utility_call(_cc_req("claude-haiku-4-5-20251001", 512), cc) is True

    # Main calls: opus 64k, haiku-as-main 32k — never utility.
    assert is_utility_call(_cc_req("claude-opus-5", 64000), cc) is False
    assert is_utility_call(_cc_req("claude-haiku-4-5-20251001", 32000), cc) is False
    # A `claude -p` main call under CLAUDE_CODE_MAX_OUTPUT_TOKENS=1000 sends
    # max_tokens=1000 on the main model — small max_tokens alone must NOT
    # disqualify it from loop inference (the pre-2.x heuristic's regression).
    assert is_utility_call(_cc_req("claude-opus-5", 1000), cc) is False

    # Only claude-code traffic gets this treatment — a small generic call
    # (e.g. a user's own low-max_tokens app) is still inferred normally.
    assert is_utility_call(_cc_req("claude-haiku-4-5-20251001", 512), {"framework": None}) is False
    # max_tokens absent → never utility.
    assert is_utility_call(_cc_req("claude-opus-5", None), cc) is False
