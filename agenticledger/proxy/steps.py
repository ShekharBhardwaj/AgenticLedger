"""The step model: one timeline for a trace, built from every source.

A trace (one session) is a tree of steps. A step is a model call the
proxy saw, a tool run the agent reported through hooks (or that the
ledger derived from a tool_use and the tool_result that answered it), a
sub-agent, a turn (one user prompt and everything it set off), a
compaction, or a plain span from OpenTelemetry. Every step has a start,
an end, a status and a parent.

The record stays in its three tables (llm_calls, agent_events,
tool_executions); this module reads them and links them. Nothing is
written back, so the timeline can only ever be as good as the record,
and every link says how it was made:

- ``link == "id"``: the parent was named in the data (a tool_use_id the
  model issued, a parent span id, a caller-supplied parent_action_id).
- ``link == "time"``: the step fell inside the parent's time window and
  nothing named a parent. Honest, and shown as such.
- ``link is None``: a root.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Optional

TEXT_CAP = 2000

_TOOL_EVENTS = {
    "PostToolUse": "ok",
    "PostToolUseFailure": "error",
    "PermissionRequest": "asked",
    "PermissionDenied": "denied",
}


def _epoch(value: Any) -> Optional[float]:
    """Seconds since the epoch from a unix number, an ISO string, or a datetime."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.timestamp()
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = _dt.datetime.fromisoformat(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.timestamp()
    return None


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).isoformat()


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return []
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _short(value: Any, cap: int = TEXT_CAP) -> Optional[str]:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n… cut at {cap} characters; the full record is on the call."


def _last_user_text(messages: list) -> Optional[str]:
    for m in reversed(messages):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif isinstance(part, dict) and part.get("type") == "tool_result":
                    return None   # a tool result turn, not a user turn
            if parts:
                return "\n".join(parts)
        return None
    return None


def _call_status(call: dict) -> str:
    detail = call.get("error_detail") or ""
    if isinstance(detail, str) and detail.startswith("blocked:"):
        return "blocked"
    code = call.get("status_code")
    if code is not None and code != 200:
        return "error"
    return "ok"


def _call_step(call: dict) -> Optional[dict]:
    t0 = _epoch(call.get("timestamp"))
    if t0 is None:
        return None
    latency = call.get("latency_ms")
    t1 = t0 + (float(latency) / 1000.0 if latency else 0.0)
    tool_calls = _as_list(call.get("tool_calls"))
    names = [tc.get("name") for tc in tool_calls if isinstance(tc, dict) and tc.get("name")]
    content = call.get("content")
    output = content if isinstance(content, str) and content else None
    if output is None and names:
        output = "asked for " + ", ".join(names)
    return {
        "id": call["action_id"],
        "kind": "call",
        "name": call.get("model_id") or "model call",
        "parent_id": call.get("parent_action_id") or None,
        "link": "id" if call.get("parent_action_id") else None,
        "t0": t0, "t1": t1,
        "status": _call_status(call),
        "source": "otel" if str(call.get("provider") or "").startswith("otlp") else "proxy",
        "agent": call.get("agent_name"),
        "agent_id": None,
        "model": call.get("model_id"),
        "cost_usd": call.get("cost_usd"),
        "tokens_in": call.get("tokens_in"),
        "tokens_out": call.get("tokens_out"),
        "input": _short(_last_user_text(_as_list(call.get("messages")))),
        "output": _short(output),
        "detail": {
            "stop_reason": call.get("stop_reason"),
            "tools": names,
            "tool_use_ids": [tc.get("id") for tc in tool_calls if isinstance(tc, dict) and tc.get("id")],
            "iteration": call.get("iteration"),
            "run_id": call.get("run_id"),
            "loop_flags": _as_list(call.get("loop_flags")),
            "error": call.get("error_detail"),
        },
        "ref": {"call": call["action_id"]},
    }


