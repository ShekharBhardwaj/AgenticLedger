"""The operator kill switch: stop a run from the dashboard and its further
calls are refused at the wall — captured amber ("blocked:"), never counted
as agent errors, never forwarded upstream. Resume lifts it; a restart does
not (the marker persists)."""

import httpx

from .conftest import openai_response


def _call(client, run_id, session="ks-session"):
    return client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-agenticledger-run-id": run_id,
                 "x-agenticledger-session-id": session},
    )


def test_stopped_run_is_refused_and_filed_as_blocked(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    assert _call(client, "night-loop").status_code == 200

    assert client.post("/api/runs/night-loop/stop").json()["status"] == "stopped"

    refused = _call(client, "night-loop")
    assert refused.status_code == 429
    assert refused.json()["error"]["type"] == "run_stopped"
    # Nothing reached the mock upstream for the refused call.
    assert len([r for r in client.upstream.requests]) == 1

    rows = client.get("/session/ks-session").json()
    blocked = [r for r in rows if (r.get("error_detail") or "").startswith("blocked:")]
    assert len(blocked) == 1
    assert "blocked by the operator" in blocked[0]["error_detail"]

    # The run wears its status.
    runs = {r["run_id"]: r for r in client.get("/api/runs").json()}
    assert runs["night-loop"]["status"] == "stopped"


def test_other_runs_and_runless_calls_flow_freely(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    _call(client, "loop-a")
    client.post("/api/runs/loop-a/stop")

    assert _call(client, "loop-b", session="other").status_code == 200
    no_run = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert no_run.status_code == 200


def test_resume_lifts_the_wall(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    _call(client, "loop-r")
    client.post("/api/runs/loop-r/stop")
    assert _call(client, "loop-r").status_code == 429

    assert client.delete("/api/runs/loop-r/stop").json()["status"] == "resumed"
    assert _call(client, "loop-r").status_code == 200
    # Resume is idempotent.
    assert client.delete("/api/runs/loop-r/stop").status_code == 200


def test_stop_survives_a_restart(proxy, tmp_path):
    dsn = f"sqlite:///{tmp_path / 'ks.db'}"
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   dsn=dsn)
    _call(client, "overnight")
    client.post("/api/runs/overnight/stop")

    reborn = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   dsn=dsn)
    assert _call(reborn, "overnight").status_code == 429


def test_stop_unknown_run_is_404_and_actions_are_audited(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    assert client.post("/api/runs/never-existed/stop").status_code == 404

    _call(client, "loop-x")
    client.post("/api/runs/loop-x/stop")
    client.delete("/api/runs/loop-x/stop")
    actions = [row["action"] for row in client.get("/api/audit").json()]
    assert "run_stop" in actions and "run_resume" in actions


# ── #74: stopping INFERRED runs (the organic overnight loop) ─────────────────

def _fresh_context_call(client, session_id, system="You are loop worker v1."):
    """One iteration of an organic loop: new session, same system prompt."""
    return client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "system": system,
              "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": "continue the work"}]},
        headers={"x-agenticledger-session-id": session_id},
    )


def test_stopping_an_inferred_run_walls_its_next_iterations(proxy):
    import httpx as _hx

    from .conftest import openai_response

    client = proxy(handler=lambda r: _hx.Response(200, json=openai_response()))
    # Two fresh-context iterations establish the inferred run.
    assert _fresh_context_call(client, "iter-a").status_code == 200
    assert _fresh_context_call(client, "iter-b").status_code == 200
    runs = client.get("/api/runs").json()
    auto = next(r for r in runs if r["run_id"].startswith("auto-run-"))
    run_id = auto["run_id"]

    assert client.post(f"/api/runs/{run_id}/stop").status_code == 200

    # The next iteration arrives as a brand-new session with no run id —
    # exactly what slipped the wall in the 0.9 walkthrough. It must block.
    blocked = _fresh_context_call(client, "iter-c")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["type"] == "run_stopped"

    # The same loop's continuing session blocks too.
    assert _fresh_context_call(client, "iter-a").status_code == 429

    # An unrelated workload (different system prompt) is untouched.
    other = _fresh_context_call(client, "other-1", system="Completely different job.")
    assert other.status_code == 200

    # The refusal files under the stopped run, amber not red.
    detail = client.get("/session/iter-c").json()[0]
    assert detail["error_detail"].startswith("blocked: ")
    assert detail["run_id"] == run_id

    # Resume lifts the wall for the loop's next iteration.
    assert client.delete(f"/api/runs/{run_id}/stop").status_code == 200
    assert _fresh_context_call(client, "iter-d").status_code == 200


# ── #77/#78/#80: refusals must never launder a loop's identity ───────────────
#
# Ground truth from wiretapping `claude -p` (2026-08-06): each invocation
# fires TWO calls — the main call, whose system prompt LEADS with an
# x-anthropic-billing-header block carrying a per-invocation nonce, and a
# context companion with a DIFFERENT system prompt embedding session-unique
# UUID paths. Which call lands first is a race. These tests replay that
# exact traffic shape.

