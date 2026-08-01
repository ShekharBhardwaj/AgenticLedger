"""#47 — names, pins, projects: human labels over stable ids."""

import httpx

from tests.conftest import openai_response

MASTER = {"x-agenticledger-api-key": "master-key"}


def _ok():
    return lambda r: httpx.Response(200, json=openai_response())


def _capture(client, session_id, run_id=None):
    headers = {"x-agenticledger-session-id": session_id}
    if run_id:
        headers["x-agenticledger-run-id"] = run_id
    assert client.post("/v1/chat/completions",
                       json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                       headers=headers).status_code == 200


def test_rename_pin_and_project_a_session(proxy):
    client = proxy(handler=_ok())
    _capture(client, "cc-boring-id")
    r = client.put("/api/labels/session/cc-boring-id",
                   json={"name": "overnight auth fix", "pinned": True,
                         "project": "checkout rewrite"})
    assert r.status_code == 200, r.text
    row = {s["session_id"]: s for s in client.get("/api/sessions").json()}["cc-boring-id"]
    assert row["label"] == "overnight auth fix"
    assert row["pinned"] is True
    assert row["project"] == "checkout rewrite"
    assert client.get("/api/projects").json()["projects"] == ["checkout rewrite"]


def test_partial_update_touches_only_given_fields(proxy):
    client = proxy(handler=_ok())
    _capture(client, "s-1")
    client.put("/api/labels/session/s-1", json={"name": "keeper", "project": "alpha"})
    client.put("/api/labels/session/s-1", json={"pinned": True})
    row = {s["session_id"]: s for s in client.get("/api/sessions").json()}["s-1"]
    assert (row["label"], row["pinned"], row["project"]) == ("keeper", True, "alpha")
    # Empty string clears a text field; pin survives.
    client.put("/api/labels/session/s-1", json={"name": ""})
    row = {s["session_id"]: s for s in client.get("/api/sessions").json()}["s-1"]
    assert (row["label"], row["pinned"], row["project"]) == (None, True, "alpha")


def test_runs_carry_labels_too(proxy):
    client = proxy(handler=_ok())
    _capture(client, "rs-1", run_id="my-run")
    _capture(client, "rs-1", run_id="my-run")
    client.put("/api/labels/run/my-run", json={"name": "the good run", "pinned": True})
    runs = {r["run_id"]: r for r in client.get("/api/runs").json()}
    assert runs["my-run"]["label"] == "the good run"
    assert runs["my-run"]["pinned"] is True


def test_label_validation_and_roles(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=_ok())
    assert client.put("/api/labels/bogus/x", json={"name": "n"},
                      headers=MASTER).status_code == 400
    assert client.put("/api/labels/session/x", json={"name": "x" * 200},
                      headers=MASTER).status_code == 400
    assert client.put("/api/labels/session/x", json={"pinned": "yes"},
                      headers=MASTER).status_code == 400
    viewer = client.post("/api/tokens", json={"name": "v", "role": "viewer"},
                         headers=MASTER).json()["token"]
    assert client.put("/api/labels/session/x", json={"name": "n"},
                      headers={"Authorization": f"Bearer {viewer}"}).status_code == 403


def test_reports_break_down_by_project(proxy):
    """#64: 'what did the checkout rewrite cost?' answerable from Reports."""
    client = proxy(handler=_ok())
    _capture(client, "proj-a1")
    _capture(client, "proj-a2")
    _capture(client, "unfiled")
    client.put("/api/labels/session/proj-a1", json={"project": "checkout rewrite"})
    client.put("/api/labels/session/proj-a2", json={"project": "checkout rewrite"})
    report = client.get("/api/reports?days=1").json()
    projects = {p["project"]: p for p in report["projects"]}
    row = projects["checkout rewrite"]
    assert row["session_count"] == 2 and row["call_count"] == 2
    assert row["cost_usd"] > 0
    assert "unfiled" not in str(report["projects"])  # unfiled sessions stay out