def _tool_step_from_event(ev: dict) -> Optional[dict]:
    status = _TOOL_EVENTS.get(ev.get("event") or "")
    if status is None:
        return None
    t1 = _epoch(ev.get("timestamp"))
    if t1 is None:
        return None
    dur = ev.get("duration_ms")
    t0 = t1 - (float(dur) / 1000.0 if dur else 0.0)
    return {
        "id": ev["id"],
        "kind": "tool",
        "name": ev.get("tool_name") or "tool",
        "parent_id": ev.get("parent_id") or None,
        "link": "id" if ev.get("parent_id") else None,
        "t0": t0, "t1": t1,
        "status": status,
        "source": ev.get("source") or "hook",
        "agent": None,
        "agent_id": ev.get("agent_id"),
        "model": None,
        "cost_usd": None, "tokens_in": None, "tokens_out": None,
        "input": _short(ev.get("tool_input")),
        "output": _short(ev.get("tool_response") or ev.get("reason")),
        "detail": {"tool_use_id": ev.get("tool_use_id"), "reason": ev.get("reason")},
        "ref": {"event": ev["id"]},
        "_tool_use_id": ev.get("tool_use_id"),
    }


def _tool_step_from_execution(ex: dict, results: dict) -> Optional[dict]:
    t0 = _epoch(ex.get("timestamp"))
    if t0 is None:
        return None
    latency = ex.get("latency_ms")
    t1 = t0 + (float(latency) / 1000.0 if latency else 0.0)
    tid = ex.get("tool_call_id")
    result = results.get(tid) if tid else None
    return {
        "id": f"tool:{tid or ex.get('issued_by_action_id')}",
        "kind": "tool",
        "name": ex.get("tool_name") or "tool",
        "parent_id": ex.get("issued_by_action_id") or None,
        "link": "id" if ex.get("issued_by_action_id") else None,
        "t0": t0, "t1": t1,
        "status": "error" if ex.get("is_error") else "ok",
        "source": "proxy",
        "agent": None,
        "agent_id": None,
        "model": None,
        "cost_usd": None, "tokens_in": None, "tokens_out": None,
        "input": _short(ex.get("arguments")),
        "output": _short(result),
        "detail": {"tool_use_id": tid, "resolved_by": ex.get("resolved_by_action_id"),
                   "derived": "from the tool_use the model issued and the tool_result that answered it"},
        "ref": {"tool_execution": tid},
        "_tool_use_id": tid,
    }


def _span_step(ev: dict) -> Optional[dict]:
    """A plain OpenTelemetry span kept as an agent event (source otel)."""
    t0 = _epoch(ev.get("timestamp"))
    if t0 is None:
        return None
    dur = ev.get("duration_ms")
    t1 = t0 + (float(dur) / 1000.0 if dur else 0.0)
    detail = _as_dict(ev.get("detail"))
    kind = detail.get("kind") if detail.get("kind") in ("tool", "agent", "span") else "span"
    return {
        "id": ev["id"],
        "kind": kind,
        "name": ev.get("tool_name") or ev.get("event") or "span",
        "parent_id": ev.get("parent_id") or None,
        "link": "id" if ev.get("parent_id") else None,
        "t0": t0, "t1": t1,
        "status": "error" if ev.get("outcome") == "error" else "ok",
        "source": "otel",
        "agent": ev.get("agent_type"),
        "agent_id": ev.get("agent_id"),
        "model": None,
        "cost_usd": None, "tokens_in": None, "tokens_out": None,
        "input": _short(ev.get("tool_input")),
        "output": _short(ev.get("tool_response")),
        "detail": {k: v for k, v in detail.items() if k != "kind"},
        "ref": {"event": ev["id"]},
        "_tool_use_id": ev.get("tool_use_id"),
    }


def _tool_results_by_id(calls: list[dict]) -> dict[str, Any]:
    """tool_use_id -> the content the next call carried back for it."""
    out: dict[str, Any] = {}
    for call in calls:
        for r in _as_list(call.get("tool_results")):
            if isinstance(r, dict) and r.get("tool_use_id") and r["tool_use_id"] not in out:
                out[r["tool_use_id"]] = r.get("content")
    return out


