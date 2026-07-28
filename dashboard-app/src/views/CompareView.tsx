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
// Prompts are skill-sized now (thousands of lines), so the diff is built to
// survive scale: identical prefix/suffix lines are trimmed before the LCS
// (edits in big files are localized, so this usually collapses the problem
// to a handful of lines), a hard cell cap keeps pathological pairs from
// freezing the tab, and rendering folds unchanged runs into git-style hunks.

type DiffLine = { kind: "same" | "add" | "del"; text: string } | { kind: "fold"; count: number };

// A diff with more than this many changed lines is unreadable anyway —
// beyond it we show the fallback note instead of burning memory.
const MAX_EDIT_DISTANCE = 1200;

type Op = { kind: "same" | "add" | "del"; text: string };

/** Myers O(ND) line diff — cost scales with the number of differences, not
 *  file size, so three edits in a 3,000-line skill prompt are near-free.
 *  Returns null when the edit distance exceeds MAX_EDIT_DISTANCE. */
function myersDiff(A: string[], B: string[]): Op[] | null {
  const N = A.length, M = B.length;
  const maxD = Math.min(N + M, MAX_EDIT_DISTANCE);
  const offset = maxD;
  const v = new Array(2 * maxD + 1).fill(0);
  const trace: number[][] = [];
  let D = -1;
  outer:
  for (let d = 0; d <= maxD; d++) {
    trace.push(v.slice());
    for (let k = -d; k <= d; k += 2) {
      let x: number;
      if (k === -d || (k !== d && v[offset + k - 1] < v[offset + k + 1])) {
        x = v[offset + k + 1];
      } else {
        x = v[offset + k - 1] + 1;
      }
      let y = x - k;
      while (x < N && y < M && A[x] === B[y]) { x++; y++; }
      v[offset + k] = x;
      if (x >= N && y >= M) { D = d; break outer; }
    }
  }
  if (D < 0) return null;

  const ops: Op[] = [];
  let x = N, y = M;
  for (let d = D; d > 0; d--) {
    const vd = trace[d];
    const k = x - y;
    const prevK = (k === -d || (k !== d && vd[offset + k - 1] < vd[offset + k + 1]))
      ? k + 1 : k - 1;
    const prevX = vd[offset + prevK];
    const prevY = prevX - prevK;
    while (x > prevX && y > prevY) { ops.push({ kind: "same", text: A[x - 1] }); x--; y--; }
    if (x === prevX) { ops.push({ kind: "add", text: B[y - 1] }); y--; }
    else { ops.push({ kind: "del", text: A[x - 1] }); x--; }
  }
  while (x > 0 && y > 0) { ops.push({ kind: "same", text: A[x - 1] }); x--; y--; }
  while (y > 0) { ops.push({ kind: "add", text: B[y - 1] }); y--; }
  while (x > 0) { ops.push({ kind: "del", text: A[x - 1] }); x--; }
  return ops.reverse();
}

