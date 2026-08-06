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