def build_steps(calls: list[dict], events: list[dict], tool_executions: list[dict]) -> dict:
    """Steps for one trace, pre-order with depth, plus a summary.

    `calls` are API-shaped llm_calls rows (ISO timestamps are fine),
    `events` are agent_events rows, `tool_executions` the derived tool
    rows. Returns {"steps": [...], "summary": {...}}.
    """
    steps: dict[str, dict] = {}
    order: list[str] = []

    def add(step: Optional[dict]) -> None:
        if step is None or step["id"] in steps:
            return
        steps[step["id"]] = step
        order.append(step["id"])

    for call in calls:
        add(_call_step(call))
    call_steps = [steps[i] for i in order]
    # tool_use_id -> the call that issued it, so tool runs hang off the right call
    issuer: dict[str, str] = {}
    for st in call_steps:
        for tid in st["detail"].get("tool_use_ids") or []:
            issuer.setdefault(tid, st["id"])

    results = _tool_results_by_id(calls)
    hook_tools_by_use: dict[str, str] = {}
    agent_bounds: dict[str, dict] = {}     # agent_id -> {"start": ev, "stop": ev, "type": str}
    turns: list[dict] = []
    compactions: list[dict] = []
    session_end: Optional[float] = None

    for ev in sorted(events, key=lambda e: _epoch(e.get("timestamp")) or 0.0):
        name = ev.get("event") or ""
        if name in _TOOL_EVENTS:
            st = _tool_step_from_event(ev)
            if st is None:
                continue
            tid = st.pop("_tool_use_id", None)
            if tid and not st["parent_id"] and tid in issuer:
                st["parent_id"], st["link"] = issuer[tid], "id"
            if tid:
                hook_tools_by_use[tid] = st["id"]
            st["detail"]["tool_use_id"] = tid
            add(st)
        elif name == "Span":
            st = _span_step(ev)
            if st is None:
                continue
            st.pop("_tool_use_id", None)
            add(st)
        elif name == "SubagentStart" and ev.get("agent_id"):
            agent_bounds.setdefault(ev["agent_id"], {})["start"] = ev
            agent_bounds[ev["agent_id"]]["type"] = ev.get("agent_type")
        elif name == "SubagentStop" and ev.get("agent_id"):
            agent_bounds.setdefault(ev["agent_id"], {})["stop"] = ev
            agent_bounds[ev["agent_id"]].setdefault("type", ev.get("agent_type"))
        elif name == "UserPromptSubmit":
            turns.append(ev)
        elif name == "PreCompact":
            compactions.append({"pre": ev})
        elif name == "PostCompact":
            if compactions and "post" not in compactions[-1]:
                compactions[-1]["post"] = ev
            else:
                compactions.append({"post": ev})
        elif name == "SessionEnd":
            session_end = _epoch(ev.get("timestamp"))

    # Tool executions the ledger derived from the wire. A hook already
    # covered the same tool_use_id? Keep the hook's step (real duration and
    # output) and give it the issuing call as parent by id.
    for ex in tool_executions:
        tid = ex.get("tool_call_id")
        if tid and tid in hook_tools_by_use:
            st = steps[hook_tools_by_use[tid]]
            if not st["parent_id"] and ex.get("issued_by_action_id"):
                st["parent_id"], st["link"] = ex["issued_by_action_id"], "id"
            continue
        st = _tool_step_from_execution(ex, results)
        if st is None:
            continue
        st.pop("_tool_use_id", None)
        add(st)

    # Compactions: a marker from PreCompact to PostCompact.
    for c in compactions:
        pre, post = c.get("pre"), c.get("post")
        t0 = _epoch((pre or post)["timestamp"])
        if t0 is None:
            continue
        t1 = _epoch(post["timestamp"]) if post else t0
        detail = _as_dict((post or pre).get("detail"))
        st = {
            "id": f"compact:{(pre or post)['id']}",
            "kind": "compaction",
            "name": "context compacted" + (f" ({detail['trigger']})" if detail.get("trigger") else ""),
            "parent_id": None, "link": None,
            "t0": t0, "t1": max(t1, t0),
            "status": "ok",
            "source": "hook",
            "agent": None, "agent_id": None,
            "model": None, "cost_usd": None, "tokens_in": None, "tokens_out": None,
            "input": None,
            "output": _short(detail.get("compact_summary")) if detail.get("compact_summary") else None,
            "detail": {k: v for k, v in detail.items() if k != "compact_summary"},
            "ref": {"event": (pre or post)["id"]},
        }
        add(st)

    # Parents that are not in this trace (a parent_action_id from another
    # session, a span the exporter never sent) become roots, honestly, and
    # stay eligible for a sub-agent or a turn to claim them by time.
    for st in steps.values():
        if st["parent_id"] and st["parent_id"] not in steps:
            st["detail"]["missing_parent"] = st["parent_id"]
            st["parent_id"], st["link"] = None, None

    all_t1 = [s["t1"] for s in steps.values()]
    last_end = max(all_t1) if all_t1 else None

    # Sub-agents: one step from start to stop; what the sub-agent reported
    # hangs off it by id, calls inside its window by time.
    for agent_id, b in agent_bounds.items():
        start, stop = b.get("start"), b.get("stop")
        t0 = _epoch((start or stop or {}).get("timestamp"))
        if t0 is None:
            continue
        t1 = _epoch(stop.get("timestamp")) if stop else (last_end if last_end and last_end > t0 else t0)
        st = {
            "id": f"agent:{agent_id}",
            "kind": "agent",
            "name": b.get("type") or agent_id,
            "parent_id": None, "link": None,
            "t0": t0, "t1": t1,
            "status": "ok" if stop else "running",
            "source": "hook",
            "agent": b.get("type"), "agent_id": agent_id,
            "model": None, "cost_usd": None, "tokens_in": None, "tokens_out": None,
            "input": None, "output": None,
            "detail": {"agent_id": agent_id},
            "ref": {"event": (start or stop)["id"]},
        }
        add(st)
        for other in steps.values():
            if other is st or other.get("parent_id"):
                continue
            if other.get("agent_id") == agent_id:
                other["parent_id"], other["link"] = st["id"], "id"
            elif other["kind"] == "call" and t0 <= other["t0"] <= t1:
                other["parent_id"], other["link"] = st["id"], "time"

    # Turns: one user prompt and everything it set off, until the next prompt.
    turn_steps: list[dict] = []
    for i, ev in enumerate(turns):
        t0 = _epoch(ev.get("timestamp"))
        if t0 is None:
            continue
        nxt = _epoch(turns[i + 1].get("timestamp")) if i + 1 < len(turns) else None
        t1 = nxt if nxt is not None else (session_end or last_end or t0)
        if t1 < t0:
            t1 = t0
        detail = _as_dict(ev.get("detail"))
        prompt = detail.get("prompt") if isinstance(detail.get("prompt"), str) else None
        st = {
            "id": f"turn:{ev['id']}",
            "kind": "turn",
            "name": (prompt.strip().splitlines()[0][:120] if prompt and prompt.strip() else f"turn {i + 1}"),
            "parent_id": None, "link": None,
            "t0": t0, "t1": t1,
            "status": "ok",
            "source": ev.get("source") or "hook",
            "agent": None, "agent_id": None,
            "model": None, "cost_usd": None, "tokens_in": None, "tokens_out": None,
            "input": _short(prompt), "output": None,
            "detail": {"prompt_id": ev.get("prompt_id")},
            "ref": {"event": ev["id"]},
        }
        turn_steps.append(st)
        add(st)
    for st in turn_steps:
        for other in steps.values():
            if other is st or other["kind"] == "turn" or other.get("parent_id"):
                continue
            if st["t0"] <= other["t0"] < st["t1"] or (other["t0"] == st["t0"] == st["t1"]):
                other["parent_id"], other["link"] = st["id"], "time"

    # Pre-order by start time, depth-first.
    children: dict[Optional[str], list[dict]] = {}
    for st in steps.values():
        children.setdefault(st["parent_id"], []).append(st)
    for lst in children.values():
        lst.sort(key=lambda s: (s["t0"], s["t1"]))

    out: list[dict] = []
    seen: set[str] = set()

    def walk(parent: Optional[str], depth: int) -> None:
        for st in children.get(parent, []):
            if st["id"] in seen:
                continue
            seen.add(st["id"])
            row = {k: v for k, v in st.items() if k not in ("t0", "t1")}
            row["started_at"] = _iso(st["t0"])
            row["ended_at"] = _iso(st["t1"])
            row["duration_ms"] = round((st["t1"] - st["t0"]) * 1000.0, 1)
            row["depth"] = depth
            row["children"] = len(children.get(st["id"], []))
            out.append(row)
            walk(st["id"], depth + 1)

    walk(None, 0)

    counts: dict[str, int] = {}
    sources: set[str] = set()
    for row in out:
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        sources.add(row["source"])
    t0s = [s["t0"] for s in steps.values()]
    summary = {
        "steps": len(out),
        "kinds": counts,
        "sources": sorted(sources),
        "started_at": _iso(min(t0s)) if t0s else None,
        "ended_at": _iso(last_end) if last_end else None,
        "duration_ms": round((last_end - min(t0s)) * 1000.0, 1) if t0s and last_end else 0.0,
        "placed_by_time": sum(1 for r in out if r["link"] == "time"),
        "cost_usd": round(sum(float(r["cost_usd"] or 0) for r in out), 6),
    }
    return {"steps": out, "summary": summary}
