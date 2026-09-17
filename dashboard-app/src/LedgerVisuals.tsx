import { Call, FlaggedCall, flagInfo, fmtNum, fmtTime, fmtUsd, plural, Run } from "./api";

type IconName = "activity" | "sessions" | "reports" | "settings" | "key" | "info" | "chevron" | "clock" | "flag";
const paths: Record<IconName, string> = {
  activity: "M3 12h4l3-8 4 16 3-8h4",
  sessions: "M8 3h12v14H8z M4 7v14h12 M11 7h6 M11 11h4",
  reports: "M4 3v18h17 M9 16v-5 M14 16V6 M19 16V9",
  settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2",
  key: "M14 3a7 7 0 1 1-5 12L3 21H1v-4l6-6a7 7 0 0 1 7-8 M16 7h.01",
  info: "M12 8h.01 M12 11v6 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  chevron: "m9 5 7 7-7 7",
  clock: "M12 6v6l4 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  flag: "M12 8v5 M12 17h.01 M10 3 1 20h22L14 3Z",
};

/** Small, local SVGs keep the dashboard independent of fonts and CDNs. */
export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"
    aria-hidden="true" className="ui-icon"><path d={paths[name]} /></svg>;
}

export function Breadcrumb({ area, project, name }: { area: string; project?: string | null; name?: string }) {
  return <div className="ledger-breadcrumb">
    <span>{area}</span>{project && <><Icon name="chevron" size={12} /><span>{project}</span></>}
    {name && <><Icon name="chevron" size={12} /><span className="breadcrumb-current">{name}</span></>}
  </div>;
}

export interface CostPoint {
  id: string; label: string; cost: number | null; detail: string;
  tone?: "flagged" | "blocked" | "errored"; onOpen?: () => void;
}

/** Every bar comes from a captured call/iteration. Unknown prices stay
 * unknown; zero-cost refusals get a baseline marker, not invented spend. */
export function CostChart({ title, points, unit }: { title: string; points: CostPoint[]; unit: string }) {
  const max = points.reduce((highest, point) => Math.max(highest, point.cost ?? 0), 0);
  const labelEvery = Math.max(1, Math.ceil(points.length / 12));
  return <section className="cost-chart" aria-label={title}>
    <div className="panel-heading"><h3>{title}</h3><span className="chart-legend"><i /> Recorded cost</span></div>
    <div className="cost-plot">
      <div className="cost-axis" aria-hidden="true"><span>{fmtUsd(max)}</span><span>{fmtUsd(max / 2)}</span><span>$0</span></div>
      <div className="cost-scroll">
        <div className="cost-columns" style={{ minWidth: points.length * 8 }}>
          {points.map((p, i) => <div className="cost-column" key={p.id}>
            <button type="button" className={`cost-bar ${p.tone ?? ""} ${p.cost === null ? "unpriced" : ""}`}
              style={{ height: p.cost === null ? "5px" : `${max > 0 ? Math.max(0, (p.cost ?? 0) / max * 100) : 0}%` }}
              disabled={!p.onOpen} onClick={p.onOpen}
              aria-label={`${unit} ${p.label}: ${p.cost === null ? "Unknown cost" : fmtUsd(p.cost)}. ${p.detail}`}
              title={`${unit} ${p.label} · ${p.cost === null ? "Unknown cost" : fmtUsd(p.cost)} · ${p.detail}`} />
            {((i % labelEvery === 0 && i < points.length - Math.ceil(labelEvery / 2)) || i === points.length - 1) && <span className="cost-tick" aria-hidden="true">{p.label}</span>}
          </div>)}
        </div>
      </div>
    </div>
    <div className="chart-caption">{plural(points.length, unit.toLowerCase())} · {points.some((p) => p.onOpen) ? "Select a bar to inspect its session" : "In recorded order"}</div>
  </section>;
}

