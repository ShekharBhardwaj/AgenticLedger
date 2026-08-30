"""#47 — names, pins, projects: human labels over stable ids."""

import httpx2 as httpx

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
    # #76 — the single-run endpoint carries the name too, so the Loop Lens
    # detail header and the sidebar tile can never disagree about identity.
    detail = client.get("/api/runs/my-run").json()
    assert detail["label"] == "the good run"


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


# ── Declared projects + auto-filing (0.8) ─────────────────────────────────────

def _capture_app(client, session_id, app_id):
    assert client.post("/v1/chat/completions",
                       json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                       headers={"x-agenticledger-session-id": session_id,
                                "x-agenticledger-app-id": app_id}).status_code == 200


def test_empty_project_exists_before_anything_is_filed(proxy):
    client = proxy(handler=_ok())
    assert client.post("/api/projects", json={"name": "Q3 rewrite"}).status_code == 201
    body = client.get("/api/projects").json()
    assert "Q3 rewrite" in body["projects"]
    assert body["bindings"] == {}


def test_app_bound_project_files_sessions_automatically_and_retroactively(proxy):
    client = proxy(handler=_ok())
    _capture_app(client, "bmad-s1", "bmad-test")     # captured BEFORE the project
    client.post("/api/projects", json={"name": "BMAD TEST", "app_id": "bmad-test"})
    _capture_app(client, "bmad-s2", "bmad-test")     # and after
    _capture_app(client, "other", "some-other-app")

    rows = {s["session_id"]: s for s in client.get("/api/sessions").json()}
    assert rows["bmad-s1"]["project"] == "BMAD TEST"   # retroactive
    assert rows["bmad-s1"]["project_auto"] is True
    assert rows["bmad-s2"]["project"] == "BMAD TEST"
    assert rows["other"]["project"] is None

    report = client.get("/api/reports?days=1").json()
    proj = {p["project"]: p for p in report["projects"]}["BMAD TEST"]
    assert proj["session_count"] == 2


def test_hand_assignment_beats_the_auto_rule(proxy):
    client = proxy(handler=_ok())
    client.post("/api/projects", json={"name": "Auto", "app_id": "app-x"})
    _capture_app(client, "s-owned", "app-x")
    client.put("/api/labels/session/s-owned", json={"project": "Hand Picked"})
    row = {s["session_id"]: s for s in client.get("/api/sessions").json()}["s-owned"]
    assert row["project"] == "Hand Picked"
    assert row["project_auto"] is False


def test_project_creation_validates(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=_ok())
    assert client.post("/api/projects", json={"name": ""},
                       headers=MASTER).status_code == 400
    assert client.post("/api/projects", json={"name": "x" * 80},
                       headers=MASTER).status_code == 400
    viewer = client.post("/api/tokens", json={"name": "v", "role": "viewer"},
                         headers=MASTER).json()["token"]
    assert client.post("/api/projects", json={"name": "np"},
                       headers={"Authorization": f"Bearer {viewer}"}).status_code == 403


def test_rename_project_moves_everything(proxy):
    client = proxy(handler=_ok())
    client.post("/api/projects", json={"name": "Old Name", "app_id": "app-r"})
    _capture_app(client, "r-auto", "app-r")
    _capture_app(client, "r-hand", "other-app")
    client.put("/api/labels/session/r-hand", json={"project": "Old Name"})

    assert client.put("/api/projects/Old%20Name",
                      json={"name": "New Name"}).status_code == 200
    rows = {s["session_id"]: s for s in client.get("/api/sessions").json()}
    assert rows["r-hand"]["project"] == "New Name"           # filed label moved
    assert rows["r-auto"]["project"] == "New Name"           # binding followed
    assert rows["r-auto"]["project_auto"] is True
    body = client.get("/api/projects").json()
    assert "New Name" in body["projects"] and "Old Name" not in body["projects"]


def test_delete_project_default_keeps_the_evidence(proxy):
    client = proxy(handler=_ok())
    client.post("/api/projects", json={"name": "Doomed", "app_id": "app-d"})
    _capture_app(client, "d-1", "app-d")
    resp = client.delete("/api/projects/Doomed").json()
    assert resp["purged"] is False and resp["sessions_deleted"] == 0
    rows = {s["session_id"]: s for s in client.get("/api/sessions").json()}
    assert "d-1" in rows                       # session survives
    assert rows["d-1"]["project"] is None      # just unfiled
    assert "Doomed" not in client.get("/api/projects").json()["projects"]


