"""
Proxy-level rate limiting for LLM calls.

Limits are requests-per-minute (RPM), applied independently per dimension.
All limits are optional — only configured ones are enforced.

    AGENTICLEDGER_RATE_LIMIT_RPM          Global limit across all calls
    AGENTICLEDGER_RATE_LIMIT_SESSION_RPM  Per session_id
    AGENTICLEDGER_RATE_LIMIT_AGENT_RPM    Per agent_name
    AGENTICLEDGER_RATE_LIMIT_USER_RPM     Per user_id

When a limit is exceeded the proxy returns HTTP 429 with a JSON error body.
The agent receives it like any other upstream error — no special handling needed.

Uses a sliding 60-second window kept in memory. Limits are enforced PER PROCESS:
each worker/replica tracks its own counts, so behind N workers the effective limit
is up to N times the configured value. A single shared limit across replicas would
require a shared backend (e.g. Redis); that is intentionally out of scope here.
Idle keys are evicted as they age out, so memory stays bounded.
"""

import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

# Safety cap on the number of distinct rate-limit keys retained, in case of very
# high session/user cardinality. When exceeded, fully-aged-out windows are swept.
_MAX_TRACKED_KEYS = 50_000


@dataclass
class RateLimitConfig:
    global_rpm:  Optional[int] = None
    session_rpm: Optional[int] = None
    agent_rpm:   Optional[int] = None
    user_rpm:    Optional[int] = None

    @property
    def enabled(self) -> bool:
        return any([self.global_rpm, self.session_rpm, self.agent_rpm, self.user_rpm])


class RateLimiter:

    def __init__(self, config: RateLimitConfig, max_keys: int = _MAX_TRACKED_KEYS) -> None:
        self._config = config
        self._windows: dict[str, deque] = {}
        self._max_keys = max_keys

    def check(
        self,
        session_id: Optional[str],
        agent_name: Optional[str],
        user_id:    Optional[str],
    ) -> Optional[str]:
        """
        Returns an error message string if any limit is exceeded, else None.
        Records this request against all applicable windows on success.
        """
        if not self._config.enabled:
            return None

        now = time.monotonic()

        checks = []
        if self._config.global_rpm:
            checks.append(("global",  "__global__",           self._config.global_rpm))
        if self._config.session_rpm and session_id:
            checks.append(("session", f"session:{session_id}", self._config.session_rpm))
        if self._config.agent_rpm and agent_name:
            checks.append(("agent",   f"agent:{agent_name}",   self._config.agent_rpm))
        if self._config.user_rpm and user_id:
            checks.append(("user",    f"user:{user_id}",       self._config.user_rpm))

        # Check all windows before recording — don't partially record then reject
        for label, key, limit in checks:
            window = self._windows.get(key)
            if window is None:
                continue  # no history for this key → under limit
            _evict(window, now)
            if not window:
                del self._windows[key]  # reclaim a key that has fully aged out
                continue
            if len(window) >= limit:
                return (
                    f"Rate limit exceeded: {limit} requests/min per {label}. "
                    f"Current: {len(window)}. Retry after 60 seconds."
                )

        # All checks passed — record this request in every window
        for _, key, _ in checks:
            self._windows.setdefault(key, deque()).append(now)

        # Bound memory under pathological key cardinality (e.g. a unique session id
        # per request): periodically drop windows that have fully aged out.
        if len(self._windows) > self._max_keys:
            self._sweep(now)

        return None

    def _sweep(self, now: float) -> None:
        for key in list(self._windows.keys()):
            window = self._windows[key]
            _evict(window, now)
            if not window:
                del self._windows[key]


def _evict(window: deque, now: float) -> None:
    """Remove timestamps older than 60 seconds from the sliding window."""
    while window and now - window[0] > 60:
        window.popleft()
