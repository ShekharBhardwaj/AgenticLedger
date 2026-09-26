"""
Tamper evidence for the audit log: a hash chain over its rows.

Every audit row records the hash of the row before it (prev_hash) and its
own hash (row_hash), computed over prev_hash plus the row's canonical
fields. Editing or deleting any row breaks every hash after it, and
GET /api/audit/verify walks the chain and names the first break.

Two strengths, stated plainly:

* Without a key, the chain is a SHA-256 chain. It catches accidental edits
  and unsophisticated tampering; someone with write access to the database
  can recompute every later hash.
* With AGENTICLEDGER_AUDIT_HMAC_KEY set, the chain is keyed (HMAC-SHA256),
  so a database writer without the key cannot re-chain. Forwarding rows to
  a SIEM you own (stdout JSON lines, OTLP logs) is the external anchor
  that closes the remaining gap.

Rows written before this chain existed carry no hash; verification counts
them as pre-chain rather than as breaks.
"""

import hashlib
import hmac
import json
from typing import Any, Optional

GENESIS = "genesis"
FIELDS = ("id", "timestamp", "actor_role", "actor_source", "actor",
          "action", "target", "details", "client")


def canonical(entry: dict[str, Any]) -> str:
    return json.dumps({k: entry.get(k) for k in FIELDS}, sort_keys=True,
                      separators=(",", ":"), default=str)


def row_hash(prev_hash: str, entry: dict[str, Any], key: Optional[str]) -> str:
    msg = (prev_hash + "\n" + canonical(entry)).encode("utf-8")
    if key:
        return "hmac-sha256:" + hmac.new(key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return "sha256:" + hashlib.sha256(msg).hexdigest()


def verify(rows_ascending: list[dict[str, Any]], key: Optional[str],
           pre_chain: int = 0) -> dict[str, Any]:
    """Walk chained rows (raw timestamps, ascending seq) and report."""
    expected_prev = GENESIS
    checked = 0
    first_break = None
    for r in rows_ascending:
        if r.get("row_hash") is None:
            pre_chain += 1
            continue
        if (r.get("prev_hash") != expected_prev
                or row_hash(r.get("prev_hash") or "", r, key) != r["row_hash"]):
            first_break = {"seq": r.get("seq"), "id": r.get("id"),
                           "action": r.get("action")}
            break
        expected_prev = r["row_hash"]
        checked += 1
    return {
        "ok": first_break is None,
        "checked": checked,
        "pre_chain": pre_chain,
        "first_break": first_break,
        "keyed": bool(key),
        "head": expected_prev if checked else None,
    }
