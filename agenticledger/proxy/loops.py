"""
Loop & run inference from raw LLM traffic.

The proxy only sees individual LLM calls, but agentic workloads have
structure the calls themselves reveal:

* **ReAct threads** — call N+1 of a tool-use loop contains call N's messages
  plus the assistant reply and tool results. Hashing each message and
  matching known chains as prefixes stitches calls into threads with a
  step index and an explicit prev link, with no client cooperation.

* **Ralph runs** — fresh-context loop iterations (`while :; do claude -p ...`)
  share NO message prefix; each spawn is a new session. They do share a
  system prompt. A new session whose system-prompt hash matches a recently
  seen one is grouped as the next iteration of the same run.

* **Loop health** — a thread issuing the same tool call with the same
  arguments over and over is stuck. Flags are recorded per call, alerts can
  fire, and (opt-in) the proxy can circuit-break the session with a 429
  before more budget burns.

Explicit headers always win over inference: x-agenticledger-run-id and
x-agenticledger-iteration pin run grouping exactly.

All state is in-memory and best-effort: a proxy restart forgets chains
(calls then start new threads), and multi-replica deployments need sticky
routing for coherent inference. Inference metadata is never a security
boundary.
"""

import contextlib
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

# Consecutive identical tool-call signatures before a thread is flagged stuck.
DEFAULT_REPEAT_THRESHOLD = 3

# Seconds between fresh-context spawns that still count as the same run.
DEFAULT_RUN_GAP_SECONDS = 900.0

_MAX_SESSIONS = 10_000       # LRU bound on tracked sessions
_MAX_THREADS_PER_SESSION = 64
_HASH_CONTENT_CAP = 4_000    # per-message bytes hashed — enough to disambiguate


def _digest(value: object) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, default=str)[:_HASH_CONTENT_CAP]
    except Exception:
        raw = str(value)[:_HASH_CONTENT_CAP]
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


def _message_chain(messages: list) -> tuple[str, ...]:
    return tuple(
        _digest({"role": m.get("role"), "content": m.get("content"),
                 "tool_calls": m.get("tool_calls")})
        if isinstance(m, dict) else _digest(m)
        for m in messages
    )


def _count_turns(messages: list) -> int:
    """User turns — user messages that aren't pure tool-result carriers."""
    turns = 0
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if (
            isinstance(content, list) and content
            and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        ):
            continue
        turns += 1
    return turns


def _system_digest(req) -> Optional[str]:
    """Stable hash of the system prompt in any wire shape, or None."""
    if req.system_prompt:
        return _digest(req.system_prompt)
    for m in req.messages[:1]:
        if isinstance(m, dict) and m.get("role") == "system":
            return _digest(m.get("content"))
    return None


# Claude Code fires small utility calls at the same endpoint as the main
# loop: startup probes (a max_tokens=1 "quota" ping on the main model) and
# haiku-class title/summary calls (max_tokens in the hundreds). Main calls
# default to 32k-64k max_tokens, but users shrink that arbitrarily via
# CLAUDE_CODE_MAX_OUTPUT_TOKENS — so a small cap alone cannot discriminate:
# it must pair with a haiku-class model, except for the near-zero probes no
# real completion could fit in.
_PROBE_MAX_TOKENS = 8
_UTILITY_MAX_TOKENS = 1024


def is_utility_call(req, meta: dict) -> bool:
    """True for framework housekeeping calls that must stay out of loop
    inference — chaining them inflates step counts and resets repeat streaks."""
    if meta.get("framework") != "claude-code" or req.max_tokens is None:
        return False
    if req.max_tokens <= _PROBE_MAX_TOKENS:
        return True
    return (
        req.max_tokens <= _UTILITY_MAX_TOKENS
        and "haiku" in (req.model_id or "").lower()
    )


# Claude Code's compaction rewrites history into a summary message with a
# stable opening phrase — the reliable signal that a shrunken, non-matching
# prefix is a continuation rather than a brand-new conversation.
_COMPACTION_MARKER = "this session is being continued"


def _is_compaction_continuation(req) -> bool:
    for m in req.messages[:2]:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and isinstance(b.get("text"), str)
            )
        if isinstance(content, str) and _COMPACTION_MARKER in content[:400].lower():
            return True
    return False


def _tool_signature(tool_calls: Optional[list]) -> Optional[tuple[str, ...]]:
    if not tool_calls:
        return None
    return tuple(
        f"{tc.get('name')}:{_digest(tc.get('arguments'))}"
        for tc in tool_calls
        if isinstance(tc, dict)
    ) or None


@dataclass
class _Thread:
    thread_id: str
    chain: tuple[str, ...]
    step_index: int
    last_action_id: str
    last_tool_sig: Optional[tuple[str, ...]] = None
    repeat_streak: int = 1
    last_ts: float = 0.0
    # tool_call_id → {tool_name, arguments, issued_by_action_id, issued_ts}
    pending_tools: dict = field(default_factory=dict)


