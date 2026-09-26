"""Tests for the audit log and right-to-erasure.

Sensitive actions (view/search/export/delete, token management, erasure) are
recorded with the acting principal. Erasure deletes all of a user's captured calls.
"""

import httpx2 as httpx

from agenticledger.proxy.auth import ROLE_VIEWER

from .conftest import openai_response

MASTER = {"x-agenticledger-api-key": "master-key"}
_CHAT = {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]}


def _audit(client):
    return client.get("/api/audit", headers=MASTER).json()


def _actions(entries):
    return [e["action"] for e in entries]


def test_view_export_and_search_are_audited(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    client.post("/v1/chat/completions", json=_CHAT, headers={"x-agenticledger-session-id": "s1"})

    assert client.get("/session/s1", headers=MASTER).status_code == 200
    assert client.get("/export/s1", headers=MASTER).status_code == 200
    assert client.get("/api/search?q=hi", headers=MASTER).status_code == 200

    entries = _audit(client)
    acts = _actions(entries)
    assert "view_session" in acts and "export_session" in acts and "search" in acts
    view = next(e for e in entries if e["action"] == "view_session")
    assert view["target"] == "s1"
    assert view["actor_source"] == "master" and view["actor_role"] == "admin"


def test_delete_session_is_audited(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    client.post("/v1/chat/completions", json=_CHAT, headers={"x-agenticledger-session-id": "s-del"})

    deleted = client.delete("/api/sessions/s-del", headers=MASTER)
    assert deleted.status_code == 200
    entry = next(e for e in _audit(client) if e["action"] == "delete_session")
    assert entry["target"] == "s-del"


def test_token_actions_are_audited(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    created = client.post("/api/tokens", json={"name": "t", "role": "viewer"}, headers=MASTER).json()
    client.delete(f"/api/tokens/{created['token_id']}", headers=MASTER)

    acts = _actions(_audit(client))
    assert "create_token" in acts and "revoke_token" in acts


def test_token_principal_is_attributed(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    client.post("/v1/chat/completions", json=_CHAT, headers={"x-agenticledger-session-id": "s2"})
    token = client.post("/api/tokens", json={"name": "viewer-bot", "role": "viewer"},
                        headers=MASTER).json()["token"]

    # View the session using the viewer token …
    assert client.get("/session/s2", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    # … the audit entry attributes it to that token.
    view = next(e for e in _audit(client) if e["action"] == "view_session" and e["target"] == "s2")
    assert view["actor_source"] == "token"
    assert view["actor_role"] == "viewer"
    assert view["actor"] == "viewer-bot"


def test_erase_user_deletes_data_and_audits(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    for i in range(2):
        client.post("/v1/chat/completions", json=_CHAT,
                    headers={"x-agenticledger-session-id": f"u-sess-{i}", "x-agenticledger-user-id": "u1"})

    resp = client.delete("/api/users/u1", headers=MASTER)
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2

    # Data is gone.
    assert client.get("/session/u-sess-0", headers=MASTER).status_code == 404
    # And the erasure is recorded.
    entry = next(e for e in _audit(client) if e["action"] == "erase_user")
    assert entry["target"] == "u1"


def test_audit_and_erasure_require_admin(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    token = client.post("/api/tokens", json={"name": "v", "role": ROLE_VIEWER},
                        headers=MASTER).json()["token"]
    viewer = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/audit", headers=viewer).status_code == 403
    erase = client.delete("/api/users/u1", headers=viewer)
    assert erase.status_code == 403


def test_audit_can_be_disabled(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(audit_enabled=False)
    client.get("/api/search?q=x", headers=MASTER)
    assert _audit(client) == []


# ── The hash chain (0.15): tamper evidence, verification, keyed mode ─────────

def _verify(client):
    return client.get("/api/audit/verify", headers=MASTER).json()


def test_rows_are_chained_and_verify_walks_them(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    for q in ("a", "b", "c"):
        client.get(f"/api/search?q={q}", headers=MASTER)
    rows = sorted(_audit(client), key=lambda r: r["seq"])
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0]["prev_hash"] == "genesis"
    for prev, cur in zip(rows, rows[1:], strict=False):
        assert cur["prev_hash"] == prev["row_hash"]
    assert all(r["row_hash"].startswith("sha256:") for r in rows)
    result = _verify(client)
    assert result["ok"] is True and result["keyed"] is False
    assert result["checked"] >= 3 and result["first_break"] is None
    # Verifying is itself audited.
    assert "verify_audit" in _actions(_audit(client))


def test_an_edited_row_breaks_the_chain(proxy, monkeypatch, tmp_path):
    """The whole point: quietly changing a row must be visible."""
    import sqlite3

    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    db = tmp_path / "audit.db"
    client = proxy(dsn=f"sqlite:///{db}")
    for q in ("one", "two", "three"):
        client.get(f"/api/search?q={q}", headers=MASTER)
    assert _verify(client)["ok"] is True
    victim = next(r for r in _audit(client) if r["target"] == "two")
    con = sqlite3.connect(db)
    con.execute("UPDATE audit_log SET target = 'two-edited' WHERE id = ?", (victim["id"],))
    con.commit()
    con.close()
    result = _verify(client)
    assert result["ok"] is False
    assert result["first_break"]["seq"] == victim["seq"]


def test_keyed_chain_uses_hmac(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(audit_hmac_key="chain-secret")
    client.get("/api/search?q=k", headers=MASTER)
    rows = _audit(client)
    assert all(r["row_hash"].startswith("hmac-sha256:") for r in rows)
    result = _verify(client)
    assert result["ok"] is True and result["keyed"] is True


# ── Coverage: the gaps a reviewer finds first ────────────────────────────────

def test_failed_logins_and_forbidden_attempts_are_audited(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    assert client.get("/api/audit", headers={"x-agenticledger-api-key": "wrong"}).status_code == 401
    token = client.post("/api/tokens", json={"name": "v", "role": ROLE_VIEWER},
                        headers=MASTER).json()["token"]
    assert client.get("/api/audit", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    failures = [e for e in _audit(client) if e["action"] == "auth_failed"]
    assert any(e["actor_source"] == "rejected" and e["details"].startswith("401")
               and e["target"] == "/api/audit" for e in failures)
    assert any(e["actor"] == "v" and e["details"].startswith("403") for e in failures)


def test_rejected_ingest_credential_is_audited(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    resp = client.post("/v1/chat/completions", json=_CHAT,
                       headers={"x-agenticledger-ingest-key": "agl_dead_card"})
    assert resp.status_code == 403
    entry = next(e for e in _audit(client) if e["action"] == "ingest_rejected")
    assert entry["actor_source"] == "rejected" and "team card" in entry["details"]


def test_mcp_reads_are_audited_with_the_tool_name(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=MASTER)
    client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "list_sessions", "arguments": {}}}, headers=MASTER)
    targets = [e["target"] for e in _audit(client) if e["action"] == "mcp_call"]
    assert "tools/list" in targets and "list_sessions" in targets


def test_forwarded_client_address_is_recorded(proxy, monkeypatch):
    """Everyone behind nginx or a tunnel used to be logged as 127.0.0.1."""
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    client.get("/api/search?q=fwd", headers={**MASTER, "x-forwarded-for": "203.0.113.9, 10.0.0.1"})
    entry = next(e for e in _audit(client) if e["action"] == "search" and e["target"] == "fwd")
    assert entry["client"] == "203.0.113.9"


def test_reports_views_are_audited(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    assert client.get("/api/reports", headers=MASTER).status_code == 200
    assert client.get("/api/reports.csv", headers=MASTER).status_code == 200
    acts = _actions(_audit(client))
    assert "view_reports" in acts and "export_reports_csv" in acts


# ── Filters, paging, strictness, forwarding ──────────────────────────────────

def test_audit_filters_and_paging(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    for q in ("p1", "p2", "p3"):
        client.get(f"/api/search?q={q}", headers=MASTER)
    client.get("/session/none", headers=MASTER)
    only_search = client.get("/api/audit?action=search", headers=MASTER).json()
    assert only_search and all(e["action"] == "search" for e in only_search)
    by_target = client.get("/api/audit?target=p2", headers=MASTER).json()
    assert [e["target"] for e in by_target] == ["p2"]
    page1 = client.get("/api/audit?limit=2", headers=MASTER).json()
    assert len(page1) == 2
    oldest_seen = min(e["seq"] for e in page1)
    page2 = client.get(f"/api/audit?limit=2&before_seq={oldest_seen}", headers=MASTER).json()
    assert page2 and all(e["seq"] < oldest_seen for e in page2)


def test_fail_open_counts_drops_and_strict_refuses(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")

    async def broken(entry):
        raise RuntimeError("audit store down")

    lenient = proxy()
    lenient.app.state.store.add_audit = broken
    assert lenient.get("/api/search?q=x", headers=MASTER).status_code == 200
    assert lenient.app.state.audit_dropped == 1
    assert "agenticledger_audit_dropped_total 1" in lenient.get("/metrics").text
    assert "agenticledger_audit_strict 0" in lenient.get("/metrics").text

    strict = proxy(audit_strict=True)
    strict.app.state.store.add_audit = broken
    refused = strict.get("/api/search?q=x", headers=MASTER)
    assert refused.status_code == 503
    assert "AGENTICLEDGER_AUDIT_STRICT" in refused.json()["detail"]
    assert "agenticledger_audit_strict 1" in strict.get("/metrics").text


def test_strict_mode_refuses_a_mutation_before_it_happens(proxy, monkeypatch):
    """Recording must precede the effect, or strict mode is theatre."""
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()), audit_strict=True)
    client.post("/v1/chat/completions", json=_CHAT, headers={"x-agenticledger-session-id": "keep"})

    async def broken(entry):
        raise RuntimeError("audit store down")

    client.app.state.store.add_audit = broken
    assert client.delete("/api/sessions/keep", headers=MASTER).status_code == 503
    # Nothing was deleted: the record could not be written, so nothing happened.
    client.app.state.store.add_audit = type(client.app.state.store).add_audit.__get__(client.app.state.store)
    assert client.get("/session/keep", headers=MASTER).status_code == 200


def test_stdout_forwarding_emits_one_json_line_per_row(proxy, monkeypatch, capsys):
    import json as _json

    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(audit_stdout=True)
    client.get("/api/search?q=line", headers=MASTER)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    rows = [_json.loads(ln) for ln in lines]
    assert any(r["action"] == "search" and r["target"] == "line" and r["row_hash"] for r in rows)
