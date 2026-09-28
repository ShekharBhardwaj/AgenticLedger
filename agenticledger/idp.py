"""
A tiny OpenID Connect provider for the hand test: `agenticledger idp`.

It exists so user zero can sign in to a local ledger with a company-style
identity provider without running one. It has no passwords: the sign-in
page lists the fake people and you pick one. Every page says it is a test
provider, and it refuses to bind to anything but loopback.

Speaks exactly what the ledger's client needs: discovery, an authorize
page, PKCE-checked code exchange, RS256 ID tokens with a JWKS, userinfo.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

DEFAULT_PORT = 9400
DEFAULT_CLIENT_ID = "agenticledger-dev"
ROLE_MAP = "ledger-admins=admin,ledger-editors=editor,ledger-viewers=viewer"

# name: groups. dave holds no mapped group, so the refusal path is one
# click away in the hand test.
DEFAULT_USERS = {
    "alice": ["ledger-admins", "staff"],
    "bob": ["ledger-editors", "staff"],
    "carol": ["ledger-viewers"],
    "dave": ["staff"],
}


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass
class LocalIdP:
    issuer: str
    client_id: str = DEFAULT_CLIENT_ID
    users: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_USERS))
    codes: dict[str, dict] = field(default_factory=dict)
    access_tokens: dict[str, str] = field(default_factory=dict)
    kid: str = field(default_factory=lambda: secrets.token_hex(4))
    _key: object = None

    def __post_init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # ── keys and tokens ──────────────────────────────────────────────────

    def jwks(self) -> dict:
        numbers = self._key.public_key().public_numbers()
        n = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
        e = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
        return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid,
                          "n": _b64url(n), "e": _b64url(e)}]}

    def id_token(self, user: str, nonce: Optional[str], now: Optional[float] = None) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        now = now or time.time()
        header = {"alg": "RS256", "typ": "JWT", "kid": self.kid}
        claims = {
            "iss": self.issuer, "sub": f"test-{user}", "aud": self.client_id,
            "iat": int(now), "exp": int(now) + 600,
            "email": f"{user}@example.test", "name": user.capitalize(),
            "groups": self.users[user],
        }
        if nonce:
            claims["nonce"] = nonce
        signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
        signature = self._key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input}.{_b64url(signature)}"

    # ── the app ──────────────────────────────────────────────────────────

    def app(self) -> FastAPI:
        idp = self
        app = FastAPI(title="Agentic Ledger test identity provider", docs_url=None, redoc_url=None)

        @app.get("/.well-known/openid-configuration")
        async def discovery() -> JSONResponse:
            base = idp.issuer.rstrip("/")
            return JSONResponse({
                "issuer": idp.issuer,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "jwks_uri": f"{base}/jwks",
                "userinfo_endpoint": f"{base}/userinfo",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"],
                "scopes_supported": ["openid", "profile", "email", "groups"],
                "claims_supported": ["sub", "email", "name", "groups"],
            })

        @app.get("/jwks")
        async def jwks() -> JSONResponse:
            return JSONResponse(idp.jwks())

        @app.get("/authorize", response_class=HTMLResponse)
        async def authorize(request: Request) -> HTMLResponse:
            q = request.query_params
            if q.get("client_id") != idp.client_id:
                return HTMLResponse("<p>unknown client_id</p>", status_code=400)
            if q.get("response_type") != "code" or q.get("code_challenge_method") != "S256":
                return HTMLResponse("<p>this test provider speaks the code flow with PKCE S256 only</p>",
                                    status_code=400)
            hidden = "".join(
                f'<input type="hidden" name="{k}" value="{v}">'
                for k, v in q.items())
            buttons = "".join(
                f'<button name="user" value="{u}">{u.capitalize()}'
                f'<small>{", ".join(g) or "no groups"}</small></button>'
                for u, g in idp.users.items())
            return HTMLResponse(f"""<!doctype html><meta charset="utf-8">
