"""Scoped access (0.16, second half): a signed-in person whose groups map
to projects sees exactly those projects, everywhere the ledger reads."""

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from agenticledger.idp import DEFAULT_CLIENT_ID, LocalIdP
from agenticledger.proxy.oidc import OIDCConfig, parse_role_map, parse_scope_map, projects_for

from .conftest import openai_response
from .test_oidc import ISSUER, sign_in

ROLE_MAP = "ledger-admins=admin,ledger-editors=editor,ledger-viewers=viewer,team-alpha=editor"
SCOPE_MAP = "team-alpha=alpha,team-alpha=alpha-infra"


def test_scope_map_parses_and_grants_the_union():
    m = parse_scope_map(SCOPE_MAP + ", platform=beta")
    assert m == {"team-alpha": {"alpha", "alpha-infra"}, "platform": {"beta"}}
    assert projects_for(["team-alpha", "staff"], m) == ["alpha", "alpha-infra"]
    assert projects_for(["team-alpha", "platform"], m) == ["alpha", "alpha-infra", "beta"]
    # No scoped group at all: unscoped, sees everything the role allows.
    assert projects_for(["staff"], m) is None
    assert projects_for(["team-alpha"], {}) is None
    with pytest.raises(ValueError):
        parse_scope_map("no-equals")


@pytest.fixture
def world(proxy):
    """A ledger with work filed under alpha, beta and nothing, and a
    provider whose 'erin' is in team-alpha (editor, scoped to alpha and
    alpha-infra) and 'alice' is an unscoped admin."""
    idp = LocalIdP(issuer=ISSUER)
    idp.users["erin"] = ["team-alpha", "staff"]
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   oidc_config=OIDCConfig(issuer=ISSUER, client_id=DEFAULT_CLIENT_ID,
                                          role_map=parse_role_map(ROLE_MAP),
                                          scope_map=parse_scope_map(SCOPE_MAP),
                                          public_url="http://testserver"))
    idp_app = idp.app()
    client.app.state.oidc.transport = httpx.ASGITransport(app=idp_app)
    idp_client = TestClient(idp_app)

    def call(session, run_id, text="hello there"):
        headers = {"x-agenticledger-session-id": session, "x-agenticledger-run-id": run_id}
        assert client.post("/v1/chat/completions",
                           json={"model": "gpt-4o",
                                 "messages": [{"role": "user", "content": text}]},
                           headers=headers).status_code == 200

    call("s-alpha", "run-alpha", "alpha secret handshake")
    call("s-beta", "run-beta", "beta secret handshake")
    call("s-none", "run-none", "nobody filed this one")
    call("s-inherit", "run-alpha", "inherited from the run")
    # Filing: the run alpha is filed (its sessions inherit), s-beta by hand,
    # and s-none stays unfiled.
    sign_in(client, idp_client, "alice")
    same = {"sec-fetch-site": "same-origin"}
    assert client.put("/api/labels/run/run-alpha", json={"project": "alpha"},
                      headers=same).status_code == 200
    assert client.put("/api/labels/session/s-beta", json={"project": "beta"},
                      headers=same).status_code == 200
    ids = {r["session_id"]: r for r in client.get("/session/s-alpha").json()}
    client.post("/auth/logout", headers=same)
    client.cookies.clear()
    return client, idp_client, next(iter(ids))


def _erin(world):
    client, idp_client, _ = world
    assert sign_in(client, idp_client, "erin").status_code == 302
    me = client.get("/api/whoami").json()
    assert me["role"] == "editor" and me["projects"] == ["alpha", "alpha-infra"]
    return client


def test_lists_show_only_the_scope_and_unfiled_stays_hidden(world):
    client = _erin(world)
    sessions = client.get("/api/sessions")
    assert sorted(s["session_id"] for s in sessions.json()) == ["s-alpha", "s-inherit"]
    assert sessions.headers["x-total-count"] == "2"
    runs = client.get("/api/runs")
    assert [r["run_id"] for r in runs.json()] == ["run-alpha"]
    assert client.get("/api/projects").json()["projects"] == ["alpha"]
    # An unscoped admin sees all of it.
    client.cookies.clear()
    world_client, idp_client, _ = world
    sign_in(world_client, idp_client, "alice")
    assert world_client.get("/api/sessions").headers["x-total-count"] == "4"
    assert sorted(world_client.get("/api/projects").json()["projects"]) == ["alpha", "beta"]