def test_delete_project_purge_takes_everything_under_it(proxy):
    client = proxy(handler=_ok())
    client.post("/api/projects", json={"name": "Purged", "app_id": "app-p"})
    _capture_app(client, "p-auto", "app-p")               # auto-filed
    _capture_app(client, "p-hand", "elsewhere")
    client.put("/api/labels/session/p-hand", json={"project": "Purged"})
    _capture_app(client, "bystander", "unrelated")

    resp = client.delete("/api/projects/Purged?purge=true").json()
    assert resp["purged"] is True and resp["sessions_deleted"] == 2
    assert resp["calls_deleted"] >= 2
    ids = {s["session_id"] for s in client.get("/api/sessions").json()}
    assert "p-auto" not in ids and "p-hand" not in ids
    assert "bystander" in ids                              # untouched
    assert "Purged" not in client.get("/api/projects").json()["projects"]


def test_project_delete_rename_validation(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=_ok())
    assert client.delete("/api/projects/nope", headers=MASTER).status_code == 404
    assert client.put("/api/projects/nope", json={"name": "x"},
                      headers=MASTER).status_code == 404
    viewer = client.post("/api/tokens", json={"name": "v", "role": "viewer"},
                         headers=MASTER).json()["token"]
    assert client.delete("/api/projects/x",
                         headers={"Authorization": f"Bearer {viewer}"}).status_code == 403


def test_filing_a_run_files_its_sessions(proxy):
    """The user's question made this a rule: a loop filed into a project
    takes its sessions with it — in the sidebar AND in Reports — losing
    only to a session's own explicit label."""
    client = proxy(handler=_ok())
    for i in range(2):
        client.post("/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                    headers={"x-agenticledger-session-id": f"iter-{i}",
                             "x-agenticledger-run-id": "the-loop"})
    client.post("/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                headers={"x-agenticledger-session-id": "unrelated"})
    client.put("/api/labels/run/the-loop", json={"project": "Loop Project"})

    rows = {s["session_id"]: s for s in client.get("/api/sessions").json()}
    assert rows["iter-0"]["project"] == "Loop Project"
    assert rows["iter-0"]["project_auto"] is True
    assert rows["iter-1"]["project"] == "Loop Project"
    assert rows["unrelated"]["project"] is None

    report = client.get("/api/reports?days=1").json()
    proj = {p["project"]: p for p in report["projects"]}["Loop Project"]
    assert proj["session_count"] == 2       # the run's cost reaches its project

    # A session's own label still wins over inheritance.
    client.put("/api/labels/session/iter-1", json={"project": "Special"})
    rows = {s["session_id"]: s for s in client.get("/api/sessions").json()}
    assert rows["iter-1"]["project"] == "Special"
    assert rows["iter-0"]["project"] == "Loop Project"


def test_purge_reaches_run_inherited_sessions(proxy):
    """Purging a project must delete the sessions filed under it via RUN
    inheritance too — the user purged a project and watched the loop and
    its sessions survive."""
    import httpx2 as _hx

    from .conftest import openai_response

    client = proxy(handler=lambda r: _hx.Response(200, json=openai_response()))
    body = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    client.post("/v1/chat/completions", json=body,
                headers={"x-agenticledger-run-id": "filed-loop",
                         "x-agenticledger-session-id": "inherited-1"})
    client.post("/v1/chat/completions", json=body,
                headers={"x-agenticledger-session-id": "unrelated"})
    # File the RUN under the project; the session inherits, nothing is
    # hand-labeled.
    client.put("/api/labels/run/filed-loop", json={"project": "doomed"})

    resp = client.delete("/api/projects/doomed?purge=true").json()
    assert resp["sessions_deleted"] == 1
    assert resp["calls_deleted"] >= 1

    # The loop's session and its calls are gone; the run tile with them.
    assert client.get("/session/inherited-1").status_code == 404
    assert "filed-loop" not in [r["run_id"] for r in client.get("/api/runs").json()]
    # The bystander survives.
    assert client.get("/session/unrelated").status_code == 200
