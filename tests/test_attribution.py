"""The attribution pipeline (#99, contract 2): one brain, two verbs.
resolve (read-only) must always agree with what commit then writes, and
refusals are first-class commits."""

import json
from pathlib import Path

from agenticledger.proxy.attribution import AttributionResolver
from agenticledger.proxy.loops import LoopTracker
from agenticledger.proxy.normalize import CanonicalResponse, normalize_request

WIRE = Path(__file__).parent / "fixtures" / "wire"


def _req(name: str):
    record = json.loads((WIRE / f"{name}.json").read_text())
    body = json.loads(record["request"]["body"])
    path = record["request"]["path"]
    return normalize_request(body, path[path.index("/v1/"):])


def _resp():
    return CanonicalResponse(content="ok", tool_calls=None, stop_reason="stop",
                             tokens_in=1, tokens_out=1, latency_ms=1.0)


def _meta(session, **kw):
    return {"session_id": session, "run_id": None, "iteration": None,
            "framework": "claude-code", "agent_name": "claude-code", **kw}


def test_resolve_agrees_with_commit_across_a_real_loop():
    """Two real Claude Code invocations (fresh sessions, salted prompts):
    what resolve predicts before each commit is exactly what commit
    writes. No second brain, so no disagreement is possible."""
    clock = {"t": 1000.0}
    resolver = AttributionResolver(LoopTracker(clock=lambda: clock["t"]))

    first = _req("claude-code-plain-main")
    before = resolver.resolve(_meta("s1"), first)
    assert before.source == "none" and before.run_id is None
    committed = resolver.commit("a1", first, _resp(), _meta("s1"))
    run = committed["run_id"]
    assert run and run.startswith("auto-run-")

    clock["t"] += 30
    second = _req("claude-code-tool-main")
    predicted = resolver.resolve(_meta("s2"), second)
    assert predicted.source == "inferred" and predicted.run_id == run
    assert resolver.commit("a2", second, _resp(), _meta("s2"))["run_id"] == run

    # The follow-up in session 2 resolves through the session, not the signature.
    follow = _req("claude-code-tool-followup")
    assert resolver.resolve(_meta("s2"), follow).run_id == run


def test_explicit_attribution_wins_and_says_so():
    resolver = AttributionResolver(LoopTracker())
    req = _req("claude-code-plain-main")
    a = resolver.resolve(_meta("s1", run_id="night-shift", iteration=4), req)
    assert (a.run_id, a.iteration, a.source) == ("night-shift", 4, "explicit")


def test_a_refusal_is_a_commit_too():
    """The wall refused under a run: the session's next call, whatever its
    shape, resolves to that run (#77's guarantee, now structural)."""
    clock = {"t": 1000.0}
    resolver = AttributionResolver(LoopTracker(clock=lambda: clock["t"]))
    main = _req("claude-code-tool-main")
    walled = resolver.resolve(_meta("k1", run_id="walled-run"), main)
    resolver.commit_refusal(walled, main, _meta("k1"))

    title = _req("claude-code-tool-title")   # a different system prompt, same session
    assert resolver.resolve(_meta("k1"), title).run_id == "walled-run"
    # And the signature stays alive for the next fresh-context iteration.
    clock["t"] += 30
    assert resolver.resolve(_meta("k2"), _req("claude-code-plain-main")).run_id == "walled-run"


def test_unknowable_resolves_to_none_and_never_raises():
    resolver = AttributionResolver(LoopTracker())
    a = resolver.resolve({}, None)  # no meta, no request: nothing to know
    assert a.source == "none" and a.run_id is None
