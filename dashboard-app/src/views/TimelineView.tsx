import { useMemo, useState } from "react";
import { Step, StepSummary, fmtNum, fmtUsd, plural } from "../api";
import ProviderMark from "./ProviderMark";

/** One timeline for a trace: every step the ledger holds for the session,
 *  as a tree on a shared time axis. Model calls the proxy saw, tool runs
 *  the agent reported (or the ledger derived from the wire), sub-agents,
 *  turns, compactions, plain spans. A step placed by time rather than by
 *  an id in the data is drawn with a dashed bar and says so. Nothing
 *  here is more certain than the record. */

const KIND_LABEL: Record<string, string> = {
  call: "model call", tool: "tool run", agent: "sub-agent", turn: "turn",
  compaction: "compaction", span: "span",
};
const KIND_GLYPH: Record<string, string> = {
  call: "M", tool: "T", agent: "A", turn: "U", compaction: "C", span: "S",
};

// The synthetic row that anchors the tree: the trace itself, totals and
// all. Client-side only, so the steps API and its counts stay the record.
const ROOT_ID = "__trace__";

const WIRE_TITLE = "Measured on the wire: from the model call that issued "
  + "this tool to the call that returned its result, so it includes the "
  + "agent's own time between the two. The agent's hooks or telemetry "
  + "would give the tool's own duration.";

const fmtDur = (ms: number | null | undefined) => {
  if (ms == null) return "";
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)} min`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.round(ms)} ms`;
};

const fmtClock = (iso: string | null) => {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
};