export function SessionMetrics({ calls }: { calls: Call[] }) {
  const cost = calls.reduce((sum, c) => sum + (c.cost_usd ?? 0), 0);
  const unpriced = calls.filter((c) => c.cost_usd == null).length;
  const tokIn = calls.reduce((sum, c) => sum + (c.tokens_in ?? 0) + (c.cache_read_tokens ?? 0) + (c.cache_write_tokens ?? 0), 0);
  const tokOut = calls.reduce((sum, c) => sum + (c.tokens_out ?? 0), 0);
  return <div className="metric-strip session-metrics">
    <div className="metric"><div className="metric-label">Recorded cost</div>
      <div className="metric-primary">{fmtUsd(cost)}</div>
      <div className="metric-sub">{unpriced ? `${plural(unpriced, "unpriced call")} excluded` : "Cache-aware pricing"}</div></div>
    <div className="metric"><div className="metric-label">Captured calls</div>
      <div className="metric-primary">{fmtNum(calls.length)}</div>
      <div className="metric-sub">Prompts, responses &amp; tool calls</div></div>
    <div className="metric"><div className="metric-label">Recorded tokens</div>
      <div className="metric-primary">{fmtNum(tokIn + tokOut)}</div>
      <div className="metric-sub">{fmtNum(tokIn)} in · {fmtNum(tokOut)} out</div></div>
  </div>;
}

export function RunTimeline({ run, flags, onOpenSession }: {
  run: Run; flags: FlaggedCall[]; onOpenSession: (id: string) => void;
}) {
  const recent = [...flags].sort((a, b) => +new Date(a.timestamp) - +new Date(b.timestamp)).slice(-3);
  const lastCallShown = recent.some((flag) => +new Date(flag.timestamp) === +new Date(run.last_call_at));
  return <section className="run-timeline" aria-label="Recorded run timeline">
    <div className="panel-heading"><h3><Icon name="activity" /> Loop Lens</h3><span className="eyebrow">Run history</span></div>
    <div className="timeline-intro"><h3>While you were away.</h3><p>Recorded activity, at a glance.</p></div>
    <ol className="timeline-events">
      <li><span className="timeline-mark"><Icon name="activity" size={14} /></span>
        <div><strong>First recorded call</strong><time>{fmtTime(run.started_at)}</time>
          <p>{run.framework || "Agent run"}</p></div></li>
      {recent.map((flag) => {
        const names: string[] = JSON.parse(flag.loop_flags);
        const warning = names.some((name) => flagInfo(name).kind === "warn");
        const tone = warning ? "warning" : names.some((name) => flagInfo(name).kind === "good") ? "success" : "neutral";
        return <li key={flag.action_id} className={`timeline-${tone}`}>
          <span className="timeline-mark"><Icon name={warning ? "flag" : "activity"} size={14} /></span>
          <div><strong>{names.map((name) => flagInfo(name).title).join(" · ")}</strong>
            <time>{fmtTime(flag.timestamp)}</time>
            <p>{flag.iteration != null ? `Iteration ${flag.iteration}` : "Recorded call"}{flag.tool_calls?.length ? ` · ${flag.tool_calls.map((tool) => tool.name || "tool").join(", ")}` : ""}</p>
            {flag.session_id && <button className="timeline-inspect" onClick={() => onOpenSession(flag.session_id!)}>Inspect {warning ? "concern" : "flag"} <span aria-hidden="true">↗</span></button>}
          </div>
        </li>;
      })}
      {!lastCallShown && <li><span className="timeline-mark"><Icon name="clock" size={14} /></span>
        <div><strong>Last recorded call</strong><time>{fmtTime(run.last_call_at)}</time>
          <p>{plural(run.iterations, "iteration")} · {plural(run.call_count, "call")}</p></div></li>}
    </ol>
    <div className="timeline-footnote">{flags.length > 0 ? `${recent.length} of ${flags.length} calls with flags shown · full evidence below` : "Observed events from this run's ledger."}</div>
  </section>;
}