_WORKER = "You are the retest loop worker."
_COMPANION = ("Analyze the recent context. Scratchpad: "
              "/tmp/claude/{u}/scratchpad for temporary files.")


def _main_call(client, session_id, nonce):
    return client.post("/v1/messages", json={
        "model": "claude-sonnet-5", "max_tokens": 64000,
        "system": [
            {"type": "text", "text": ("x-anthropic-billing-header: "
                                      f"cc_version=2.1.220.{nonce}; cc_entrypoint=cli;")},
            {"type": "text", "text": _WORKER},
        ],
        "messages": [{"role": "user", "content": f"iteration {nonce}: continue"}],
    }, headers={"x-agenticledger-session-id": session_id})


def _companion_call(client, session_id, sess_uuid):
    return client.post("/v1/messages", json={
        "model": "claude-sonnet-5", "max_tokens": 64000,
        "system": _COMPANION.format(u=sess_uuid),
        "messages": [{"role": "user", "content": "<system-reminder>context</system-reminder>"}],
    }, headers={"x-agenticledger-session-id": session_id})


def _the_auto_run(client):
    return next(r["run_id"] for r in client.get("/api/runs").json()
                if r["run_id"].startswith("auto-run-"))


def _anthropic_ok():
    import httpx as _hx

    from .conftest import anthropic_response
    return lambda r: _hx.Response(200, json=anthropic_response())


def test_salted_prompts_and_call_order_still_group_one_run(proxy):
    """#80 — per-invocation billing nonces, session-unique UUID paths, and
    the main/companion arrival race fragmented one loop into three runs.
    All of it must group into a single inferred run."""
    client = proxy(handler=_anthropic_ok())
    assert _main_call(client, "s1", "aaa").status_code == 200
    assert _companion_call(client, "s1", "11111111-1111-4111-8111-111111111111").status_code == 200
    # Invocation 2 loses the race: the companion lands first.
    assert _companion_call(client, "s2", "22222222-2222-4222-8222-222222222222").status_code == 200
    assert _main_call(client, "s2", "bbb").status_code == 200
    assert _main_call(client, "s3", "ccc").status_code == 200

    run_ids = set()
    for sid in ("s1", "s2", "s3"):
        run_ids.update(r["run_id"] for r in client.get(f"/session/{sid}").json())
    assert len(run_ids) == 1, f"one loop, one run — got {run_ids}"
    assert next(iter(run_ids)).startswith("auto-run-")


def test_wall_holds_against_retries_and_companion_calls(proxy):
    """#77 — the observed leak: block the main call, and 1.5s later the
    client's retry (or the companion) sailed through under a fresh
    identity. Every follow-up of a walled session must hit the wall."""
    client = proxy(handler=_anthropic_ok())
    _main_call(client, "k1", "aaa")
    _main_call(client, "k2", "bbb")
    run_id = _the_auto_run(client)
    client.post(f"/api/runs/{run_id}/stop")
    upstream_before = len(client.upstream.requests)

    assert _main_call(client, "k3", "ccc").status_code == 429
    assert _companion_call(client, "k3", "33333333-3333-4333-8333-333333333333").status_code == 429
    assert _main_call(client, "k3", "ccc").status_code == 429
    # Nothing reached upstream; nothing was billed.
    assert len(client.upstream.requests) == upstream_before

    # Every refusal filed under the walled run, not a fresh identity.
    rows = client.get("/session/k3").json()
    assert len(rows) == 3
    assert {r["run_id"] for r in rows} == {run_id}

    # #79 — the wall's aggregate row is amber (blocked), never red (error).
    iters = client.get(f"/api/runs/{run_id}/iterations").json()
    walled = [i for i in iters if i["blocked_calls"]]
    assert walled and all(i["error_calls"] == 0 for i in walled)


def test_resume_restores_the_loops_identity(proxy):
    """#78 — observed live: the post-resume iteration answered fine but
    landed in a brand-new auto-run; the resumed run looked dead. The next
    iteration must continue the SAME run."""
    client = proxy(handler=_anthropic_ok())
    _main_call(client, "r1", "aaa")
    _main_call(client, "r2", "bbb")
    run_id = _the_auto_run(client)
    client.post(f"/api/runs/{run_id}/stop")
    assert _main_call(client, "r3", "ccc").status_code == 429  # knocks while walled
    client.delete(f"/api/runs/{run_id}/stop")

    assert _main_call(client, "r4", "ddd").status_code == 200
    rows = client.get("/session/r4").json()
    assert rows and all(r["run_id"] == run_id for r in rows)