export default function TimelineView({ steps, summary, sessionId, rootName }: {
  steps: Step[]; summary: StepSummary; sessionId: string; rootName?: string;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());

  const t0 = summary.started_at ? +new Date(summary.started_at) : 0;
  const span = Math.max(summary.duration_ms || 0, 1);

  // The trace itself as the tree's root, so every step hangs off a named
  // whole instead of floating. Its numbers are the summary's, restated.
  const rows = useMemo<Step[]>(() => {
    if (steps.length === 0) return steps;
    const calls = steps.filter((s) => s.kind === "call");
    const root: Step = {
      id: ROOT_ID, kind: "span", name: rootName || sessionId,
      parent_id: null, link: null,
      started_at: summary.started_at, ended_at: summary.ended_at,
      duration_ms: summary.duration_ms, status: "ok", source: "",
      agent: null, agent_id: null, model: null,
      cost_usd: summary.cost_usd,
      tokens_in: calls.reduce((n, s) => n + (s.tokens_in ?? 0), 0),
      tokens_out: calls.reduce((n, s) => n + (s.tokens_out ?? 0), 0),
      input: null, output: null, detail: {}, ref: {},
      depth: 0, children: steps.filter((s) => s.depth === 0).length,
    };
    return [root, ...steps.map((s) => ({ ...s, depth: s.depth + 1 }))];
  }, [steps, summary, rootName, sessionId]);

  // Rows under a collapsed ancestor stay out of the list.
  const visible = useMemo(() => {
    const out: Step[] = [];
    const hidden: Array<{ id: string; depth: number }> = [];
    for (const s of rows) {
      while (hidden.length && hidden[hidden.length - 1].depth >= s.depth) hidden.pop();
      if (hidden.length) continue;
      out.push(s);
      if (collapsed.has(s.id) && s.children > 0) hidden.push({ id: s.id, depth: s.depth });
    }
    return out;
  }, [rows, collapsed]);

  if (steps.length === 0) return <div className="empty">No steps in this trace.</div>;

  const k = summary.kinds;
  const parts = [
    k.call ? plural(k.call, "model call") : null,
    k.tool ? plural(k.tool, "tool run") : null,
    k.agent ? plural(k.agent, "sub-agent") : null,
    k.turn ? plural(k.turn, "turn") : null,
    k.compaction ? plural(k.compaction, "compaction") : null,
    k.span ? plural(k.span, "span") : null,
  ].filter(Boolean);
  const sources = summary.sources.map((s) => s === "proxy" ? "the proxy" : s === "hook" ? "hooks" : s === "otel" ? "telemetry" : s);

  const toggle = (id: string) =>
    setCollapsed((cur) => { const next = new Set(cur); if (next.has(id)) next.delete(id); else next.add(id); return next; });

  // The trace header already names the agent; a badge on every call only
  // earns its room when the trace actually mixes agents.
  const mixedAgents = new Set(steps.filter((s) => s.kind === "call" && s.agent).map((s) => s.agent)).size > 1;

  return (
    <div className="timeline">
      <div className="timeline-summary muted">
        {parts.join(", ")} · {fmtDur(summary.duration_ms)}
        {summary.cost_usd > 0 && <> · {fmtUsd(summary.cost_usd)}</>}
        {" · from "}{sources.join(" and ")}
        {summary.placed_by_time > 0 && (
          <span className="timeline-note" title="These steps carried no parent id, so they were placed inside the step whose time window held them.">
            {" · "}{summary.placed_by_time} placed by time
          </span>
        )}
      </div>
      <div className="timeline-axis" aria-hidden="true">
        <span className="timeline-axis-label" />
        <span className="timeline-axis-track">
          {[0, 0.25, 0.5, 0.75, 1].map((f) => (
            <span key={f} className="timeline-tick" style={{ left: `${f * 100}%` }}>
              <i>{fmtDur(span * f)}</i>
            </span>
          ))}
        </span>
      </div>
      <ol className="timeline-rows">
        {visible.map((s) => {
          const isRoot = s.id === ROOT_ID;
          const kindLabel = isRoot ? "trace" : (KIND_LABEL[s.kind] ?? s.kind);
          // A tool the ledger derived from the wire: its duration is issue
          // to result, not the tool's own clock. Say so wherever it shows.
          const wire = !isRoot && s.kind === "tool" && s.source === "proxy";
          const tokens = s.kind === "call" || isRoot
            ? (s.tokens_in ?? 0) + (s.tokens_out ?? 0) : null;
          const start = s.started_at ? +new Date(s.started_at) - t0 : 0;
          const left = Math.min(Math.max(start / span, 0), 1) * 100;
          const width = Math.max(Math.min((s.duration_ms || 0) / span, 1 - left / 100) * 100, 0.25);
          const isOpen = open === s.id;
          const status = s.status === "ok" || s.status === "running" ? "" : s.status;
          return (
            <li key={s.id} className={`timeline-row kind-${isRoot ? "root" : s.kind} ${s.link === "time" ? "by-time" : ""} ${status}`}>
              <div className="timeline-line" style={{ ["--depth" as string]: s.depth }}>
                <div className="timeline-label">
                  {s.children > 0 ? (
                    <button type="button" className="timeline-caret" aria-label={collapsed.has(s.id) ? "expand" : "collapse"}
                            aria-expanded={!collapsed.has(s.id)} onClick={() => toggle(s.id)}>
                      {collapsed.has(s.id) ? "▸" : "▾"}
                    </button>
                  ) : <span className="timeline-caret" />}
                  <button type="button" className="timeline-name" onClick={() => setOpen(isOpen ? null : s.id)} aria-expanded={isOpen}
                          title={`${kindLabel}${s.link === "time" ? " (placed by time)" : ""}${wire ? ` (wire time: issued to answered)` : ""}`}>
                    {s.kind === "call" ? (
                      <ProviderMark model={s.model} />
                    ) : (
                      <span className={`timeline-glyph g-${isRoot ? "root" : s.kind}`} aria-hidden="true">{isRoot ? "◆" : (KIND_GLYPH[s.kind] ?? "?")}</span>
                    )}
                    <span className="timeline-text">{s.name}</span>
                    {mixedAgents && s.agent && s.kind === "call" && <span className="badge fw">{s.agent}</span>}
                    {s.status === "error" && <span className="badge error">failed</span>}
                    {s.status === "blocked" && <span className="badge blocked">blocked</span>}
                    {s.status === "denied" && <span className="badge blocked">denied</span>}
                    {s.status === "asked" && <span className="badge flagged">asked</span>}
                    {s.status === "running" && <span className="badge">running</span>}
                  </button>
                  {tokens !== null && tokens > 0 && (
                    <span className="timeline-stat mono" title={`${fmtNum(s.tokens_in ?? 0)} in / ${fmtNum(s.tokens_out ?? 0)} out${s.cost_usd ? ` · ${fmtUsd(s.cost_usd)}` : ""}`}>
                      {fmtNum(tokens)} tok{s.cost_usd ? ` · ${fmtUsd(s.cost_usd)}` : ""}
                    </span>
                  )}
                  {wire && (
                    <span className="timeline-stat mono" title={WIRE_TITLE}>wire</span>
                  )}
                </div>
                <div className="timeline-track" onClick={() => setOpen(isOpen ? null : s.id)}>
                  <span className="timeline-bar" style={{ left: `${left}%`, width: `${width}%` }}
                        title={`${fmtClock(s.started_at)} · ${fmtDur(s.duration_ms)}${s.link === "time" ? " · placed by time" : ""}${wire ? " · wire time, issued to answered" : ""}`} />
                  <span className="timeline-dur mono" style={{ left: `${Math.min(left + width, 100)}%` }}>{fmtDur(s.duration_ms)}</span>
                </div>
              </div>
              {isOpen && (
                <div className="timeline-body" style={{ ["--depth" as string]: s.depth }}>
                  <div className="timeline-meta muted">
                    {kindLabel} · {fmtClock(s.started_at)} to {fmtClock(s.ended_at)} · {fmtDur(s.duration_ms)}
                    {s.model && <> · {s.model}</>}
                    {s.tokens_in != null && <> · {fmtNum(s.tokens_in)} in / {fmtNum(s.tokens_out ?? 0)} out</>}
                    {s.cost_usd != null && s.cost_usd > 0 && <> · {fmtUsd(s.cost_usd)}</>}
                    {s.source && <>{" · from "}{s.source === "proxy" ? "the proxy" : s.source === "hook" ? "hooks" : s.source === "otel" ? "telemetry" : s.source}</>}
                    {wire && <> · <span title={WIRE_TITLE}>wire time, issued to answered</span></>}
                    {s.link === "time" && <> · <span className="timeline-note">placed by time, no parent id in the data</span></>}
                    {s.detail?.missing_parent ? <> · <span className="timeline-note">its parent is not in this trace</span></> : null}
                    {s.detail?.derived ? <> · {String(s.detail.derived)}</> : null}
                  </div>
                  {s.detail?.reason ? <div className="timeline-reason">{String(s.detail.reason)}</div> : null}
                  {s.detail?.error ? <div className="timeline-reason">{String(s.detail.error)}</div> : null}
                  {s.input && <><div className="muted">input</div><pre className="activity-pre">{s.input}</pre></>}
                  {s.output && <><div className="muted">output</div><pre className="activity-pre">{s.output}</pre></>}
                  {s.kind === "call" && !isRoot && (
                    <a className="link-btn" href={`#/traces/${encodeURIComponent(sessionId)}/calls/${encodeURIComponent(s.id)}`}>
                      Open this call
                    </a>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
