"""The step model: one timeline per trace, from every source, with every
link saying how it was made."""

import time

import httpx2 as httpx

from agenticledger.proxy.hooks_ingest import event_from_hook
from agenticledger.proxy.steps import build_steps

from .conftest import anthropic_response

SESSION = "11111111-2222-4333-8444-555555555555"


def _call(action_id, ts, *, latency=500.0, tool_calls=None, tool_results=None, parent=None, **extra):
    return {
        "action_id": action_id, "session_id": SESSION, "timestamp": ts, "latency_ms": latency,
        "model_id": "claude-sonnet-5", "provider": "anthropic", "status_code": 200,
        "messages": [{"role": "user", "content": "fix the test"}],
        "content": None if tool_calls else "done", "tool_calls": tool_calls, "tool_results": tool_results,
        "parent_action_id": parent, "cost_usd": 0.001, "tokens_in": 10, "tokens_out": 5, **extra,
    }


def _hook(event, ts, **fields):
    return event_from_hook({"session_id": SESSION, "hook_event_name": event, **fields}, ts)


def _by_id(result):
    return {s["id"]: s for s in result["steps"]}


def test_tool_runs_hang_off_the_call_that_issued_them_by_id():
    t = 1_700_000_000.0
    calls = [
        _call("c1", t, tool_calls=[{"id": "tu_1", "name": "Bash", "arguments": "{}"}]),
        _call("c2", t + 3, tool_results=[{"tool_use_id": "tu_1", "content": "ok"}]),
    ]
    events = [_hook("PostToolUse", t + 2.5, tool_name="Bash", tool_use_id="tu_1",
                    tool_input={"command": "ls"}, tool_response="ok", duration_ms=1500)]
    out = build_steps(calls, events, [])
    steps = _by_id(out)
    tool = next(s for s in out["steps"] if s["kind"] == "tool")
    assert tool["parent_id"] == "c1" and tool["link"] == "id"
    assert tool["depth"] == 1 and steps["c1"]["depth"] == 0 and steps["c1"]["children"] == 1
    assert tool["duration_ms"] == 1500.0 and tool["status"] == "ok"
    # the tool started 1.5 s before the hook fired, inside the call's window
    assert tool["started_at"] > steps["c1"]["started_at"]
    assert out["summary"]["kinds"] == {"call": 2, "tool": 1}
    assert out["summary"]["placed_by_time"] == 0
    assert out["summary"]["sources"] == ["hook", "proxy"]


def test_a_hook_without_an_id_match_is_placed_by_time_and_says_so():
    t = 1_700_000_000.0
    calls = [_call("c1", t), _call("c2", t + 10)]
    events = [_hook("PostToolUse", t + 4, tool_name="Read", tool_use_id="unknown",
                    tool_input={"file_path": "a.py"}, tool_response="x", duration_ms=100)]
    out = build_steps(calls, events, [])
    tool = next(s for s in out["steps"] if s["kind"] == "tool")
    assert tool["parent_id"] is None and tool["link"] is None       # no window claimed it
    # with a turn around it, the turn claims it by time
    events.insert(0, _hook("UserPromptSubmit", t - 1, prompt="fix the failing test"))
    out = build_steps(calls, events, [])
    turn = next(s for s in out["steps"] if s["kind"] == "turn")
    tool = next(s for s in out["steps"] if s["kind"] == "tool")
    assert turn["name"] == "fix the failing test"
    assert tool["parent_id"] == turn["id"] and tool["link"] == "time"
    assert out["summary"]["placed_by_time"] == 3   # both calls and the tool
    assert out["steps"][0]["kind"] == "turn" and out["steps"][0]["depth"] == 0


def test_sub_agents_nest_their_own_events_by_id_and_calls_by_time():
    t = 1_700_000_000.0
    calls = [_call("c1", t), _call("c2", t + 5), _call("c3", t + 20)]
    events = [
        _hook("SubagentStart", t + 3, agent_id="ag-1", agent_type="Explore"),
        _hook("PostToolUse", t + 6, agent_id="ag-1", tool_name="Grep", tool_use_id="tu_9",
              tool_input={}, tool_response="hit", duration_ms=50),
        _hook("SubagentStop", t + 8, agent_id="ag-1", agent_type="Explore"),
    ]
    out = build_steps(calls, events, [])
    steps = _by_id(out)
    agent = steps["agent:ag-1"]
    assert agent["kind"] == "agent" and agent["name"] == "Explore" and agent["status"] == "ok"
    assert agent["duration_ms"] == 5000.0
    grep = next(s for s in out["steps"] if s["kind"] == "tool")
    assert grep["parent_id"] == "agent:ag-1" and grep["link"] == "id"
    assert steps["c2"]["parent_id"] == "agent:ag-1" and steps["c2"]["link"] == "time"
    assert steps["c1"]["parent_id"] is None and steps["c3"]["parent_id"] is None
    # pre-order: c1, agent, (c2, grep by time), c3
    assert [s["id"] for s in out["steps"]] == ["c1", "agent:ag-1", "c2", grep["id"], "c3"]
    assert [s["depth"] for s in out["steps"]] == [0, 0, 1, 1, 0]


