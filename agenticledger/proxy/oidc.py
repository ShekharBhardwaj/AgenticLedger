"""
Sign in with the company's identity provider (0.16 "Identity").

A plain OpenID Connect client, authorization code flow with PKCE, no
framework: discovery, the authorize URL, the code exchange, RS256
verification of the ID token against the provider's JWKS, and the
group-to-role mapping that decides what a signed-in person may do.

Design rules, stated once:
* The identity provider is the source of truth for who a person is and
  which groups they hold; the ledger only maps groups to its three roles.
  A person whose groups map to nothing is refused, with the reason on
  screen and in the audit trail. Nothing is visible by default.
* The ledger never brokers identity itself: no passwords, no user table
  a person can be created in by hand, no vendor control plane. A local
  test provider ships for the hand test (agenticledger idp) and says on
  every page that it is not for production.
* Every claim the ledger relies on is verified: issuer, audience, expiry,
  nonce and the RS256 signature. Other algorithms are refused by name.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlencode

import httpx2 as httpx

from .auth import ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER

# The role a group grants. Higher wins when a person holds several groups.
_ROLE_RANK = {ROLE_VIEWER: 1, ROLE_EDITOR: 2, ROLE_ADMIN: 3}
# A role map entry for "any signed-in person": an operator's deliberate
# choice, never the default.
ANY_GROUP = "*"

DEFAULT_SCOPES = "openid profile email"
DEFAULT_GROUPS_CLAIM = "groups"
DEFAULT_IDLE_HOURS = 12.0
DEFAULT_MAX_HOURS = 24.0 * 7


class OIDCError(Exception):
    """A sign-in that cannot complete, with a sentence a person can act on."""


def b64url_decode(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def parse_role_map(raw: Optional[str]) -> dict[str, str]:
    """"ledger-admins=admin,ledger-editors=editor" -> {group: role}.
    Unknown roles are refused at startup, not at the first sign-in."""
    out: dict[str, str] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"role map entry {pair!r} must be group=role")
        group, _, role = pair.partition("=")
        group, role = group.strip(), role.strip().lower()
        if role not in _ROLE_RANK:
            raise ValueError(f"role map entry {pair!r}: role must be viewer, editor or admin")
        out[group] = role
    return out


def role_for(groups: list[str], role_map: dict[str, str]) -> Optional[str]:
    """The highest role the person's groups grant, or None: refused."""
    best: Optional[str] = None
    candidates = list(groups) + [ANY_GROUP]
    for group in candidates:
        role = role_map.get(group)
        if role and (best is None or _ROLE_RANK[role] > _ROLE_RANK[best]):
            best = role
    return best


@dataclass
class OIDCConfig:
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None      # absent: public client, PKCE alone
    scopes: str = DEFAULT_SCOPES
    groups_claim: str = DEFAULT_GROUPS_CLAIM
    role_map: dict[str, str] = field(default_factory=dict)
    public_url: Optional[str] = None         # the redirect base a browser can reach
    idle_seconds: float = DEFAULT_IDLE_HOURS * 3600
    max_seconds: float = DEFAULT_MAX_HOURS * 3600
    provider_name: Optional[str] = None      # shown on the sign-in button

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.client_id)

    def problems(self) -> list[str]:
        """What stops sign-in from working, in plain words, for startup."""
        out = []
        if not self.enabled:
            return out
        if not self.role_map:
            out.append("AGENTICLEDGER_OIDC_ROLE_MAP is empty, so every sign-in would be "
                       "refused; map at least one group, e.g. ledger-admins=admin")
        if not self.public_url:
            out.append("AGENTICLEDGER_PUBLIC_URL is unset; the redirect back from the "
                       "identity provider will use the address the browser used")
        return out


def pkce_pair() -> tuple[str, str]:
    verifier = b64url_encode(secrets.token_bytes(48))
    challenge = b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


