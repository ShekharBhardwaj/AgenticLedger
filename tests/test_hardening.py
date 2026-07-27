"""Tests for the security/correctness hardening cluster.

- RateLimiter must bound its memory: idle keys are reclaimed, and a sweep keeps the
  tracked-key set from growing without limit under high session/user cardinality.
- export integrity must be honest: a plain SHA-256 checksum by default, upgradable to
  a tamper-evident keyed HMAC-SHA256 via AGENTICLEDGER_EXPORT_HMAC_KEY.
"""

import hashlib
import json

from agenticledger.proxy.export import build_export
from agenticledger.proxy.ratelimit import RateLimitConfig, RateLimiter


def _calls():
    return [{
        "action_id": "a1", "session_id": "s1", "model_id": "gpt-4o",
        "timestamp": "2026-01-01T00:00:00+00:00", "cost_usd": 0.1,
        "tokens_in": 10, "tokens_out": 5, "latency_ms": 12, "status_code": 200,
        "agent_name": "A", "content": "hi",
    }]


def _canonical_sha256(calls):
    return hashlib.sha256(json.dumps(calls, sort_keys=True, default=str).encode()).hexdigest()


# ── RateLimiter memory bounding ───────────────────────────────────────────────

def test_idle_key_is_reclaimed_after_window_ages_out(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("agenticledger.proxy.ratelimit.time.monotonic", lambda: clock[0])

    rl = RateLimiter(RateLimitConfig(session_rpm=100))
    assert rl.check("s", None, None) is None
    assert "session:s" in rl._windows

    clock[0] += 61  # the request ages out of the 60s window
    rl._sweep(clock[0])
    assert "session:s" not in rl._windows


def test_memory_bounded_under_high_cardinality(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("agenticledger.proxy.ratelimit.time.monotonic", lambda: clock[0])

    rl = RateLimiter(RateLimitConfig(session_rpm=100), max_keys=3)
    for i in range(5):
        rl.check(f"s{i}", None, None)
    assert len(rl._windows) == 5  # nothing has aged out yet, so all are retained

    clock[0] += 61  # every recorded request is now stale
    # A new request crosses max_keys and triggers a sweep of the aged-out keys.
    rl.check("s-new", None, None)
    assert set(rl._windows) == {"session:s-new"}


def test_reclaimed_key_still_enforces_after_reuse(monkeypatch):
    """A key reclaimed mid-check is re-created and still limits correctly."""
    clock = [1000.0]
    monkeypatch.setattr("agenticledger.proxy.ratelimit.time.monotonic", lambda: clock[0])

    rl = RateLimiter(RateLimitConfig(session_rpm=1))
    assert rl.check("s", None, None) is None       # 1st allowed
    assert rl.check("s", None, None) is not None    # 2nd blocked (limit 1)
    clock[0] += 61                                  # window slides
    assert rl.check("s", None, None) is None        # allowed again


# ── Export integrity tag ──────────────────────────────────────────────────────

def test_integrity_is_plain_sha256_by_default(monkeypatch):
    monkeypatch.delenv("AGENTICLEDGER_EXPORT_HMAC_KEY", raising=False)
    tag = build_export("s1", _calls())["export"]["integrity"]
    assert tag == "sha256:" + _canonical_sha256(_calls())


def test_integrity_is_keyed_hmac_when_configured(monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_EXPORT_HMAC_KEY", "topsecret")
    tag = build_export("s1", _calls())["export"]["integrity"]
    assert tag.startswith("hmac-sha256:")
    # An HMAC is not the plain checksum — it cannot be recomputed without the key.
    assert tag != "sha256:" + _canonical_sha256(_calls())


def test_hmac_depends_on_key_and_is_tamper_evident(monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_EXPORT_HMAC_KEY", "key-1")
    tag_k1 = build_export("s1", _calls())["export"]["integrity"]

    # Different key → different tag: an attacker can't forge it without the key.
    monkeypatch.setenv("AGENTICLEDGER_EXPORT_HMAC_KEY", "key-2")
    tag_k2 = build_export("s1", _calls())["export"]["integrity"]
    assert tag_k1 != tag_k2

    # Editing the calls changes the tag: tampering is detectable.
    tampered = _calls()
    tampered[0]["cost_usd"] = 999.0
    tag_tampered = build_export("s1", tampered)["export"]["integrity"]
    assert tag_tampered != tag_k2