function lcsDiff(A: string[], B: string[]): { kind: "same" | "add" | "del"; text: string }[] {
  const m = A.length, n = B.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out: { kind: "same" | "add" | "del"; text: string }[] = [];
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

/** Line diff with prefix/suffix trimming and folded unchanged runs.
 *  Returns null when the edit distance is too large to render usefully. */
function diffLines(a: string, b: string): DiffLine[] | null {
  const A = a.split("\n");
  const B = b.split("\n");
  let start = 0;
  while (start < A.length && start < B.length && A[start] === B[start]) start++;
  let endA = A.length, endB = B.length;
  while (endA > start && endB > start && A[endA - 1] === B[endB - 1]) { endA--; endB--; }
  const mid = myersDiff(A.slice(start, endA), B.slice(start, endB));
  if (mid === null) return null;

  const CONTEXT = 2;
  const raw: DiffLine[] = [
    ...A.slice(0, start).map((text) => ({ kind: "same" as const, text })),
    ...mid,
    ...A.slice(endA).map((text) => ({ kind: "same" as const, text })),
  ];
  // Fold long unchanged runs, keeping CONTEXT lines on each side of changes.
  const out: DiffLine[] = [];
  let run: { kind: "same"; text: string }[] = [];
  const flushRun = (atEnd: boolean, atStart: boolean) => {
    const keepHead = atStart ? 0 : CONTEXT;
    const keepTail = atEnd ? 0 : CONTEXT;
    if (run.length > keepHead + keepTail + 1) {
      out.push(...run.slice(0, keepHead));
      out.push({ kind: "fold", count: run.length - keepHead - keepTail });
      out.push(...run.slice(run.length - keepTail));
    } else {
      out.push(...run);
    }
    run = [];
  };
  let seenChange = false;
  for (const line of raw) {
    if (line.kind === "same") {
      run.push(line as { kind: "same"; text: string });
    } else {
      flushRun(false, !seenChange);
      seenChange = true;
      out.push(line);
    }
  }
  flushRun(true, !seenChange);
  return out;
}

/** Word-level refinement for a paired changed line: marks only the words
 *  that differ, so a one-word edit in a prose paragraph reads as one word. */
function wordDiff(a: string, b: string): { del: JSX.Element; add: JSX.Element } {
  const ta = a.split(/(\s+)/);
  const tb = b.split(/(\s+)/);
  if (ta.length * tb.length > 40_000) {
    return { del: <>{a}</>, add: <>{b}</> };
  }
  const parts = lcsDiff(ta, tb);
  const delEls: JSX.Element[] = [];
  const addEls: JSX.Element[] = [];
  parts.forEach((p, i) => {
    if (p.kind === "same") { delEls.push(<span key={i}>{p.text}</span>); addEls.push(<span key={i}>{p.text}</span>); }
    else if (p.kind === "del") delEls.push(<mark key={i} className="w-del">{p.text}</mark>);
    else addEls.push(<mark key={i} className="w-add">{p.text}</mark>);
  });
  return { del: <>{delEls}</>, add: <>{addEls}</> };
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
  const aCount = a.split("\n").length;
  const bCount = b.split("\n").length;
  if (lines === null) {
    return (
      <div className="drift-block">
        <div className="muted">
          {label}: differs on more than {MAX_EDIT_DISTANCE.toLocaleString()} lines
          ({aCount} vs {bCount} total) — effectively rewritten; open the first
          call of each run to read them whole.
        </div>
      </div>
    );
  }
  const adds = lines.filter((l) => l.kind === "add").length;
  const dels = lines.filter((l) => l.kind === "del").length;

  const rows: JSX.Element[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.kind === "fold") {
      rows.push(<div key={i} className="diff-fold">⋯ {line.count} unchanged lines</div>);
      continue;
    }
    const next = lines[i + 1];
    const prev = lines[i - 1];
    // A lone del followed by a lone add is an edited line — refine to words.
    if (line.kind === "del" && next?.kind === "add"
        && lines[i + 2]?.kind !== "add" && prev?.kind !== "del") {
      const refined = wordDiff(line.text, (next as { text: string }).text);
      rows.push(<div key={i} className="diff-del">− {refined.del}</div>);
      rows.push(<div key={i + "a"} className="diff-add">+ {refined.add}</div>);
      i++;
      continue;
    }
    rows.push(
      <div key={i} className={line.kind === "same" ? "" : line.kind === "add" ? "diff-add" : "diff-del"}>
        {line.kind === "add" ? "+ " : line.kind === "del" ? "− " : "  "}{line.text}
      </div>,
    );
  }

  return (
    <div className="drift-block">
      <div className="muted">
        {label} — <span className="diff-del-key">−{dels}</span>{" "}
        <span className="diff-add-key">+{adds}</span> of {Math.max(aCount, bCount)} lines
      </div>
      <pre className="diff">{rows}</pre>
    </div>
  );
}

function toolName(t: { name?: string; function?: { name?: string } }): string {
  return t?.name ?? t?.function?.name ?? "?";
}

function ConfigDrift({ a, b }: { a: Call | null; b: Call | null }) {
  if (!a || !b) return null;
  const rows: { label: string; va: string; vb: string }[] = [
    { label: "model", va: a.model_id, vb: b.model_id },
    {
      label: "temperature",
      va: a.temperature != null ? String(a.temperature) : "default",
      vb: b.temperature != null ? String(b.temperature) : "default",
    },
    {
      label: "tools",
      va: (a.tools ?? []).map(toolName).sort().join(", ") || "none",
      vb: (b.tools ?? []).map(toolName).sort().join(", ") || "none",
    },
  ];
  const drifted = rows.filter((r) => r.va !== r.vb);
  return (
    <div className="drift-block">
      <div className="muted">
        Configuration{drifted.length === 0 ? ": identical in both runs" : " — differences change costs too, not just the prompt"}
      </div>
      {drifted.length > 0 && (
        <table className="rtable" style={{ maxWidth: 640 }}>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <td>{r.label}</td>
                <td className="mono" style={{ color: r.va !== r.vb ? "#e5484d" : undefined }}>{r.va}</td>
                <td className="mono" style={{ color: r.va !== r.vb ? "var(--green)" : undefined }}>{r.vb}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
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
          <ConfigDrift a={ra.firstCall} b={rb.firstCall} />
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
