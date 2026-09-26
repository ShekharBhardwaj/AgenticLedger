import { useCallback, useEffect, useState } from "react";
import { fmtTime, liveUpdates, resumeAllCalls, stopAllCalls, stopState, StopState } from "./api";

/** Stop all calls: the fleet-wide emergency stop. The banner IS the state,
 *  shown on every page while the stop is on, and carries the lift; the
 *  control in Loop Lens engages it. Both read the live state so a stop
 *  pressed on a phone shows on the laptop within the next event. */
function useStopState(): [StopState | null, () => void] {
  const [stop, setStop] = useState<StopState | null>(null);
  const refetch = useCallback(() => { stopState().then(setStop).catch(() => {}); }, []);
  useEffect(() => { refetch(); return liveUpdates(refetch); }, [refetch]);
  return [stop, refetch];
}

function describe(stop: StopState): string {
  const by = stop.by ? ` by ${stop.by}` : "";
  const since = stop.since ? `, since ${fmtTime(stop.since)}` : "";
  return `All calls are stopped${by}${since}.`;
}

export function StopBanner() {
  const [stop, refetch] = useStopState();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  if (!stop?.calls_stopped) return null;
  return (
    <div className="stop-banner" role="alert">
      <span>
        <b>{describe(stop)}</b> Every agent call is refused at the wall and
        recorded; nothing is spent. Replays are refused too.
      </span>
      <button className="link-btn" disabled={busy}
              title="Lifts the stop: every agent's calls flow again. Per-run blocks and budgets still apply."
              onClick={() => {
                setBusy(true); setNote(null);
                resumeAllCalls()
                  .then(refetch)
                  .catch((e) => setNote(
                    `could not lift it: ${e?.message || "request failed"}. Calls are still stopped.`))
                  .finally(() => setBusy(false));
              }}>
        {busy ? "lifting…" : "allow calls again"}
      </button>
      {note && <span className="stop-note">{note}</span>}
    </div>
  );
}

export function StopAllControl() {
  const [stop, refetch] = useStopState();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // Engaged: the banner above is the state and holds the lift.
  if (stop === null || stop.calls_stopped) return null;
  return (
    <span className="stop-control">
      {confirm ? (
        <>
          <button className="link-btn project-purge" disabled={busy}
                  onClick={() => {
                    setBusy(true); setNote(null);
                    stopAllCalls()
                      .then(() => { setConfirm(false); refetch(); })
                      .catch((e) => setNote(
                        `the stop did NOT take: ${e?.message || "request failed"}. Calls are still flowing.`))
                      .finally(() => setBusy(false));
                  }}>
            {busy ? "stopping…" : "stop every agent's calls now"}
          </button>
          <button className="link-btn" onClick={() => { setConfirm(false); setNote(null); }}>Cancel</button>
        </>
      ) : (
        <button className="link-btn project-purge"
                title="The emergency stop for the whole fleet: every agent call is refused at the wall, recorded, and nothing is spent until you lift it. Survives a restart."
                onClick={() => setConfirm(true)}>
          Stop all calls
        </button>
      )}
      {note && <span className="stop-note">{note}</span>}
    </span>
  );
}