@dataclass
class _SessionState:
    threads: list = field(default_factory=list)
    run_id: Optional[str] = None
    iteration: Optional[int] = None
    flags: set = field(default_factory=set)
    last_seen: float = 0.0


class LoopTracker:
    """Stateful inference over the capture stream. One instance per app.

    Not thread-safe by design: the proxy calls annotate() from a single event
    loop (sync capture) or a single FIFO worker (async capture), never
    concurrently.
    """

    def __init__(
        self,
        repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD,
        run_gap_seconds: float = DEFAULT_RUN_GAP_SECONDS,
        max_steps: Optional[int] = None,
        completion_promise: Optional[str] = None,
        clock=time.time,
    ) -> None:
        self._repeat_threshold = repeat_threshold
        self._run_gap = run_gap_seconds
        self._max_steps = max_steps
        self._promise_re = None
        if completion_promise:
            # An invalid pattern leaves promise detection off.
            with contextlib.suppress(re.error):
                self._promise_re = re.compile(completion_promise)
        self._clock = clock
        self._sessions: dict[str, _SessionState] = {}
        # (app-or-framework, system-prompt hash) → run grouping state
        self._run_sigs: dict[tuple, dict] = {}

    # ── enforcement-time resolution (the kill switch's eyes) ─────────────────

    def resolve_run(self, req, meta: dict) -> Optional[str]:
        """Which run would this incoming call belong to? Read-only twin of
        the capture-time grouping, for the enforcement gate: explicit
        attribution wins; a session the tracker has seen keeps its run; a
        fresh-context call resolves through the live run-signature table
        (same key _assign_run uses) without mutating anything. Returns None
        when the tracker cannot know — enforcement then has nothing to
        match, which fails open by design."""
        try:
            explicit = meta.get("run_id")
            if explicit:
                return explicit
            state = self._sessions.get(meta.get("session_id") or "-")
            if state is not None and state.run_id:
                return state.run_id
            sys_digest = _system_digest(req)
            if sys_digest is None:
                return None
            key = (meta.get("app_id") or meta.get("framework") or "-", sys_digest)
            sig = self._run_sigs.get(key)
            if sig is not None and self._clock() - sig["last_seen"] <= self._run_gap:
                return sig["run_id"]
            return None
        except Exception:
            return None

    # ── capture-time annotation ──────────────────────────────────────────────

    def annotate(self, action_id: str, req, resp, meta: dict) -> dict:
        """Infer loop fields for one captured call. Returns the columns to
        store: thread_id, step_index, turn_index, prev_action_id, run_id,
        iteration, loop_flags. Never raises."""
        try:
            return self._annotate(action_id, req, resp, meta)
        except Exception:
            return {
                "thread_id": None, "step_index": None, "turn_index": None,
                "prev_action_id": None,
                "run_id": meta.get("run_id"), "iteration": _as_int(meta.get("iteration")),
                "loop_flags": None,
                "tool_executions": [],
            }

    def _annotate(self, action_id: str, req, resp, meta: dict) -> dict:
        now = self._clock()
        session_id = meta.get("session_id") or "-"
        state = self._session(session_id)
        new_session = state.last_seen == 0.0
        state.last_seen = now

        # ── Run grouping (explicit headers win; else fresh-context inference)
        run_id = meta.get("run_id")
        iteration = _as_int(meta.get("iteration"))
        if run_id is None:
            if new_session:
                self._assign_run(state, req, meta, now)
            run_id = state.run_id
            if iteration is None:
                iteration = state.iteration
        else:
            state.run_id, state.iteration = run_id, iteration

        # ── Thread stitching via message-chain prefix match
        chain = _message_chain(req.messages)
        thread = self._match_thread(state, chain)
        new_flags: list[str] = []

        # Compaction tolerance: a rewritten history breaks the prefix chain,
        # but Claude Code's continuation marker identifies it — re-link to the
        # session's most recently active thread instead of minting a phantom
        # new one, and record the event so the rewrite is visible.
        if thread is None and state.threads and _is_compaction_continuation(req):
            thread = max(state.threads, key=lambda t: t.last_ts)
            new_flags.append("context_compaction")

        if thread is None:
            thread = _Thread(
                thread_id=f"t-{action_id[:13]}", chain=chain,
                step_index=1, last_action_id=action_id,
            )
            state.threads.append(thread)
            del state.threads[:-_MAX_THREADS_PER_SESSION]
            prev_action_id = None
        else:
            prev_action_id = thread.last_action_id
            thread.step_index += 1
            thread.chain = chain
            thread.last_action_id = action_id
        thread.last_ts = now

        # ── Stuck-loop detection: identical tool-call signature streaks
        sig = _tool_signature(resp.tool_calls)
        if sig is not None and sig == thread.last_tool_sig:
            thread.repeat_streak += 1
            if thread.repeat_streak >= self._repeat_threshold:
                new_flags.append("repeat_tool_call")
        else:
            thread.repeat_streak = 1
        thread.last_tool_sig = sig

        if self._max_steps is not None and thread.step_index >= self._max_steps:
            new_flags.append("step_budget_exceeded")

        # Completion promise: a runner-visible "the loop is done" signal in the
        # response text (e.g. an exact COMPLETE marker). Recorded as a flag so
        # run status survives proxy restarts via the loop_flags column.
        if (
            self._promise_re is not None
            and resp.content
            and self._promise_re.search(resp.content)
        ):
            new_flags.append("completion_promise")

        state.flags.update(new_flags)

        # ── Tool-execution pairing: results in THIS request resolve tool
        # calls issued by the thread's previous response. The proxy never sees
        # the tool run — the gap between the two calls IS its wall-clock time.
        executions = self._pair_tools(thread, action_id, session_id, req, resp)

        return {
            "thread_id": thread.thread_id,
            "step_index": thread.step_index,
            "turn_index": _count_turns(req.messages),
            "prev_action_id": prev_action_id,
            "run_id": run_id,
            "iteration": iteration,
            "loop_flags": json.dumps(new_flags) if new_flags else None,
            "tool_executions": executions,
        }

    def _pair_tools(self, thread: _Thread, action_id: str, session_id: str,
                    req, resp) -> list[dict]:
        executions: list[dict] = []
        for tr in req.tool_results or []:
            call_id = tr.get("tool_call_id") or tr.get("tool_use_id")
            pending = thread.pending_tools.pop(call_id, None)
            if pending is None:
                continue
            executions.append({
                "tool_call_id": call_id,
                "tool_name": pending["tool_name"],
                "arguments": pending["arguments"],
                "issued_by_action_id": pending["issued_by_action_id"],
                "resolved_by_action_id": action_id,
                "session_id": session_id,
                "thread_id": thread.thread_id,
                "latency_ms": max(round((req.timestamp - pending["issued_ts"]) * 1000), 0),
                "is_error": bool(tr.get("is_error")) if tr.get("is_error") is not None else None,
                "timestamp": req.timestamp,
            })
        # Register this response's tool calls as pending for the next call.
        issued_ts = req.timestamp + (resp.latency_ms or 0) / 1000
        for tc in resp.tool_calls or []:
            if isinstance(tc, dict) and tc.get("id"):
                thread.pending_tools[tc["id"]] = {
                    "tool_name": tc.get("name"),
                    "arguments": tc.get("arguments"),
                    "issued_by_action_id": action_id,
                    "issued_ts": issued_ts,
                }
        # Bound memory: abandoned tool calls (never resolved) are dropped
        # beyond a small cap rather than accumulating forever.
        if len(thread.pending_tools) > 32:
            for key in list(thread.pending_tools)[:-32]:
                del thread.pending_tools[key]
        return executions

    # ── request-time circuit breaker ─────────────────────────────────────────

    def check_block(self, session_id: Optional[str]) -> Optional[str]:
        """Return a block reason when the session tripped a loop guard.
        Cheap dict lookups only — safe on the hot path."""
        state = self._sessions.get(session_id or "-")
        if state is None:
            return None
        if "repeat_tool_call" in state.flags:
            return (
                f"Loop guard: the agent issued the same tool call with identical "
                f"arguments {self._repeat_threshold}+ times in a row. Session: {session_id}"
            )
        if "step_budget_exceeded" in state.flags:
            return (
                f"Loop guard: a thread in this session exceeded the "
                f"{self._max_steps}-step budget. Session: {session_id}"
            )
        return None

    # ── internals ────────────────────────────────────────────────────────────

    def _session(self, session_id: str) -> _SessionState:
        state = self._sessions.get(session_id)
        if state is None:
            if len(self._sessions) >= _MAX_SESSIONS:
                oldest = min(self._sessions, key=lambda k: self._sessions[k].last_seen)
                del self._sessions[oldest]
            state = _SessionState()
            self._sessions[session_id] = state
        return state

    def _assign_run(self, state: _SessionState, req, meta: dict, now: float) -> None:
        """Group fresh-context sessions sharing a system prompt into a run."""
        sys_digest = _system_digest(req)
        if sys_digest is None:
            return
        key = (meta.get("app_id") or meta.get("framework") or "-", sys_digest)
        sig = self._run_sigs.get(key)
        if sig is not None and now - sig["last_seen"] <= self._run_gap:
            sig["iteration"] += 1
            sig["last_seen"] = now
        else:
            # The auto- prefix marks inferred runs so read paths can hide
            # one-iteration "runs" (every one-off session starts one) until a
            # second fresh-context iteration confirms an actual loop.
            sig = {"run_id": f"auto-run-{uuid.uuid4().hex[:12]}", "iteration": 1, "last_seen": now}
            self._run_sigs[key] = sig
        state.run_id = sig["run_id"]
        state.iteration = sig["iteration"]

    @staticmethod
    def _match_thread(state: _SessionState, chain: tuple[str, ...]) -> Optional[_Thread]:
        best: Optional[_Thread] = None
        for t in state.threads:
            if (
                len(t.chain) <= len(chain)
                and chain[: len(t.chain)] == t.chain
                and (best is None or len(t.chain) > len(best.chain))
            ):
                best = t
        return best


def _as_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
