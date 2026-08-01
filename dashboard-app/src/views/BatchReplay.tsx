import { useEffect, useRef, useState } from "react";
import {
  BatchJob, fmtUsd, getReplayJob, listReplayJobs, replayModels, replayTargets,
  ReplayTarget, startBatchReplay,
} from "../api";

/** 0.8 flagship — test-drive another model on the whole journey.
 *  Each step re-sends the ORIGINAL captured inputs and grades the answer:
 *  an honest moment-by-moment comparison, not a pretend re-run. */
export default function BatchReplay({ scope, refId, onOpenSession, numberOf }: {
  scope: "run" | "session"; refId: string;
  onOpenSession?: (sid: string) => void;
  numberOf?: (originalActionId: string) => number | null;
}) {
  const [open, setOpen] = useState(false);
  const [targets, setTargets] = useState<ReplayTarget[]>([]);
  const [provider, setProvider] = useState(
    localStorage.getItem("agenticledger.replay.dest") ?? "auto");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [job, setJob] = useState<BatchJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  // A finished report card should find the reader: if one exists for this
  // run/session, open the panel and show it without being asked.
  useEffect(() => {
    listReplayJobs({ scope, refId })
      .then((r) => {
        const last = r.jobs[r.jobs.length - 1];
        if (last) { setOpen(true); poll(last.job_id); }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, refId]);

  useEffect(() => {
    if (!open) return;
    replayTargets().then((r) => setTargets(r.targets)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    const t = targets.find((x) => x.provider === provider);
    if (!t) { setModels([]); return; }
    replayModels(provider)
      .then((r) => {
        setModels(r.models);
        if (t.local && r.models.length > 0) setModel((cur) => cur || r.models[0]);
      })
      .catch(() => setModels([]));
  }, [provider, targets]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const poll = (jobId: string) => {
    getReplayJob(jobId)
      .then((j) => {
        setJob(j);
        if (j.status !== "done") {
          timer.current = window.setTimeout(() => poll(jobId), 1000);
        }
      })
      .catch((e) => setError(e.message));
  };

  const run = () => {
    setError(null);
    setJob(null);
    startBatchReplay({
      [scope === "run" ? "run_id" : "session_id"]: refId,
      model: model.trim(),
      ...(provider !== "auto" ? { provider } : {}),
    })
      .then((r) => poll(r.job_id))
      .catch((e) => setError(e.message));
  };

  if (!open) {
    return (
      <button className="link-btn" onClick={() => setOpen(true)}
              title="Re-run every step of this on another model and get a report card">
        ⟳ Replay whole {scope}
      </button>
    );
  }

  const report = job?.report;
  const fumbleSteps = (job?.steps ?? []).filter(
    (s) => s.status !== "ok" || (s.score && !s.score.match));
  const shown = showAll ? job?.steps ?? [] : fumbleSteps;

  return (
    <div className="replay-panel">
      <div className="replay-controls">
        <select className="replay-provider" value={provider}
                onChange={(e) => { setProvider(e.target.value);
                  localStorage.setItem("agenticledger.replay.dest", e.target.value); }}>
          <option value="auto">auto</option>
          {targets.map((t) => (
            <option key={t.provider} value={t.provider}>
              {t.local ? `local — ${t.host}` : `${t.provider} — ${t.host}`}
            </option>
          ))}
        </select>
        <input className="replay-model" placeholder="model to test-drive…"
               value={model} list={models.length ? "batch-models" : undefined}
               onChange={(e) => setModel(e.target.value)} />
        {models.length > 0 && (
          <datalist id="batch-models">
            {models.map((m) => <option key={m} value={m} />)}
          </datalist>
        )}
        <button className="link-btn" disabled={!model.trim() || job?.status === "running"}
                onClick={run}>
          {job?.status === "running" ? "Replaying…" : `Replay whole ${scope}`}
        </button>
        <button className="link-btn" onClick={() => setOpen(false)}>Close</button>
        <span className="muted">
          every step re-sends its original inputs — local destinations are free
        </span>
      </div>
      {error && <div className="replay-error">{error}</div>}

      {job && (
        <div className="batch-progress">
          <div className="batch-bar">
            <div className="batch-bar-fill"
                 style={{ width: `${(100 * job.done) / job.total}%` }} />
          </div>
          <span className="muted">{job.done} / {job.total} steps</span>
        </div>
      )}

      {report && (
        <div className="report-card">
          <div className="report-headline"
               title="a 'moment' is one recorded call, re-asked to the stand-in with its original inputs; it matches when the stand-in answered and reached for the same tools the original used">
            {report.matched} / {report.replayed} moments matched
            {report.failed > 0 && <span className="rc-failed"> · {report.failed} failed</span>}
            {report.skipped > 0 && (
              <span className="muted" title="metadata-only captures have no stored messages to replay">
                {" "}· {report.skipped} not replayable
              </span>
            )}
          </div>
          <div className="muted" style={{ maxWidth: 720 }}>
            {report.replayed} recorded calls were re-asked to {job!.model} with
            their original inputs; at {report.matched} of them it made the same
            move as the original. Nothing was executed.
          </div>
          <div className="muted">
            cost: {fmtUsd(report.replay_cost_usd)} on {job!.model} vs{" "}
            {fmtUsd(report.original_cost_usd)} original
            {onOpenSession && (
              <>
                {" · "}
                <button className="link-btn" style={{ marginTop: 0 }}
                        onClick={() => onOpenSession(job!.replay_session_id)}>
                  open replay session
                </button>
              </>
            )}
          </div>
          {job!.steps.length > 0 && (
            <div className="muted" style={{ marginTop: 6 }}>
              {fumbleSteps.length === 0
                ? "No fumbles — every step held up."
                : `${fumbleSteps.length} to read:`}{" "}
              {job!.steps.length > fumbleSteps.length && (
                <button className="link-btn" style={{ marginTop: 0 }}
                        onClick={() => setShowAll(!showAll)}>
                  {showAll ? "show fumbles only" : "show all steps"}
                </button>
              )}
            </div>
          )}
          {shown.map((st, i) => (
            <div key={st.original_action_id} className="fumble">
              <div className="fumble-head">
                <span className="mono" title="the call this row grades — same number as in the Calls list">
                  {numberOf?.(st.original_action_id)
                    ? `call #${numberOf(st.original_action_id)}`
                    : `step ${job!.steps.indexOf(st) + 1}`}
                </span>
                {st.status !== "ok" ? (
                  <span className="badge error">{st.status}</span>
                ) : st.score?.match ? (
                  <span className="badge complete">match</span>
                ) : (
                  <span className="badge flagged">
                    {st.score?.tool_verdict === "orig-only" ? "dropped the tools"
                      : st.score?.tool_verdict === "replay-only" ? "invented tools"
                      : st.score?.tool_verdict === "different" ? "different tools"
                      : "no answer"}
                  </span>
                )}
                {st.score && st.score.tool_verdict !== "same" && (
                  <span className="muted mono">
                    {st.score.orig_tools.join(",") || "—"} → {st.score.replay_tools.join(",") || "—"}
                  </span>
                )}
              </div>
              {st.reason && <div className="muted">{st.reason}</div>}
              {st.status === "ok" && (
                <div className="replay-grid">
                  <div>
                    <div className="muted mono">{st.original_model} (original — really ran)</div>
                    <pre>{st.original_content?.trim()
                      || (st.score?.orig_tools.length
                        ? `(no text — called: ${st.score.orig_tools.join(", ")})`
                        : "(no text)")}</pre>
                  </div>
                  <div>
                    <div className="muted mono">{job!.model} (replay — answer only, nothing executed)</div>
                    <pre>{st.replay_content?.trim()
                      || (st.score?.replay_tools.length
                        ? `(no text — would have called: ${st.score.replay_tools.join(", ")})`
                        : "(no answer at all)")}</pre>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
