"""Tests for scoped, role-based API tokens (auth.py + store token CRUD + endpoints).

Auth is enforced only when AGENTICLEDGER_API_KEY is set. The master key grants admin
and bootstraps token creation; tokens grant their own role (viewer < editor < admin).
"""

import time

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from agenticledger.proxy.app import _token_is_valid
from agenticledger.proxy.auth import (
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_VIEWER,
    TOKEN_PREFIX,
    generate_token,
    hash_token,
    role_satisfies,
    valid_role,
)

from .conftest import openai_response

MASTER = {"x-agenticledger-api-key": "master-key"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mint(client, name: str, role: str) -> str:
    """Create a token via the admin API and return the raw secret."""
    resp = client.post("/api/tokens", json={"name": name, "role": role}, headers=MASTER)
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


# ── auth.py units ─────────────────────────────────────────────────────────────

def test_role_hierarchy():
    assert role_satisfies(ROLE_ADMIN, ROLE_VIEWER)
    assert role_satisfies(ROLE_EDITOR, ROLE_VIEWER)
    assert role_satisfies(ROLE_VIEWER, ROLE_VIEWER)
    assert not role_satisfies(ROLE_VIEWER, ROLE_EDITOR)
    assert not role_satisfies(ROLE_EDITOR, ROLE_ADMIN)
    assert not role_satisfies(None, ROLE_VIEWER)
    assert not role_satisfies("bogus", ROLE_VIEWER)


def test_valid_role():
    assert valid_role(ROLE_VIEWER) and valid_role(ROLE_ADMIN)
    assert not valid_role("root")


def test_token_generation_is_unique_prefixed_and_hashed():
    raw1, h1 = generate_token()
    raw2, h2 = generate_token()
    assert raw1.startswith(TOKEN_PREFIX) and raw1 != raw2
    assert h1 != h2
    assert hash_token(raw1) == h1 and len(h1) == 64  # sha256 hex


def test_token_validity_logic():
    assert _token_is_valid({"role": "viewer", "expires_at": None, "revoked_at": None})
    assert not _token_is_valid({"role": "viewer", "expires_at": time.time() - 1, "revoked_at": None})
    assert not _token_is_valid({"role": "viewer", "expires_at": None, "revoked_at": 123.0})
    assert not _token_is_valid({"role": "bogus", "expires_at": None, "revoked_at": None})


# ── Store token CRUD ──────────────────────────────────────────────────────────

async def test_store_token_crud(store):
    raw, token_hash = generate_token()
    await store.create_token("t1", "ci", token_hash, "viewer", time.time(), None)

    row = await store.get_token_by_hash(token_hash)
    assert row["token_id"] == "t1" and row["role"] == "viewer" and row["revoked_at"] is None

    listed = await store.list_tokens()
    assert len(listed) == 1
    assert "token_hash" not in listed[0]  # never expose the hash

    assert await store.revoke_token("t1", time.time()) == 1
    assert (await store.get_token_by_hash(token_hash))["revoked_at"] is not None
    assert await store.revoke_token("t1", time.time()) == 0  # already revoked → no-op
    assert await store.get_token_by_hash("does-not-exist") is None


# ── Endpoint enforcement (auth enabled via master key) ────────────────────────

def test_master_key_grants_admin(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    assert client.get("/api/sessions", headers=MASTER).status_code == 200
    assert client.get("/api/tokens", headers=MASTER).status_code == 200  # admin-only route


def test_no_or_invalid_credential_is_401(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers=_bearer("agl_not-a-real-token")).status_code == 401


def test_viewer_token_can_read_but_not_delete_or_manage(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    viewer = _mint(client, "reader", ROLE_VIEWER)

    assert client.get("/api/sessions", headers=_bearer(viewer)).status_code == 200
    # cannot delete (needs editor)
    denied = client.delete("/api/sessions/whatever", headers=_bearer(viewer))
    assert denied.status_code == 403
    # cannot manage tokens (needs admin)
    assert client.get("/api/tokens", headers=_bearer(viewer)).status_code == 403
    assert client.post("/api/tokens", json={"name": "x", "role": "viewer"},
                       headers=_bearer(viewer)).status_code == 403


def test_editor_token_can_delete(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    editor = _mint(client, "ed", ROLE_EDITOR)

    # Capture a call so there's a session to delete.
    client.post("/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                headers={"x-agenticledger-session-id": "s-del"})
    resp = client.delete("/api/sessions/s-del", headers=_bearer(editor))
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1


def test_token_is_shown_once_and_secrets_never_listed(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    created = client.post("/api/tokens", json={"name": "svc", "role": "viewer"}, headers=MASTER).json()
    assert created["token"].startswith(TOKEN_PREFIX)

    listed = client.get("/api/tokens", headers=MASTER).json()
    row = next(t for t in listed if t["token_id"] == created["token_id"])
    assert "token" not in row and "token_hash" not in row
    assert row["role"] == "viewer" and row["name"] == "svc"


def test_revoked_token_is_rejected(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    created = client.post("/api/tokens", json={"name": "tmp", "role": "viewer"}, headers=MASTER).json()
    token, token_id = created["token"], created["token_id"]

    assert client.get("/api/sessions", headers=_bearer(token)).status_code == 200
    revoked = client.delete(f"/api/tokens/{token_id}", headers=MASTER)
    assert revoked.status_code == 200
    assert client.get("/api/sessions", headers=_bearer(token)).status_code == 401


def test_create_token_validates_input(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    assert client.post("/api/tokens", json={"role": "viewer"}, headers=MASTER).status_code == 400  # no name
    assert client.post("/api/tokens", json={"name": "x", "role": "root"},
                       headers=MASTER).status_code == 400  # bad role


def test_token_via_query_param_and_x_header(proxy, monkeypatch):
    """Tokens can be presented as ?token= or x-agenticledger-token, not only Bearer."""
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    viewer = _mint(client, "q", ROLE_VIEWER)
    assert client.get(f"/api/sessions?token={viewer}").status_code == 200
    assert client.get("/api/sessions", headers={"x-agenticledger-token": viewer}).status_code == 200


def test_endpoints_open_when_auth_disabled(proxy):
    """With no master key, access is open (dev UX) — documents the default."""
    client = proxy()
    assert client.get("/api/sessions").status_code == 200
    # token management is reachable too (but tokens are not enforced while auth is off)
    assert client.get("/api/tokens").status_code == 200


# ── WebSocket auth (/ws) ──────────────────────────────────────────────────────

def test_ws_open_when_auth_disabled(proxy):
    """With no master key, the live-events socket is open (dev UX)."""
    client = proxy()
    with client.websocket_connect("/ws"):
        pass


def test_ws_rejects_missing_or_invalid_credential(proxy, monkeypatch):
    """When auth is enabled, unauthenticated connects are closed with 1008."""
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    for url in ("/ws", "/ws?api_key=wrong", "/ws?token=agl_not-a-real-token"):
        with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(url):
            pass
        assert exc.value.code == 1008, url


def test_ws_accepts_master_key_and_viewer_token(proxy, monkeypatch):
    """The same credentials the dashboard uses (?api_key= / ?token= / Bearer) work."""
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy()
    viewer = _mint(client, "ws-viewer", ROLE_VIEWER)
    with client.websocket_connect("/ws?api_key=master-key"):
        pass
    with client.websocket_connect(f"/ws?token={viewer}"):
        pass
    with client.websocket_connect("/ws", headers=_bearer(viewer)):
        pass


def test_ws_authenticated_client_receives_call_events(proxy, monkeypatch):
    """An authenticated socket still gets the live call broadcast."""
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    with client.websocket_connect("/ws?api_key=master-key") as ws:
        client.post("/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                    headers={"x-agenticledger-session-id": "s-ws"})
        event = ws.receive_json()
    assert event["type"] == "call"
    assert event["session_id"] == "s-ws"
