"""Sign in with the company's identity provider (0.16 "Identity"): the
code flow with PKCE against the built-in test provider, roles from groups,
refusal for the unmapped, cookie sign-ins with timeouts and revocation,
CSRF on mutating routes, and a real person behind every audit row."""

import time
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from agenticledger.idp import DEFAULT_CLIENT_ID, ROLE_MAP, LocalIdP
from agenticledger.proxy import app as app_module
from agenticledger.proxy.oidc import (
    OIDCClient,
    OIDCConfig,
    OIDCError,
    parse_role_map,
    role_for,
)

from .conftest import openai_response

ISSUER = "http://idp.test"


@pytest.fixture
def sso(proxy):
    """A ledger with sign-in configured against an in-process test
    provider. Returns (ledger client, provider client, provider)."""
    idp = LocalIdP(issuer=ISSUER)
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   oidc_config=OIDCConfig(issuer=ISSUER, client_id=DEFAULT_CLIENT_ID,
                                          role_map=parse_role_map(ROLE_MAP),
                                          public_url="http://testserver",
                                          provider_name="Test IdP"))
    idp_app = idp.app()
    client.app.state.oidc.transport = httpx.ASGITransport(app=idp_app)
    return client, TestClient(idp_app), idp


def sign_in(client, idp_client, user):
    """Drive the browser's three hops: ledger -> provider -> ledger."""
    start = client.get("/auth/login", follow_redirects=False)
    assert start.status_code == 302, start.text
    authorize = urlsplit(start.headers["location"])
    assert authorize.netloc == "idp.test" and authorize.path == "/authorize"
    params = {k: v[0] for k, v in parse_qs(authorize.query).items()}
    assert params["code_challenge_method"] == "S256" and params["client_id"] == DEFAULT_CLIENT_ID
    page = idp_client.get(f"/authorize?{authorize.query}")
    assert page.status_code == 200 and "never for production" in page.text
    picked = idp_client.post("/authorize/pick", data={**params, "user": user},
                             follow_redirects=False)
    assert picked.status_code == 302
    back = urlsplit(picked.headers["location"])
    assert back.netloc == "testserver" and back.path == "/auth/callback"
    return client.get(f"/auth/callback?{back.query}", follow_redirects=False)


# ── the mapping ──────────────────────────────────────────────────────────────

def test_role_map_parses_and_highest_group_wins():
    assert parse_role_map(" a=admin, b = viewer ,") == {"a": "admin", "b": "viewer"}
    assert role_for(["b", "a"], {"a": "admin", "b": "viewer"}) == "admin"
    assert role_for(["nope"], {"a": "admin"}) is None
    assert role_for([], {"*": "viewer"}) == "viewer"
    with pytest.raises(ValueError):
        parse_role_map("a=root")
    with pytest.raises(ValueError):
        parse_role_map("no-equals")


def test_config_names_what_stops_sign_in():
    off = OIDCConfig()
    assert not off.enabled and off.problems() == []
    bare = OIDCConfig(issuer=ISSUER, client_id="x")
    assert bare.enabled
    assert any("ROLE_MAP" in p for p in bare.problems())
    assert any("PUBLIC_URL" in p for p in bare.problems())


# ── the flow ─────────────────────────────────────────────────────────────────

def test_status_says_where_sign_in_starts(sso, proxy):
    client, _, _ = sso
    assert client.get("/auth/status").json() == {
        "enabled": True, "provider": "Test IdP", "login": "/auth/login"}
    plain = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    assert plain.get("/auth/status").json() == {"enabled": False, "provider": None, "login": None}
    assert plain.get("/auth/login", follow_redirects=False).status_code == 404


def test_admin_signs_in_and_is_a_person_on_every_row(sso):
    client, idp_client, _ = sso
    # Sign-in is on, so nothing is open until someone signs in.
    assert client.get("/api/whoami").status_code == 401
    done = sign_in(client, idp_client, "alice")
    assert done.status_code == 302 and done.headers["location"] == "/app"
    cookie = done.headers["set-cookie"].lower()
    assert "agenticledger_signin=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    me = client.get("/api/whoami").json()
    assert me["auth"] is True and me["role"] == "admin" and me["source"] == "sso"
    assert me["name"] == "alice@example.test" and me["person_id"]
    # A real person on the audit row.
    rows = client.get("/api/audit").json()
    login = [r for r in rows if r["action"] == "login"][0]
    assert login["actor"] == "alice@example.test" and login["actor_source"] == "sso"
    assert login["actor_role"] == "admin" and "Test IdP" in login["details"]
    people = client.get("/api/people").json()
    assert [p["email"] for p in people] == ["alice@example.test"]
    assert people[0]["role"] == "admin" and "ledger-admins" in people[0]["groups"]
    assert people[0]["active_signins"] == 1
    # The live socket rides the cookie through a ticket.
    assert client.post("/api/ws/ticket", headers={"sec-fetch-site": "same-origin"}).status_code == 200


