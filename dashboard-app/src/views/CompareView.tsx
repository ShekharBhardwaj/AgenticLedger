import { useEffect, useState } from "react";
import { fmtNum, fmtTime, fmtUsd, get, Iteration, Run } from "../api";

/** Side-by-side diff of two loop runs — the change-the-prompt-and-rerun
 *  workflow: did the new run get cheaper, shorter, less flagged? */

function useRun(id: string) {
  const [detail, setDetail] = useState<Run | null>(null);
  const [iterations, setIterations] = useState<Iteration[]>([]);
  useEffect(() => {
    get<Run>(`/api/runs/${encodeURIComponent(id)}`).then(setDetail).catch(() => setDetail(null));
    get<Iteration[]>(`/api/runs/${encodeURIComponent(id)}/iterations`)
      .then(setIterations).catch(() => setIterations([]));
  }, [id]);
  return { detail, iterations };
}

function durationMin(r: Run | null): number | null {
  if (!r?.started_at || !r?.last_call_at) return null;
  const ms = new Date(r.last_call_at).getTime() - new Date(r.started_at).getTime();
  return ms >= 0 ? ms / 60000 : null;
}

interface Row {
  label: string;
  a: number | null;
  b: number | null;
  fmt: (v: number) => string;
  lowerIsBetter?: boolean;
}

function DeltaCell({ row }: { row: Row }) {
  if (row.a == null || row.b == null) return <td>—</td>;
  const d = row.b - row.a;
  if (d === 0) return <td className="muted">=</td>;
  const pct = row.a !== 0 ? ` (${d > 0 ? "+" : ""}${Math.round((100 * d) / row.a)}%)` : "";
  const color = row.lowerIsBetter === undefined
    ? undefined
    : (d < 0) === row.lowerIsBetter ? "var(--green)" : "var(--red)";
  return (
    <td style={{ color }}>
      {d > 0 ? "+" : "−"}{row.fmt(Math.abs(d))}{pct}
    </td>
  );
}

function Ribbon({ id, iterations, maxCost, onOpenSession }: {
  id: string;
  iterations: Iteration[];
  maxCost: number;
  onOpenSession: (s: string) => void;
}) {
  return (
    <div className="cmp-col">
      <div className="muted mono">{id}</div>
      <div className="ribbon">
        {iterations.map((it) => (
          <div
            key={String(it.iteration)}
            className={`bar ${it.error_calls ? "errored" : it.flagged_calls ? "flagged" : ""}`}
            style={{ height: `${Math.max((100 * (it.cost_usd || 0)) / maxCost, 3)}%` }}
            title={`iteration ${it.iteration}: ${fmtUsd(it.cost_usd)}, ${it.call_count} calls`}
            onClick={() => it.session_id && onOpenSession(it.session_id)}
          />
        ))}
        {iterations.length === 0 && <div className="empty">no iterations</div>}
      </div>
    </div>
  );
}

export default function CompareView({ a, b, onClose, onOpenSession }: {
  a: string;
  b: string;
  onClose: () => void;
  onOpenSession: (s: string) => void;
}) {
  const ra = useRun(a);
  const rb = useRun(b);

  // One shared scale so bar heights are comparable across the two runs.
  const maxCost = Math.max(
    ...ra.iterations.map((i) => i.cost_usd || 0),
    ...rb.iterations.map((i) => i.cost_usd || 0),
    0.000001,
  );

  const rows: Row[] = [
    { label: "total cost", a: ra.detail?.total_cost_usd ?? null, b: rb.detail?.total_cost_usd ?? null, fmt: fmtUsd, lowerIsBetter: true },
    { label: "iterations", a: ra.detail?.iterations ?? null, b: rb.detail?.iterations ?? null, fmt: String, lowerIsBetter: true },
    { label: "llm calls", a: ra.detail?.call_count ?? null, b: rb.detail?.call_count ?? null, fmt: fmtNum, lowerIsBetter: true },
    { label: "tokens in", a: ra.detail?.total_tokens_in ?? null, b: rb.detail?.total_tokens_in ?? null, fmt: fmtNum },
    { label: "tokens out", a: ra.detail?.total_tokens_out ?? null, b: rb.detail?.total_tokens_out ?? null, fmt: fmtNum },
    { label: "flagged calls", a: ra.detail?.flagged_calls ?? null, b: rb.detail?.flagged_calls ?? null, fmt: String, lowerIsBetter: true },
    { label: "duration (min)", a: durationMin(ra.detail), b: durationMin(rb.detail), fmt: (v) => v.toFixed(1), lowerIsBetter: true },
  ];

  return (
    <>
      <h2 className="page-title">
        Compare runs
        <button className="link-btn" style={{ marginLeft: 12 }} onClick={onClose}>
          ✕ close
        </button>
      </h2>
      <div className="muted">
        <span className="mono">{a}</span>
        {ra.detail && <> ({fmtTime(ra.detail.started_at)})</>}
        {"  vs  "}
        <span className="mono">{b}</span>
        {rb.detail && <> ({fmtTime(rb.detail.started_at)})</>}
      </div>

      <table className="rtable" style={{ maxWidth: 640, marginTop: 16 }}>
        <thead>
          <tr>
            <th>metric</th>
            <th className="mono">{a.length > 18 ? a.slice(0, 17) + "…" : a}</th>
            <th className="mono">{b.length > 18 ? b.slice(0, 17) + "…" : b}</th>
            <th>Δ (b − a)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td>{row.label}</td>
              <td>{row.a == null ? "—" : row.fmt(row.a)}</td>
              <td>{row.b == null ? "—" : row.fmt(row.b)}</td>
              <DeltaCell row={row} />
            </tr>
          ))}
        </tbody>
      </table>

      <div className="section-title">Cost per iteration (shared scale)</div>
      <div className="cmp-grid">
        <Ribbon id={a} iterations={ra.iterations} maxCost={maxCost} onOpenSession={onOpenSession} />
        <Ribbon id={b} iterations={rb.iterations} maxCost={maxCost} onOpenSession={onOpenSession} />
      </div>
    </>
  );
}
