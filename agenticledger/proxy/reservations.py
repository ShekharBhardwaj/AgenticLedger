"""
Admission-time budget reservations (#124): the wall holds under concurrency.

Budget and run-ceiling checks read RECORDED spend. Concurrent in-flight
calls each saw the same remaining room and all passed, so a burst could
overshoot a cap by about concurrency times one call's cost. A reservation
is an estimate held from admission until the real cost is recorded (or the
call fails, is dropped, or is refused), so concurrent callers see each
other.

Semantics, decided 2026-09-26:

* recorded spend plus in-flight reservations is compared against the cap;
  the one call that crosses the line still goes through, exactly as a
  single caller always did, so overshoot is bounded to one call's cost;
* the estimate is the request text at four chars per token (the cache
  audit's stated method) plus the request's max_tokens (a conservative
  default when absent), priced like any call. Over-reserving is the safe
  direction; the record corrects it the moment the real cost lands;
* an unpriced model reserves nothing and is not counted, as before. The
  policy is stated on the settings page, and
  AGENTICLEDGER_BUDGET_UNPRICED=refuse turns such calls away instead.

The ledger is per process (one replica). The platform cycle moves it to
shared rows; the interface here stays.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

from .pricing import compute_cost

_CHARS_PER_TOKEN = 4              # the cache audit's stated estimate method
DEFAULT_MAX_OUTPUT_TOKENS = 1024  # when the request names no max_tokens

# ("session", id) | ("agent", name) | ("user", id) | ("team", name)
# | ("daily", "*") | ("run", id)
Key = tuple[str, str]


@dataclass
class Reservation:
    """What one in-flight call holds. Released exactly once, from any path."""

    ledger: "ReservationLedger"
    amount: float
    keys: list[Key] = field(default_factory=list)
    released: bool = False

    def release(self) -> None:
        self.ledger.release(self)


class ReservationLedger:
    """In-flight reservations per budget key.

    One event loop, one process: a compare followed by take() with no await
    between them is atomic, which is the whole guarantee.
    """

    def __init__(self) -> None:
        self._held: dict[Key, float] = {}

    def held(self, key: Key) -> float:
        return self._held.get(key, 0.0)

    def take(self, reservation: Reservation, key: Key) -> None:
        """Hold the reservation's amount against one more key."""
        if reservation.amount:
            self._held[key] = self.held(key) + reservation.amount
        reservation.keys.append(key)

    def release(self, reservation: Optional[Reservation]) -> None:
        if reservation is None or reservation.released:
            return
        reservation.released = True
        for key in reservation.keys:
            left = self.held(key) - reservation.amount
            if left <= 1e-12:
                self._held.pop(key, None)
            else:
                self._held[key] = left

    def empty(self) -> bool:
        return not self._held


def release_reservation(reservation: Optional[Reservation]) -> None:
    """Idempotent release for every exit path that might still hold one."""
    if reservation is not None:
        reservation.release()


def estimate_call_cost(req) -> Optional[float]:
    """Pre-call cost estimate for a canonical request, or None when the model
    has no price (the packs decide; an unknown model cannot be counted)."""
    try:
        chars = len(json.dumps(req.messages or [], default=str))
        chars += len(req.system_prompt or "")
        if req.tools:
            chars += len(json.dumps(req.tools, default=str))
        tokens_in = chars // _CHARS_PER_TOKEN
        tokens_out = req.max_tokens or DEFAULT_MAX_OUTPUT_TOKENS
        return compute_cost(req.model_id, tokens_in, tokens_out,
                            provider=req.provider or "")
    except Exception:
        return None
