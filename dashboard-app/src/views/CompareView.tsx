import { useEffect, useState } from "react";
import { Call, fmtNum, fmtTime, fmtUsd, get, Iteration, Run } from "../api";

/** Side-by-side diff of two loop runs — the change-the-prompt-and-rerun
 *  workflow: did the new run get cheaper, shorter, less flagged? */

function useRun(id: string) {
  const [detail, setDetail] = useState<Run | null>(null);
  const [iterations, setIterations] = useState<Iteration[]>([]);
  const [firstCall, setFirstCall] = useState<Call | null>(null);
  useEffect(() => {
    get<Run>(`/api/runs/${encodeURIComponent(id)}`).then(setDetail).catch(() => setDetail(null));
    get<Iteration[]>(`/api/runs/${encodeURIComponent(id)}/iterations`)
      .then(setIterations).catch(() => setIterations([]));
  }, [id]);
  useEffect(() => {
    const sid = iterations[0]?.session_id;
    if (!sid) { setFirstCall(null); return; }
    get<Call[]>(`/session/${encodeURIComponent(sid)}`)
      .then((rows) => setFirstCall(rows[0] ?? null))
      .catch(() => setFirstCall(null));
  }, [iterations]);
  return { detail, iterations, firstCall };
}

// ── Prompt drift ─────────────────────────────────────────────────────────────

type DiffLine = { kind: "same" | "add" | "del"; text: string };

function diffLines(a: string, b: string): DiffLine[] {
  const A = a.split("\n");
  const B = b.split("\n");
  const m = A.length, n = B.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: DiffLine[] = [];
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (A[i] === B[j]) { out.push({ kind: "same", text: A[i] }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ kind: "del", text: A[i] }); i++; }
    else { out.push({ kind: "add", text: B[j] }); j++; }
  }
  while (i < m) out.push({ kind: "del", text: A[i++] });
  while (j < n) out.push({ kind: "add", text: B[j++] });
  return out;
}

function firstUserText(call: Call | null): string {
  const msgs = Array.isArray(call?.messages) ? (call!.messages as any[]) : [];
  const first = msgs.find((msg) => msg?.role === "user");
  const content = first?.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content))
    return content.map((block) => (typeof block === "string" ? block : block?.text ?? "")).join("\n");
  return "";
}

function DriftBlock({ label, a, b }: { label: string; a: string; b: string }) {
  if (!a && !b) return null;
  if (a === b) {
    return (
      <div className="drift-block">
        <div className="muted">{label}: identical in both runs</div>
      </div>
    );
  }
  const lines = diffLines(a, b);
  return (
    <div className="drift-block">
      <div className="muted">{label} — <span className="diff-del-key">removed</span> vs <span className="diff-add-key">added</span></div>
      <pre className="diff">
        {lines.map((line, i) => (
          <div key={i} className={line.kind === "same" ? "" : line.kind === "add" ? "diff-add" : "diff-del"}>
            {line.kind === "add" ? "+ " : line.kind === "del" ? "− " : "  "}{line.text}
          </div>
        ))}
      </pre>
    </div>
  );
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

      {(ra.firstCall || rb.firstCall) && (
        <>
          <div className="section-title">Prompt drift — what changed between the runs</div>
          <DriftBlock
            label="System prompt"
            a={ra.firstCall?.system_prompt ?? ""}
            b={rb.firstCall?.system_prompt ?? ""}
          />
          <DriftBlock
            label="Opening instruction"
            a={firstUserText(ra.firstCall)}
            b={firstUserText(rb.firstCall)}
          />
        </>
      )}
    </>
  );
}