class OIDCClient:
    """Talks to one provider. Discovery and the JWKS are cached; a signing
    key the cache does not know is fetched once more before refusing."""

    def __init__(self, config: OIDCConfig, transport: Optional[httpx.AsyncBaseTransport] = None,
                 clock=time.time):
        self.config = config
        self.transport = transport   # tests route to an in-process provider
        self.clock = clock
        self._discovery: Optional[dict] = None
        self._jwks: dict[str, dict] = {}

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=10.0, transport=self.transport)

    async def discovery(self) -> dict:
        if self._discovery is None:
            url = self.config.issuer.rstrip("/") + "/.well-known/openid-configuration"
            async with self._http() as http:
                resp = await http.get(url)
            if resp.status_code != 200:
                raise OIDCError(f"the identity provider's discovery document at {url} "
                                f"answered {resp.status_code}")
            doc = resp.json()
            for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                if not doc.get(key):
                    raise OIDCError(f"the discovery document lacks {key}")
            self._discovery = doc
        return self._discovery

    async def _load_jwks(self) -> None:
        doc = await self.discovery()
        async with self._http() as http:
            resp = await http.get(doc["jwks_uri"])
        if resp.status_code != 200:
            raise OIDCError(f"the identity provider's keys at {doc['jwks_uri']} "
                            f"answered {resp.status_code}")
        self._jwks = {k.get("kid", ""): k for k in resp.json().get("keys", [])
                      if k.get("kty") == "RSA"}

    async def authorize_url(self, redirect_uri: str, state: str, nonce: str,
                            challenge: str) -> str:
        doc = await self.discovery()
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.config.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        sep = "&" if "?" in doc["authorization_endpoint"] else "?"
        return doc["authorization_endpoint"] + sep + urlencode(params)

    async def exchange(self, code: str, verifier: str, redirect_uri: str) -> dict:
        doc = await self.discovery()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.config.client_id,
            "code_verifier": verifier,
        }
        if self.config.client_secret:
            form["client_secret"] = self.config.client_secret
        async with self._http() as http:
            resp = await http.post(doc["token_endpoint"], data=form,
                                   headers={"accept": "application/json"})
        if resp.status_code != 200:
            raise OIDCError("the identity provider refused the code exchange: "
                            f"{resp.status_code} {resp.text[:200]}")
        tokens = resp.json()
        if not tokens.get("id_token"):
            raise OIDCError("the token response carries no id_token")
        return tokens

    async def verify_id_token(self, token: str, nonce: str) -> dict:
        """The claims, after every check that matters. RS256 only."""
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")
            header = json.loads(b64url_decode(header_b64))
            claims = json.loads(b64url_decode(payload_b64))
        except Exception:
            raise OIDCError("the id_token is not a well-formed JWT") from None
        alg = header.get("alg")
        if alg != "RS256":
            raise OIDCError(f"the id_token is signed with {alg!r}; only RS256 is accepted")
        kid = header.get("kid", "")
        if kid not in self._jwks:
            await self._load_jwks()
        key = self._jwks.get(kid)
        if key is None:
            raise OIDCError("the id_token's signing key is not in the provider's JWKS")
        _verify_rs256(key, f"{header_b64}.{payload_b64}".encode("ascii"), b64url_decode(sig_b64))
        doc = await self.discovery()
        issuer = doc.get("issuer") or self.config.issuer
        if claims.get("iss") != issuer:
            raise OIDCError(f"the id_token's issuer {claims.get('iss')!r} is not {issuer!r}")
        aud = claims.get("aud")
        audiences = aud if isinstance(aud, list) else [aud]
        if self.config.client_id not in audiences:
            raise OIDCError("the id_token was not issued for this client_id")
        now = self.clock()
        if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - 60:
            raise OIDCError("the id_token has expired")
        if claims.get("nonce") != nonce:
            raise OIDCError("the id_token's nonce does not match this sign-in")
        if not claims.get("sub"):
            raise OIDCError("the id_token carries no subject")
        return claims

    async def userinfo(self, access_token: str) -> dict:
        doc = await self.discovery()
        endpoint = doc.get("userinfo_endpoint")
        if not endpoint or not access_token:
            return {}
        async with self._http() as http:
            resp = await http.get(endpoint, headers={"authorization": f"Bearer {access_token}"})
        return resp.json() if resp.status_code == 200 else {}

    async def sign_in(self, code: str, verifier: str, nonce: str, redirect_uri: str) -> dict:
        """The whole exchange: returns {subject, email, name, groups, role}
        with role None when the person's groups map to nothing."""
        tokens = await self.exchange(code, verifier, redirect_uri)
        claims = await self.verify_id_token(tokens["id_token"], nonce)
        groups = claims.get(self.config.groups_claim)
        if groups is None:
            extra = await self.userinfo(tokens.get("access_token", ""))
            groups = extra.get(self.config.groups_claim)
            for k in ("email", "name"):
                claims.setdefault(k, extra.get(k))
        groups = [str(g) for g in groups] if isinstance(groups, list) else []
        return {
            "subject": str(claims["sub"]),
            "email": claims.get("email"),
            "name": claims.get("name") or claims.get("preferred_username") or claims.get("email"),
            "groups": groups,
            "role": role_for(groups, self.config.role_map),
        }


def _verify_rs256(jwk: dict, signing_input: bytes, signature: bytes) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    try:
        n = int.from_bytes(b64url_decode(jwk["n"]), "big")
        e = int.from_bytes(b64url_decode(jwk["e"]), "big")
        public = RSAPublicNumbers(e, n).public_key()
        public.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        raise OIDCError("the id_token's signature does not verify") from None
    except (KeyError, ValueError):
        raise OIDCError("the provider's signing key is malformed") from None


def display_name(config: OIDCConfig) -> str:
    """What the sign-in button says: the operator's name for the provider,
    else the issuer's host."""
    if config.provider_name:
        return config.provider_name
    host = (config.issuer or "").split("//", 1)[-1].split("/", 1)[0]
    return host or "your identity provider"


def signin_row(person: dict[str, Any]) -> dict[str, Any]:
    """The fields a person row exposes to the API (never the subject as an
    address, never internal ids beyond the one the API is keyed by)."""
    return {k: person.get(k) for k in ("id", "email", "name", "role", "groups",
                                       "created_at", "last_login_at")}
