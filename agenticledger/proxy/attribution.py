"""The attribution pipeline (0.10 design, contract 2): one brain for
"which session and run does this call belong to", with two verbs.

    resolve(meta, req)              -> Attribution      read-only, any time
    commit(action_id, req, resp, meta) -> loop fields    capture: read, then write
    commit_refusal(attribution, req, meta)               the wall: a refusal is a
                                                         first-class event too

The kill gate calls resolve and nothing else. Capture calls resolve
through commit. Refusals commit as well, which is what keeps walls
airtight against retries and companion calls: there is no second copy
of the grouping logic anywhere to fall out of sync with the first.
LoopTracker is this module's private engine.
"""

from dataclasses import dataclass
from typing import Optional

from .loops import LoopTracker


@dataclass(frozen=True)
class Attribution:
    session_id: str
    run_id: Optional[str]
    iteration: Optional[int]
    framework: Optional[str]
    agent_name: Optional[str]
    source: str  # "explicit" (headers / path) | "inferred" (the ledger grouped it) | "none"


class AttributionResolver:
    def __init__(self, tracker: LoopTracker) -> None:
        self._tracker = tracker

    @property
    def tracker(self) -> LoopTracker:
        return self._tracker

    def resolve(self, meta: dict, req) -> Attribution:
        """Never raises; an unknowable run resolves to source 'none', and
        enforcement then has nothing to match, which fails open by design."""
        try:
            run_id, iteration, source = self._tracker.lookup(req, meta)
        except Exception:
            run_id, iteration, source = None, None, "none"
        return Attribution(
            session_id=meta.get("session_id") or "-",
            run_id=run_id,
            iteration=iteration,
            framework=meta.get("framework"),
            agent_name=meta.get("agent_name"),
            source=source,
        )

    def commit(self, action_id: str, req, resp, meta: dict) -> dict:
        """Capture-time commit: assigns the run (joining or minting),
        stitches the thread, raises flags. Returns the loop columns."""
        return self._tracker.annotate(action_id, req, resp, meta)

    def commit_refusal(self, attribution: Attribution, req,
                       meta: dict) -> Optional[int]:
        """A wall refused this call under attribution.run_id: remember the
        session and keep the signature alive so the loop's retries and
        next iterations keep resolving to the walled run. Returns the
        iteration the refusal files under (see observe_blocked)."""
        if attribution.run_id:
            return self._tracker.observe_blocked(req, meta, attribution.run_id)
        return None