<title>Test identity provider</title>
<style>body{{font:15px system-ui;margin:40px auto;max-width:420px;color:#222}}
button{{display:block;width:100%;margin:8px 0;padding:12px;font:inherit;text-align:left;
border:1px solid #bbb;border-radius:8px;background:#fafafa;cursor:pointer}}
button:hover{{background:#eef}} small{{display:block;color:#666;font-size:12px}}
.warn{{background:#fff3cd;border:1px solid #e0c060;padding:10px;border-radius:8px;font-size:13px}}</style>
<p class="warn">This is the Agentic Ledger <b>test</b> identity provider. No passwords, no real
people, never for production.</p>
<h2>Who are you today?</h2>
<form method="post" action="/authorize/pick">{hidden}{buttons}</form>""")

        async def _form(request: Request) -> dict[str, str]:
            # Plain urlencoded bodies, parsed by hand: no python-multipart.
            raw = (await request.body()).decode("utf-8", "replace")
            return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

        @app.post("/authorize/pick")
        async def pick(request: Request) -> RedirectResponse:
            form = await _form(request)
            user = str(form.get("user") or "")
            if user not in idp.users:
                return HTMLResponse("<p>unknown user</p>", status_code=400)
            code = secrets.token_urlsafe(24)
            idp.codes[code] = {
                "user": user, "nonce": form.get("nonce"), "redirect_uri": str(form.get("redirect_uri")),
                "challenge": str(form.get("code_challenge")), "issued": time.time(),
            }
            target = str(form.get("redirect_uri")) + ("&" if "?" in str(form.get("redirect_uri")) else "?")
            target += urlencode({"code": code, "state": str(form.get("state") or "")})
            return RedirectResponse(target, status_code=302)

        @app.post("/token")
        async def token(request: Request) -> JSONResponse:
            form = await _form(request)
            grant_type, code = form.get("grant_type"), form.get("code", "")
            redirect_uri, client_id = form.get("redirect_uri"), form.get("client_id")
            code_verifier = form.get("code_verifier", "")
            pending = idp.codes.pop(code, None)
            if grant_type != "authorization_code" or pending is None:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if client_id != idp.client_id or redirect_uri != pending["redirect_uri"]:
                return JSONResponse({"error": "invalid_client"}, status_code=400)
            expected = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
            if expected != pending["challenge"]:
                return JSONResponse({"error": "invalid_grant",
                                     "error_description": "PKCE verifier mismatch"}, status_code=400)
            if time.time() - pending["issued"] > 300:
                return JSONResponse({"error": "invalid_grant", "error_description": "code expired"},
                                    status_code=400)
            access = secrets.token_urlsafe(24)
            idp.access_tokens[access] = pending["user"]
            return JSONResponse({
                "access_token": access, "token_type": "Bearer", "expires_in": 600,
                "id_token": idp.id_token(pending["user"], pending["nonce"]),
            })

        @app.get("/userinfo")
        async def userinfo(request: Request) -> JSONResponse:
            auth = request.headers.get("authorization", "")
            user = idp.access_tokens.get(auth[7:]) if auth.lower().startswith("bearer ") else None
            if not user:
                return JSONResponse({"error": "invalid_token"}, status_code=401)
            return JSONResponse({"sub": f"test-{user}", "email": f"{user}@example.test",
                                 "name": user.capitalize(), "groups": idp.users[user]})

        return app


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agenticledger idp",
        description="Run the test identity provider on loopback, for signing in to a "
                    "local ledger during development. Not for production.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--user", action="append", default=[],
                        metavar="NAME:GROUP,GROUP",
                        help="add or replace a fake person (repeatable); default people are "
                             "alice (admin), bob (editor), carol (viewer), dave (no role)")
    args = parser.parse_args(argv)
    issuer = f"http://127.0.0.1:{args.port}"
    users = dict(DEFAULT_USERS)
    for spec in args.user:
        name, _, groups = spec.partition(":")
        users[name.strip()] = [g.strip() for g in groups.split(",") if g.strip()]
    idp = LocalIdP(issuer=issuer, users=users)
    print("Test identity provider (not for production) on", issuer)
    print("Point the ledger at it:")
    print(f"  AGENTICLEDGER_OIDC_ISSUER={issuer}")
    print(f"  AGENTICLEDGER_OIDC_CLIENT_ID={DEFAULT_CLIENT_ID}")
    print(f"  AGENTICLEDGER_OIDC_ROLE_MAP={ROLE_MAP}")
    print("  AGENTICLEDGER_PUBLIC_URL=http://localhost:8000   (or wherever the dashboard is)")
    print("People:", ", ".join(f"{u} ({', '.join(g) or 'no groups'})" for u, g in users.items()))
    import uvicorn
    uvicorn.run(idp.app(), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