def test_single_reads_outside_the_scope_are_not_found(world):
    client = _erin(world)
    assert client.get("/session/s-alpha").status_code == 200
    assert client.get("/session/s-inherit").status_code == 200
    for sid in ("s-beta", "s-none"):
        assert client.get(f"/session/{sid}").status_code == 404, sid
        assert client.get(f"/export/{sid}").status_code == 404, sid
        assert client.get(f"/export/{sid}/report").status_code == 404, sid
        assert client.get(f"/api/sessions/{sid}/tools").status_code == 404, sid
        assert client.get(f"/api/sessions/{sid}/loop-block").status_code == 404, sid
    assert client.get("/api/runs/run-alpha").status_code == 200
    for rid in ("run-beta", "run-none"):
        assert client.get(f"/api/runs/{rid}").status_code == 404, rid
        assert client.get(f"/api/runs/{rid}/iterations").status_code == 404, rid
        assert client.get(f"/api/runs/{rid}/flags").status_code == 404, rid
        assert client.get(f"/api/runs/{rid}/cache-audit").status_code == 404, rid
    assert client.get("/api/whatif?model=gpt-4o&session_id=s-alpha").status_code == 200
    assert client.get("/api/whatif?model=gpt-4o&session_id=s-beta").status_code == 404


def test_calls_search_and_mcp_follow_the_scope(world):
    client = _erin(world)
    beta_call = client.get("/api/search?q=beta+secret").json()
    assert beta_call == []
    alpha_hits = client.get("/api/search?q=secret+handshake").json()
    assert {h["session_id"] for h in alpha_hits} == {"s-alpha"}
    action = alpha_hits[0]["action_id"]
    assert client.get(f"/explain/{action}").status_code == 200
    assert client.get(f"/api/calls/{action}").status_code == 200
    # A call from an unscoped session, found by an unscoped reader, is
    # not found by erin.
    client.cookies.clear()
    world_client, idp_client, _ = world
    sign_in(world_client, idp_client, "alice")
    beta_action = world_client.get("/api/search?q=beta+secret").json()[0]["action_id"]
    world_client.cookies.clear()
    sign_in(world_client, idp_client, "erin")
    assert world_client.get(f"/explain/{beta_action}").status_code == 404
    assert world_client.get(f"/api/calls/{beta_action}").status_code == 404

    def mcp(name, **args):
        return world_client.post("/mcp", headers={"sec-fetch-site": "same-origin"}, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args}}).json()

    listed = mcp("list_sessions")["result"]["content"][0]["text"]
    assert "s-alpha" in listed and "s-beta" not in listed and "s-none" not in listed
    runs = mcp("list_runs")["result"]["content"][0]["text"]
    assert "run-alpha" in runs and "run-beta" not in runs
    assert "error" in mcp("get_session", session_id="s-beta")
    assert "error" in mcp("get_run_status", run_id="run-none")
    assert "error" in mcp("explain", action_id=beta_action)
    assert "No results" in mcp("search", query="beta secret")["result"]["content"][0]["text"]


def test_reports_count_only_the_scope(world):
    client = _erin(world)
    report = client.get("/api/reports?days=1").json()
    assert report["totals"]["call_count"] == 2
    assert [p["project"] for p in report["projects"]] == ["alpha"]
    client.cookies.clear()
    world_client, idp_client, _ = world
    sign_in(world_client, idp_client, "alice")
    assert world_client.get("/api/reports?days=1").json()["totals"]["call_count"] == 4


def test_mutations_outside_the_scope_are_refused(world):
    client = _erin(world)
    same = {"sec-fetch-site": "same-origin"}
    assert client.post("/api/runs/run-beta/stop", headers=same).status_code == 404
    assert client.post("/api/runs/run-none/end", headers=same).status_code == 404
    assert client.delete("/api/sessions/s-beta", headers=same).status_code == 404
    assert client.put("/api/labels/session/s-beta", json={"name": "x"},
                      headers=same).status_code == 404
    # Inside the scope, work; but filing under a project outside it is refused.
    assert client.post("/api/runs/run-alpha/stop", headers=same).status_code == 200
    assert client.put("/api/labels/session/s-alpha", json={"name": "mine"},
                      headers=same).status_code == 200
    moved = client.put("/api/labels/session/s-alpha", json={"project": "beta"}, headers=same)
    assert moved.status_code == 403 and "your scope" in moved.json()["detail"]
    assert client.put("/api/labels/session/s-alpha", json={"project": "alpha-infra"},
                      headers=same).status_code == 200
    # The audit trail names the scope on the login row.
    client.cookies.clear()
    world_client, idp_client, _ = world
    sign_in(world_client, idp_client, "alice")
    login = [r for r in world_client.get("/api/audit").json()
             if r["action"] == "login" and r["actor"] == "erin@example.test"][0]
    assert "scope=alpha,alpha-infra" in login["details"]
    people = {p["email"]: p for p in world_client.get("/api/people").json()}
    assert people["erin@example.test"]["projects"] == ["alpha", "alpha-infra"]
    assert people["alice@example.test"]["projects"] is None


def test_keys_and_the_open_dashboard_stay_unscoped(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    assert client.post("/v1/chat/completions",
                       json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                       headers={"x-agenticledger-session-id": "open"}).status_code == 200
    me = client.get("/api/whoami").json()
    assert me["source"] == "open" and me.get("projects") is None
    assert client.get("/session/open").status_code == 200