def test_simultaneous_loops_block_and_resume_independently(proxy):
    """Two loops interleaved (the untested scenario the user called out):
    walling one must not touch the other's flow, grouping, or identity."""
    client = proxy(handler=_anthropic_ok())

    def loop_b(session_id, n):
        return client.post("/v1/messages", json={
            "model": "claude-sonnet-5", "max_tokens": 64000,
            "system": "You are loop B, the nightly summarizer.",
            "messages": [{"role": "user", "content": f"pass {n}"}],
        }, headers={"x-agenticledger-session-id": session_id})

    _main_call(client, "a1", "aaa")
    loop_b("b1", 1)
    _main_call(client, "a2", "bbb")
    loop_b("b2", 2)
    runs = {r["run_id"] for r in client.get("/api/runs").json()
            if r["run_id"].startswith("auto-run-")}
    assert len(runs) == 2
    run_a = client.get("/session/a1").json()[0]["run_id"]
    run_b = client.get("/session/b1").json()[0]["run_id"]
    assert run_a != run_b

    client.post(f"/api/runs/{run_a}/stop")
    assert _main_call(client, "a3", "ccc").status_code == 429
    assert loop_b("b3", 3).status_code == 200
    assert client.get("/session/b3").json()[0]["run_id"] == run_b

    client.delete(f"/api/runs/{run_a}/stop")
    assert _main_call(client, "a4", "ddd").status_code == 200
    assert client.get("/session/a4").json()[0]["run_id"] == run_a
    # Loop B never blinked.
    assert loop_b("b4", 4).status_code == 200
    assert client.get("/session/b4").json()[0]["run_id"] == run_b


# ── #100: inferred-run identity survives restarts ────────────────────────────
#
# The Bedrock E2E hit this three times in one day: the signature table
# lived in RAM, so every proxy restart orphaned auto-detected runs — the
# next iteration opened a fresh tile and sailed past the old tile's wall.
# Signatures now write through to the store and reload at boot.

def test_inferred_run_identity_survives_a_restart(proxy, tmp_path):
    import httpx as _hx

    from .conftest import openai_response

    dsn = f"sqlite:///{tmp_path / 'sig.db'}"
    client = proxy(handler=lambda r: _hx.Response(200, json=openai_response()),
                   dsn=dsn)
    assert _fresh_context_call(client, "life-1").status_code == 200
    assert _fresh_context_call(client, "life-2").status_code == 200
    runs = [r for r in client.get("/api/runs").json()
            if r["run_id"].startswith("auto-run-")]
    assert len(runs) == 1
    run_id = runs[0]["run_id"]
    assert runs[0]["iterations"] == 2

    reborn = proxy(handler=lambda r: _hx.Response(200, json=openai_response()),
                   dsn=dsn)
    assert _fresh_context_call(reborn, "life-3").status_code == 200
    runs = {r["run_id"]: r for r in reborn.get("/api/runs").json()}
    assert set(k for k in runs if k.startswith("auto-run-")) == {run_id}, (
        "the restart minted a new tile instead of continuing the run")
    assert runs[run_id]["iterations"] == 3


def test_wall_on_inferred_run_survives_a_restart(proxy, tmp_path):
    import httpx as _hx

    from .conftest import openai_response

    dsn = f"sqlite:///{tmp_path / 'sigwall.db'}"
    client = proxy(handler=lambda r: _hx.Response(200, json=openai_response()),
                   dsn=dsn)
    _fresh_context_call(client, "w-1")
    _fresh_context_call(client, "w-2")
    run_id = next(r["run_id"] for r in client.get("/api/runs").json()
                  if r["run_id"].startswith("auto-run-"))
    assert client.post(f"/api/runs/{run_id}/stop").json()["status"] == "stopped"

    reborn = proxy(handler=lambda r: _hx.Response(200, json=openai_response()),
                   dsn=dsn)
    refused = _fresh_context_call(reborn, "w-3")
    assert refused.status_code == 429, (
        "the restart let the loop launder past the wall")
    assert refused.json()["error"]["type"] == "run_stopped"


def test_blocked_knock_files_as_the_next_iteration(proxy):
    """The amber tower stands where the loop was stopped: a refused
    fresh-context knock numbers itself as the iteration it was attempting
    (here: 3, after two real ones), never a "?" bucket."""
    import httpx as _hx

    from .conftest import openai_response

    client = proxy(handler=lambda r: _hx.Response(200, json=openai_response()))
    _fresh_context_call(client, "seq-1")
    _fresh_context_call(client, "seq-2")
    run_id = next(r["run_id"] for r in client.get("/api/runs").json()
                  if r["run_id"].startswith("auto-run-"))
    client.post(f"/api/runs/{run_id}/stop")

    assert _fresh_context_call(client, "seq-3").status_code == 429
    blocked = client.get("/session/seq-3").json()
    assert len(blocked) == 1
    assert blocked[0]["error_detail"].startswith("blocked:")
    assert blocked[0]["iteration"] == 3
    assert blocked[0]["run_id"] == run_id

    # A retry in the same refused session keeps the number, not a new one.
    assert _fresh_context_call(client, "seq-3").status_code == 429
    rows = client.get("/session/seq-3").json()
    assert [r["iteration"] for r in rows] == [3, 3]
