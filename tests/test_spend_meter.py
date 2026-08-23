"""0.11 spend meter — the bill, before the bill: a per-run cost ceiling
refuses calls at the proxy the moment the run's spend reaches it, survives
restarts on the label row, covers inferred runs, and clears cleanly."""

import httpx

from .conftest import openai_response

_BODY = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def _call(client, run_id, session):
    return client.post("/v1/chat/completions", json=_BODY,
                       headers={"x-agenticledger-run-id": run_id,
                                "x-agenticledger-session-id": session})


def _ok(content="ok"):
    return lambda r: httpx.Response(200, json=openai_response(content=content))


def test_ceiling_refuses_once_spend_reaches_it(proxy):
    client = proxy(handler=_ok())
    assert _call(client, "capped", "c-1").status_code == 200

    # A ceiling below what the run has already spent: the wall is up.
    client.put("/api/labels/run/capped", json={"budget_usd": 0.000001})
    refused = _call(client, "capped", "c-2")
    assert refused.status_code == 429
    body = refused.json()["error"]
    assert body["type"] == "run_ceiling_reached"
    assert "cost ceiling" in body["message"]

    # The refusal is filed amber under the run, costing nothing.
    rows = client.get("/session/c-2").json()
    assert rows[0]["error_detail"].startswith("blocked:")
    assert (rows[0]["cost_usd"] or 0) == 0

    # Raising the ceiling lifts the wall in the same render.
    client.put("/api/labels/run/capped", json={"budget_usd": 1000})
    assert _call(client, "capped", "c-3").status_code == 200

    # Clearing (0) removes it entirely.
    client.put("/api/labels/run/capped", json={"budget_usd": 0})
    assert _call(client, "capped", "c-4").status_code == 200


def test_ceiling_survives_a_restart(proxy, tmp_path):
    dsn = f"sqlite:///{tmp_path / 'meter.db'}"
    client = proxy(handler=_ok(), dsn=dsn)
    _call(client, "night", "n-1")
    client.put("/api/labels/run/night", json={"budget_usd": 0.000001})
    assert _call(client, "night", "n-2").status_code == 429

    reborn = proxy(handler=_ok(), dsn=dsn)
    refused = _call(reborn, "night", "n-3")
    assert refused.status_code == 429, "the restart dropped the ceiling"
    assert refused.json()["error"]["type"] == "run_ceiling_reached"


def test_ceiling_walls_inferred_runs_too(proxy):
    client = proxy(handler=_ok())

    def fresh(session):
        return client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "system": "You are loop worker v2.",
                  "messages": [{"role": "system", "content": "You are loop worker v2."},
                                {"role": "user", "content": "go"}]},
            headers={"x-agenticledger-session-id": session})

    fresh("i-1")
    fresh("i-2")
    run_id = next(r["run_id"] for r in client.get("/api/runs").json()
                  if r["run_id"].startswith("auto-run-"))
    client.put(f"/api/labels/run/{run_id}", json={"budget_usd": 0.000001})
    refused = fresh("i-3")
    assert refused.status_code == 429
    assert refused.json()["error"]["type"] == "run_ceiling_reached"


def test_budget_field_validation(proxy):
    client = proxy(handler=_ok())
    assert client.put("/api/labels/run/x", json={"budget_usd": -1}).status_code == 400
    assert client.put("/api/labels/session/x", json={"budget_usd": 5}).status_code == 400
    assert client.put("/api/labels/run/x", json={"budget_usd": "lots"}).status_code == 400


def test_run_detail_carries_ceiling_and_burn(proxy):
    client = proxy(handler=_ok())
    _call(client, "metered", "m-1")
    client.put("/api/labels/run/metered", json={"budget_usd": 25})
    run = client.get("/api/runs/metered").json()
    assert run["budget_usd"] == 25
    assert run["burn_last_hour_usd"] > 0  # the call just happened