def test_derived_tool_executions_fill_in_when_no_hook_reported_the_run():
    t = 1_700_000_000.0
    calls = [
        _call("c1", t, tool_calls=[{"id": "tu_1", "name": "Bash", "arguments": "{}"}]),
        _call("c2", t + 3, tool_results=[{"tool_use_id": "tu_1", "content": "listing"}]),
    ]
    execs = [{"tool_call_id": "tu_1", "tool_name": "Bash", "arguments": '{"command": "ls"}',
              "issued_by_action_id": "c1", "resolved_by_action_id": "c2", "session_id": SESSION,
              "latency_ms": 2400, "is_error": 0, "timestamp": t + 0.5}]
    out = build_steps(calls, [], execs)
    tool = next(s for s in out["steps"] if s["kind"] == "tool")
    assert tool["parent_id"] == "c1" and tool["link"] == "id" and tool["source"] == "proxy"
    assert tool["output"] == "listing" and tool["duration_ms"] == 2400.0
    # a hook for the same tool_use_id wins (real duration and output), still under c1
    hook = _hook("PostToolUse", t + 2.9, tool_name="Bash", tool_use_id="tu_1",
                 tool_input={"command": "ls"}, tool_response="listing\n", duration_ms=2300)
    out = build_steps(calls, [hook], execs)
    tools = [s for s in out["steps"] if s["kind"] == "tool"]
    assert len(tools) == 1 and tools[0]["source"] == "hook" and tools[0]["parent_id"] == "c1"


def test_compaction_and_missing_parents_are_honest():
    t = 1_700_000_000.0
    calls = [_call("c1", t, parent="not-in-this-trace")]
    events = [
        _hook("UserPromptSubmit", t - 1, prompt="go"),
        _hook("PreCompact", t + 2, trigger="auto"),
        _hook("PostCompact", t + 4, trigger="auto", compact_summary="the gist"),
    ]
    out = build_steps(calls, events, [])
    steps = _by_id(out)
    assert steps["c1"]["parent_id"] == f"turn:{events[0]['id']}"
    assert steps["c1"]["detail"]["missing_parent"] == "not-in-this-trace"
    compact = next(s for s in out["steps"] if s["kind"] == "compaction")
    assert compact["name"] == "context compacted (auto)" and compact["duration_ms"] == 2000.0
    assert compact["output"] == "the gist" and compact["link"] == "time"


def test_trace_steps_endpoint_joins_the_proxied_call_and_the_hooks(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response(
        None, tool_uses=[{"id": "tu_7", "name": "Bash", "input": {"command": "date"}}],
        stop_reason="tool_use")))
    assert client.post("/v1/messages", json={"model": "claude-sonnet-5", "max_tokens": 8,
                                             "messages": [{"role": "user", "content": "what day is it"}]},
                       headers={"x-agenticledger-session-id": SESSION}).status_code == 200
    body = {"session_id": SESSION, "hook_event_name": "PostToolUse", "tool_name": "Bash",
            "tool_use_id": "tu_7", "tool_input": {"command": "date"}, "tool_response": "Friday",
            "duration_ms": 40}
    assert client.post("/v1/hooks/claude-code", json=body).status_code == 200
    out = client.get(f"/api/traces/{SESSION}/steps").json()
    kinds = [s["kind"] for s in out["steps"]]
    assert kinds == ["call", "tool"]
    assert out["steps"][1]["parent_id"] == out["steps"][0]["id"] and out["steps"][1]["link"] == "id"
    assert out["steps"][1]["output"] == "Friday"
    assert out["summary"]["sources"] == ["hook", "proxy"]
    assert client.get("/api/traces/nope/steps").status_code == 404


def test_otlp_span_tree_arrives_whole(proxy):
    """An agent span, a tool span under it, and a model call under the tool
    span: the tree the exporter saw, not only the model call."""
    client = proxy()
    trace = "0af7651916cd43dd8448eb211c80319c"
    base = int(time.time()) * 1_000_000_000

    def span(sid, name, start, end, attrs, parent=None):
        s = {"traceId": trace, "spanId": sid, "name": name,
             "startTimeUnixNano": str(base + start), "endTimeUnixNano": str(base + end),
             "attributes": [{"key": k, "value": v} for k, v in attrs]}
        if parent:
            s["parentSpanId"] = parent
        return s
    sv = lambda x: {"stringValue": x}  # noqa: E731
    payload = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": sv("my-agent")}]},
                                  "scopeSpans": [{"spans": [
        span("a" * 16, "invoke_agent planner", 0, 5_000_000_000,
             [("gen_ai.operation.name", sv("invoke_agent")), ("gen_ai.agent.name", sv("planner")),
              ("gen_ai.conversation.id", sv("conv-tree"))]),
        span("b" * 16, "execute_tool search", 1_000_000_000, 3_000_000_000,
             [("gen_ai.operation.name", sv("execute_tool")), ("gen_ai.tool.name", sv("search")),
              ("gen_ai.tool.call.arguments", sv('{"q": "weather"}')), ("gen_ai.tool.call.result", sv("sunny"))],
             parent="a" * 16),
        span("c" * 16, "chat gpt-4o", 1_200_000_000, 2_800_000_000,
             [("gen_ai.operation.name", sv("chat")), ("gen_ai.system", sv("openai")),
              ("gen_ai.request.model", sv("gpt-4o")), ("gen_ai.usage.input_tokens", {"intValue": "10"}),
              ("gen_ai.usage.output_tokens", {"intValue": "5"})],
             parent="b" * 16),
    ]}]}]}
    assert client.post("/v1/traces", json=payload, headers={"content-type": "application/json"}).status_code == 200
    out = client.get("/api/traces/conv-tree/steps").json()
    kinds = [(s["kind"], s["name"], s["depth"], s["link"]) for s in out["steps"]]
    assert kinds == [("agent", "planner", 0, None), ("tool", "search", 1, "id"), ("call", "gpt-4o", 2, "id")]
    assert out["steps"][1]["output"] == "sunny" and out["steps"][1]["source"] == "otel"
    assert out["summary"]["placed_by_time"] == 0
    # the model call is still a call row, filed under the same session
    assert len(client.get("/session/conv-tree").json()) == 1
