"""
Scoped, hashed, revocable API tokens with roles.

Auth is enforced only when AGENTICLEDGER_API_KEY is set. When it is:

* the master key (``x-agenticledger-api-key`` header or ``?api_key=``) grants the
  ``admin`` role — it is the bootstrap credential used to mint tokens;
* an API token (``Authorization: Bearer agl_…``, ``x-agenticledger-token`` header,
  or ``?token=``) grants the role it was created with.

Tokens are random secrets shown once at creation; only their SHA-256 hash is
stored, so a database leak does not expose usable credentials. A token can be
revoked or given an expiry.

Roles are hierarchical:

    viewer  → read captured data (dashboard, API, export, MCP read tools)
    editor  → viewer + delete sessions
    admin   → editor + manage API tokens
    ingest  → proxy path only (a "team card": opens the relay, attributes the
              team, and can carry its own daily budget) — grants NO read access
"""

import hashlib
import secrets
from contextlib import suppress
from dataclasses import dataclass
from typing import Optional

ROLE_VIEWER = "viewer"
ROLE_EDITOR = "editor"
ROLE_ADMIN = "admin"
ROLE_INGEST = "ingest"

# Higher number = more privilege. role_satisfies() uses this ordering; ingest
# sits at 0 so a team card satisfies no read/manage requirement.
_ROLE_LEVELS = {ROLE_INGEST: 0, ROLE_VIEWER: 1, ROLE_EDITOR: 2, ROLE_ADMIN: 3}

TOKEN_PREFIX = "agl_"


def valid_role(role: str) -> bool:
    return role in _ROLE_LEVELS


def role_satisfies(have: Optional[str], need: str) -> bool:
    """True if a principal holding role ``have`` is allowed an action needing ``need``."""
    return _ROLE_LEVELS.get(have or "", 0) >= _ROLE_LEVELS.get(need, 99)


def hash_token(raw: str) -> str:
    """SHA-256 hex digest of a raw token — what gets stored and looked up."""
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). The raw token is shown to the user once."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_token(raw)


@dataclass
class Principal:
    """The authenticated identity for a request."""
    role: str
    source: str               # "open" | "master" | "token"
    token_id: Optional[str] = None
    name: Optional[str] = None


def client_is_local(host: Optional[str]) -> bool:
    """True when a request's client address is this machine itself.

    The remote guard's question: loopback callers keep the zero-config open
    dashboard, anyone else must present a key. Absent or unparseable
    addresses count as local — they mean an in-process caller (tests, ASGI
    embedding), not a network peer; a real network peer always has an IP.
    """
    if not host or host == "testclient":
        return True
    import ipaddress
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def load_or_create_remote_key(path) -> str:
    """The auto-generated key remote dashboard visitors must present when no
    AGENTICLEDGER_API_KEY is configured. Created once, kept across restarts
    (a pairing link that changes every restart is a pairing link nobody
    trusts), stored raw and chmod 0600 like an SSH key — the pairing link
    needs the real value, so a hash-only store cannot serve here."""
    from pathlib import Path
    p = Path(path)
    try:
        existing = p.read_text().strip()
        if existing.startswith(TOKEN_PREFIX):
            return existing
    except OSError:
        pass
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw + "\n")
    with suppress(OSError):
        p.chmod(0o600)
    return raw
