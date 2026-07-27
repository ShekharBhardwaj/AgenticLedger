"""Unit tests for agenticledger.proxy.ratelimit (RateLimiter + RateLimitConfig).

These are pure unit tests: time.monotonic is monkeypatched so the 60-second
sliding window can be driven deterministically without sleeping.

The intended contract (from the module docstring):
- Limits are requests-per-minute, applied independently per dimension.
- Only configured (non-None) limits are enforced.
- check() returns an error message string when a limit is exceeded, else None,
  and records the request against every applicable window only on success.
- A 60-second sliding window: once timestamps age out, calls are allowed again.
"""

import time

import pytest

from agenticledger.proxy.ratelimit import RateLimitConfig, RateLimiter

# ── Time control helper ───────────────────────────────────────────────────────

class FakeClock:
    """A controllable stand-in for time.monotonic."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    """Patch time.monotonic (the symbol ratelimit imports) with a FakeClock."""
    c = FakeClock()
    monkeypatch.setattr(time, "monotonic", c)
    return c


def _window_len(limiter: RateLimiter, key: str) -> int:
    """Number of recorded timestamps in a given internal window (no eviction)."""
    return len(limiter._windows[key])


# ── RateLimitConfig.enabled ───────────────────────────────────────────────────

class TestConfigEnabled:
    def test_disabled_when_all_none(self):
        """enabled is False when no limit is configured."""
        assert RateLimitConfig().enabled is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"global_rpm": 5},
            {"session_rpm": 5},
            {"agent_rpm": 5},
            {"user_rpm": 5},
        ],
    )
    def test_enabled_when_any_set(self, kwargs):
        """enabled is True when any single dimension is configured."""
        assert RateLimitConfig(**kwargs).enabled is True

    def test_enabled_when_multiple_set(self):
        """enabled is True when several dimensions are configured."""
        assert RateLimitConfig(global_rpm=1, user_rpm=2).enabled is True


# ── Disabled limiter ──────────────────────────────────────────────────────────

class TestDisabledLimiter:
    def test_check_always_none_when_disabled(self, clock):
        """A limiter with no configured limits never rejects."""
        limiter = RateLimiter(RateLimitConfig())
        for _ in range(1000):
            assert limiter.check("s", "a", "u") is None

    def test_disabled_records_nothing(self, clock):
        """A disabled limiter does not accumulate window state."""
        limiter = RateLimiter(RateLimitConfig())
        for _ in range(10):
            limiter.check("s", "a", "u")
        # No windows should have been touched/recorded.
        assert all(len(w) == 0 for w in limiter._windows.values())


# ── Global RPM window behavior ────────────────────────────────────────────────

class TestGlobalRpm:
    def test_first_n_allowed_then_rejected(self, clock):
        """global_rpm=N allows the first N calls and rejects the (N+1)th."""
        n = 3
        limiter = RateLimiter(RateLimitConfig(global_rpm=n))
        for _ in range(n):
            assert limiter.check(None, None, None) is None
        rejected = limiter.check(None, None, None)
        assert rejected is not None

    def test_rejection_message_mentions_limit(self, clock):
        """The rejection string mentions the configured limit and the dimension."""
        n = 2
        limiter = RateLimiter(RateLimitConfig(global_rpm=n))
        for _ in range(n):
            limiter.check(None, None, None)
        msg = limiter.check(None, None, None)
        assert msg is not None
        assert str(n) in msg
        assert "global" in msg

    def test_window_slides_after_60s(self, clock):
        """After the 60s window passes, previously-counted calls are forgotten."""
        n = 2
        limiter = RateLimiter(RateLimitConfig(global_rpm=n))
        for _ in range(n):
            assert limiter.check(None, None, None) is None
        assert limiter.check(None, None, None) is not None  # at the cap

        # Advance beyond the window so all earlier timestamps age out (> 60).
        clock.advance(60.001)
        for _ in range(n):
            assert limiter.check(None, None, None) is None
        assert limiter.check(None, None, None) is not None

    def test_partial_window_slide(self, clock):
        """Only timestamps older than 60s are evicted; recent ones still count."""
        limiter = RateLimiter(RateLimitConfig(global_rpm=2))
        assert limiter.check(None, None, None) is None  # t=0 in window
        clock.advance(40)
        assert limiter.check(None, None, None) is None  # window now full (2)
        assert limiter.check(None, None, None) is not None  # rejected at cap

        # Advance 21s more: total 61s since first call (evicted), 21s since second.
        clock.advance(21)
        # First timestamp (now 61s old) is evicted; one slot frees up.
        assert limiter.check(None, None, None) is None
        # Now full again (the 40s-mark call + this one).
        assert limiter.check(None, None, None) is not None

    def test_rejection_does_not_grow_window(self, clock):
        """A rejected call is not itself recorded in the window."""
        limiter = RateLimiter(RateLimitConfig(global_rpm=2))
        limiter.check(None, None, None)
        limiter.check(None, None, None)
        assert _window_len(limiter, "__global__") == 2
        # Several rejected attempts must not increase the count.
        for _ in range(5):
            assert limiter.check(None, None, None) is not None
        assert _window_len(limiter, "__global__") == 2


# ── Per-dimension independence ────────────────────────────────────────────────

class TestSessionRpm:
    def test_distinct_sessions_independent(self, clock):
        """session_rpm counts each session_id separately."""
        limiter = RateLimiter(RateLimitConfig(session_rpm=2))
        assert limiter.check("alice", None, None) is None
        assert limiter.check("alice", None, None) is None
        assert limiter.check("alice", None, None) is not None  # alice capped
        # bob is unaffected by alice's usage.
        assert limiter.check("bob", None, None) is None
        assert limiter.check("bob", None, None) is None
        assert limiter.check("bob", None, None) is not None  # bob now capped

    def test_none_session_never_limited(self, clock):
        """A None session_id is never counted/limited under session_rpm."""
        limiter = RateLimiter(RateLimitConfig(session_rpm=1))
        for _ in range(100):
            assert limiter.check(None, None, None) is None
        # No session window should have accumulated anything.
        assert all(not k.startswith("session:") for k in limiter._windows
                   if len(limiter._windows[k]) > 0)


class TestAgentRpm:
    def test_distinct_agents_independent(self, clock):
        """agent_rpm counts each agent_name separately."""
        limiter = RateLimiter(RateLimitConfig(agent_rpm=1))
        assert limiter.check(None, "planner", None) is None
        assert limiter.check(None, "planner", None) is not None  # planner capped
        assert limiter.check(None, "executor", None) is None  # different agent ok

    def test_none_agent_never_limited(self, clock):
        """A None agent_name is never counted/limited under agent_rpm."""
        limiter = RateLimiter(RateLimitConfig(agent_rpm=1))
        for _ in range(50):
            assert limiter.check(None, None, None) is None


class TestUserRpm:
    def test_distinct_users_independent(self, clock):
        """user_rpm counts each user_id separately."""
        limiter = RateLimiter(RateLimitConfig(user_rpm=1))
        assert limiter.check(None, None, "u1") is None
        assert limiter.check(None, None, "u1") is not None  # u1 capped
        assert limiter.check(None, None, "u2") is None  # different user ok

    def test_none_user_never_limited(self, clock):
        """A None user_id is never counted/limited under user_rpm."""
        limiter = RateLimiter(RateLimitConfig(user_rpm=1))
        for _ in range(50):
            assert limiter.check(None, None, None) is None


# ── Atomicity across dimensions ───────────────────────────────────────────────

class TestAtomicity:
    def test_rejection_on_one_dim_does_not_record_others(self, clock):
        """When one dimension is at its cap, a rejected check must not record the
        request against the other still-under-limit dimensions."""
        # global capped at 1, session allowed up to 100.
        limiter = RateLimiter(RateLimitConfig(global_rpm=1, session_rpm=100))

        # First call: both global and session record one timestamp.
        assert limiter.check("s1", None, None) is None
        assert _window_len(limiter, "__global__") == 1
        assert _window_len(limiter, "session:s1") == 1

        # Second call: global is at its cap -> rejected. Session is still under
        # its limit but must NOT be recorded because the whole check failed.
        assert limiter.check("s1", None, None) is not None
        assert _window_len(limiter, "session:s1") == 1  # unchanged
        assert _window_len(limiter, "__global__") == 1  # unchanged

    def test_under_limit_dim_unchanged_when_later_dim_rejects(self, clock):
        """If an earlier-checked dimension is under limit but a later-checked one
        is over, the earlier window length is left unchanged after rejection."""
        # global checked first (under limit, cap 100); user checked later (cap 1).
        limiter = RateLimiter(RateLimitConfig(global_rpm=100, user_rpm=1))

        assert limiter.check(None, None, "bob") is None
        assert _window_len(limiter, "__global__") == 1
        assert _window_len(limiter, "user:bob") == 1

        # user 'bob' is now capped -> this check rejects. global (under limit)
        # must not gain a timestamp from the rejected request.
        assert limiter.check(None, None, "bob") is not None
        assert _window_len(limiter, "__global__") == 1  # not recorded
        assert _window_len(limiter, "user:bob") == 1

    def test_all_dims_recorded_on_success(self, clock):
        """A successful check records the request in every configured+present
        dimension window simultaneously."""
        limiter = RateLimiter(
            RateLimitConfig(global_rpm=5, session_rpm=5, agent_rpm=5, user_rpm=5)
        )
        assert limiter.check("s", "a", "u") is None
        assert _window_len(limiter, "__global__") == 1
        assert _window_len(limiter, "session:s") == 1
        assert _window_len(limiter, "agent:a") == 1
        assert _window_len(limiter, "user:u") == 1
