import { Call, fmtUsd } from "../api";

const PALETTE = ["#4ea1ff", "#a371f7", "#3fb950", "#d29922", "#f778ba", "#39c5bb", "#e3b341", "#7ee787"];

export function agentColor(name: string | null | undefined): string {
  if (!name) return "#8b98a9";
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

const fmtRel = (ms: number) =>
  ms >= 60_000 ? `${(ms / 60_000).toFixed(1)}m` : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;

/** Gantt/waterfall on a shared time axis. Upgrade over the classic view:
 *  parent connectors come from real thread links (prev_action_id, or the
 *  caller-supplied parent_action_id) instead of timestamp inference. */
export default function TraceView({ calls }: { calls: Call[] }) {
  const rows = [...calls].sort(
    (a, b) => +new Date(a.timestamp) - +new Date(b.timestamp),
  );
  if (rows.length === 0) return <div className="empty">No calls in this session.</div>;

  const t0 = +new Date(rows[0].timestamp);
  const tEnd = Math.max(...rows.map((c) => +new Date(c.timestamp) + (c.latency_ms ?? 0)));
  const span = Math.max(tEnd - t0, 1);

  const LABEL = 190;
  const W = 1000;
  const CHART = W - LABEL - 24;
  const ROW = 26;
  const TOP = 26;
  const H = TOP + rows.length * ROW + 10;
  const x = (t: number) => LABEL + ((t - t0) / span) * CHART;
  const rowIndex = new Map(rows.map((c, i) => [c.action_id, i]));

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    px: LABEL + f * CHART,
    label: fmtRel(f * span),
  }));

  return (
    <div className="svg-scroll">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ minWidth: 700 }}>
        {ticks.map((t) => (
          <g key={t.px}>
            <line x1={t.px} y1={TOP - 6} x2={t.px} y2={H} stroke="var(--border)" strokeDasharray="2 4" />
            <text x={t.px} y={12} fill="var(--text-dim)" fontSize="9" textAnchor="middle" fontFamily="var(--mono)">
              +{t.label}
            </text>
          </g>
        ))}

        {rows.map((c, i) => {
          const start = +new Date(c.timestamp);
          const y = TOP + i * ROW;
          const barX = x(start);
          const barW = Math.max(((c.latency_ms ?? 0) / span) * CHART, 2.5);
          const failed = (c.status_code ?? 200) !== 200;
          const flagged = Boolean(c.loop_flags);
          const parentId = c.prev_action_id ?? c.parent_action_id;
          const pIdx = parentId != null ? rowIndex.get(parentId) : undefined;

          return (
            <g key={c.action_id}>
              {pIdx !== undefined && (
                <path
                  d={`M ${x(+new Date(rows[pIdx].timestamp)) + Math.max(((rows[pIdx].latency_ms ?? 0) / span) * CHART, 2.5)} ${TOP + pIdx * ROW + 8}
                      V ${y + 8} H ${barX}`}
                  stroke="var(--text-dim)"
                  strokeWidth="1"
                  fill="none"
                  opacity="0.5"
                />
              )}
              <text x={LABEL - 10} y={y + 12} fill="var(--text-dim)" fontSize="10" textAnchor="end" fontFamily="var(--mono)">
                {c.step_index != null ? `s${c.step_index} · ` : ""}
                {c.model_id.length > 18 ? c.model_id.slice(0, 17) + "…" : c.model_id}
              </text>
              <rect
                x={barX} y={y + 2} width={barW} height={13} rx={3}
                fill={failed ? "var(--red)" : agentColor(c.agent_name)}
                stroke={flagged ? "var(--amber)" : "none"}
                strokeWidth={flagged ? 1.5 : 0}
                opacity={failed ? 0.9 : 0.85}
              >
                <title>
                  {`${c.model_id} · ${c.agent_name ?? "unattributed"}
${fmtRel(c.latency_ms ?? 0)} · ${fmtUsd(c.cost_usd)} · ${c.tokens_in ?? "?"}→${c.tokens_out ?? "?"} tok${failed ? `\nstatus ${c.status_code}` : ""}${flagged ? `\nflags: ${c.loop_flags}` : ""}`}
                </title>
              </rect>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