def test_roles_follow_groups_and_the_unmapped_are_refused(sso):
    client, idp_client, _ = sso
    assert sign_in(client, idp_client, "carol").status_code == 302
    assert client.get("/api/whoami").json()["role"] == "viewer"
    # A viewer cannot mint tokens; the refusal is audited under her name.
    denied = client.post("/api/tokens", json={"name": "x", "role": "viewer"},
                         headers={"sec-fetch-site": "same-origin"})
    assert denied.status_code == 403
    client.post("/auth/logout", headers={"sec-fetch-site": "same-origin"})

    refused = sign_in(client, idp_client, "dave")
    assert refused.status_code == 403
    assert "no role in this ledger" in refused.text and "staff" in refused.text
    assert "set-cookie" not in refused.headers
    assert client.get("/api/whoami").status_code == 401
    # The refusal is on the record, with the groups the provider reported.
    assert sign_in(client, idp_client, "alice").status_code == 302
    rows = client.get("/api/audit").json()
    refusal = [r for r in rows if r["action"] == "auth_refused"][0]
    assert refusal["target"] == "dave@example.test" and "staff" in refusal["details"]
    # dave is not a person here: nothing was created for a refused sign-in.
    assert "dave@example.test" not in [p["email"] for p in client.get("/api/people").json()]


def test_logout_ends_the_sign_in(sso):
    client, idp_client, _ = sso
    sign_in(client, idp_client, "bob")
    assert client.get("/api/whoami").json()["role"] == "editor"
    out = client.post("/auth/logout", headers={"sec-fetch-site": "same-origin"})
    assert out.json() == {"signed_out": True}
    assert "agenticledger_signin=" in out.headers["set-cookie"]
    assert client.get("/api/whoami").status_code == 401
    # The trail holds the logout, read back by an admin.
    sign_in(client, idp_client, "alice")
    assert "logout" in [r["action"] for r in client.get("/api/audit").json()]


def test_cross_site_requests_with_a_cookie_are_refused(sso):
    client, idp_client, _ = sso
    sign_in(client, idp_client, "alice")
    # No Origin, no Sec-Fetch-Site: a form post from elsewhere looks like this.
    blind = client.post("/api/runs/nope/stop")
    assert blind.status_code == 403 and "own origin" in blind.json()["detail"]
    # The dashboard's own fetches say where they come from.
    own = client.post("/api/runs/nope/stop", headers={"origin": "http://testserver"})
    assert own.status_code == 404
    same = client.post("/api/runs/nope/stop", headers={"sec-fetch-site": "same-origin"})
    assert same.status_code == 404
    foreign = client.post("/api/runs/nope/stop", headers={"origin": "https://evil.example"})
    assert foreign.status_code == 403
    # Reads are never blocked by this, and the refusal is audited.
    assert client.get("/api/runs").status_code == 200
    assert "csrf_refused" in [r["action"] for r in client.get("/api/audit").json()]


def test_keys_keep_working_beside_sign_in(sso):
    client, idp_client, _ = sso
    sign_in(client, idp_client, "alice")
    token = client.post("/api/tokens", json={"name": "script", "role": "viewer"},
                        headers={"sec-fetch-site": "same-origin"}).json()["token"]
    client.post("/auth/logout", headers={"sec-fetch-site": "same-origin"})
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/runs", headers={"x-agenticledger-token": token}).status_code == 200
    # A key in a header is not an ambient credential: no origin check.
    assert client.post("/api/runs/nope/stop",
                       headers={"x-agenticledger-token": token}).status_code == 403  # viewer


