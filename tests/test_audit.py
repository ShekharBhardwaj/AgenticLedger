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