def test_sign_ins_expire_idle_and_absolutely(sso, monkeypatch):
    client, idp_client, _ = sso
    sign_in(client, idp_client, "alice")
    assert client.get("/api/whoami").status_code == 200
    real = time.time
    # Thirteen idle hours later the cookie is worthless.
    monkeypatch.setattr(app_module.time, "time", lambda: real() + 13 * 3600)
    assert client.get("/api/whoami").status_code == 401
    monkeypatch.setattr(app_module.time, "time", real)
    # Kept alive by use, it still ends at the absolute limit.
    sign_in(client, idp_client, "alice")
    # Used every ten hours, inside the idle window, all week long.
    for hours in range(10, 24 * 7 - 1, 10):
        monkeypatch.setattr(app_module.time, "time", lambda h=hours: real() + h * 3600)
        assert client.get("/api/whoami").status_code == 200, hours
    monkeypatch.setattr(app_module.time, "time", lambda: real() + 24 * 7 * 3600 + 60)
    assert client.get("/api/whoami").status_code == 401


def test_admin_signs_a_person_out_everywhere(sso):
    client, idp_client, _ = sso
    sign_in(client, idp_client, "bob")
    bob_cookie = client.cookies.get("agenticledger_signin")
    client.cookies.clear()
    sign_in(client, idp_client, "alice")
    people = {p["email"]: p for p in client.get("/api/people").json()}
    ended = client.post(f"/api/people/{people['bob@example.test']['id']}/signout",
                        headers={"sec-fetch-site": "same-origin"}).json()
    assert ended["signins_ended"] == 1
    assert client.get("/api/whoami", cookies={"agenticledger_signin": bob_cookie}).status_code == 401
    assert client.post("/api/people/nobody/signout",
                       headers={"sec-fetch-site": "same-origin"}).status_code == 404
    assert "signout_all" in [r["action"] for r in client.get("/api/audit").json()]


def test_a_stale_or_forged_callback_is_refused(sso):
    client, idp_client, _ = sso
    stale = client.get("/auth/callback?code=x&state=unknown", follow_redirects=False)
    assert stale.status_code == 400 and "expired" in stale.text
    # A code the provider never issued fails the exchange, and says so.
    start = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    bad = client.get(f"/auth/callback?code=forged&state={state}", follow_redirects=False)
    assert bad.status_code == 400 and "refused the code exchange" in bad.text
    assert "set-cookie" not in bad.headers
    sign_in(client, idp_client, "alice")
    failures = [r["details"] for r in client.get("/api/audit").json() if r["action"] == "auth_failed"]
    assert any("expired" in d for d in failures) and any("code exchange" in d for d in failures)


# ── the token checks, directly ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_id_token_checks_signature_nonce_audience_and_algorithm():
    idp = LocalIdP(issuer=ISSUER)
    other = LocalIdP(issuer=ISSUER)   # a different key, same issuer
    oidc = OIDCClient(OIDCConfig(issuer=ISSUER, client_id=DEFAULT_CLIENT_ID),
                      transport=httpx.ASGITransport(app=idp.app()))
    good = await oidc.verify_id_token(idp.id_token("alice", "n1"), "n1")
    assert good["sub"] == "test-alice" and good["groups"] == ["ledger-admins", "staff"]
    with pytest.raises(OIDCError, match="nonce"):
        await oidc.verify_id_token(idp.id_token("alice", "n1"), "n2")
    with pytest.raises(OIDCError, match="signing key|signature"):
        await oidc.verify_id_token(other.id_token("alice", "n1"), "n1")
    with pytest.raises(OIDCError, match="expired"):
        await oidc.verify_id_token(idp.id_token("alice", "n1", now=time.time() - 7200), "n1")
    wrong_aud = OIDCClient(OIDCConfig(issuer=ISSUER, client_id="someone-else"),
                           transport=httpx.ASGITransport(app=idp.app()))
    with pytest.raises(OIDCError, match="client_id"):
        await wrong_aud.verify_id_token(idp.id_token("alice", "n1"), "n1")
    header, payload, sig = idp.id_token("alice", "n1").split(".")
    import base64
    import json
    forged_header = base64.urlsafe_b64encode(
        json.dumps({"alg": "none", "kid": idp.kid}).encode()).rstrip(b"=").decode()
    with pytest.raises(OIDCError, match="RS256"):
        await oidc.verify_id_token(f"{forged_header}.{payload}.", "n1")
