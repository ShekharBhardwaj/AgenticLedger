import {
  useEffect, useLayoutEffect, useMemo, useRef, useState,
  type KeyboardEvent, type PointerEvent, type CSSProperties,
} from "react";
import { Ban, MessageSquare, Minus, TriangleAlert, Wrench } from "lucide-react";
import { Call, flagBadgeClass, flagInfo, fmtNum, fmtUsd, get, plural, Run, RunCall } from "./api";
import { LabelMark } from "./LabelMarks";
import { Stage } from "./AnatomyStage";

/** The anatomy of a loop, replayed from its recorded calls: the reel.
 *
 *  Time runs left to right, one column per call, column height is what the
 *  model read on that call, on one token scale shared by every lane. Calls
 *  in one conversation touch and form a ribbon; where the session changes
 *  the ribbon is cut. The whole run is there from frame zero as a faint
 *  outline, and playback inks it in.
 *
 *  The live beat is a loop figure of fixed size anchored on the current
 *  column: the column rises, a scan sweeps up into the model node on the
 *  rail, the node lights, a label says what was read and what the model
 *  did, then a loop is drawn from the node and a bead rides it. If the
 *  next call shares the session the loop lands back on the same column
 *  (the result joined the conversation); if the next call starts a new
 *  session the path drops through the cut and the bead fades (the
 *  conversation ended). Under the floor, one bar per conversation.
 *
 *  Honesty rules: every drawn quantity is a stored field. Static CSS is the
 *  end state of every animation and keyframes define only "from", so pause,
 *  step and reduced motion all land on a complete frame. */

export interface Lane {
  id: string;
  run: Run | null;
  calls: RunCall[];
}

/** Everything the model read on one call: fresh tokens plus cache traffic. */
export function contextOf(c: RunCall): number {
  return (c.tokens_in ?? 0) + (c.cache_read_tokens ?? 0) + (c.cache_write_tokens ?? 0);
}

type Kind = "tool" | "answer" | "silent" | "refused" | "error";

interface Beat {
  c: RunCall;
  i: number;
  ctx: number;
  cache: number;
  recorded: boolean;
  fresh: boolean;
  kind: Kind;
  conv: number;
  elapsedMs: number;
  flags: string[];
}

interface Conv {
  first: number;
  last: number;
  count: number;
  iteration: number | null;
  peakCtx: number;
  refused: number;
  errors: number;
  session: string | null;
}

interface Frame { beats: Beat[]; convs: Conv[] }

function kindOf(c: RunCall): Kind {
  const status = c.status_code ?? 200;
  if (status === 402 || status === 429) return "refused";
  if (status >= 400) return "error";
  if (c.tools.length) return "tool";
  if (c.has_text) return "answer";
  return "silent";
}

function buildFrame(calls: RunCall[]): Frame {
  const sorted = [...calls].sort((a, b) => Date.parse(a.timestamp) - Date.parse(b.timestamp));
  const t0 = sorted.length ? Date.parse(sorted[0].timestamp) : 0;
  const beats: Beat[] = [];
  const convs: Conv[] = [];
  sorted.forEach((c, i) => {
    // A new conversation starts whenever the session changes. Consecutive
    // calls with no session id count as one conversation: the record
    // cannot tell them apart, so the drawing does not pretend to.
    const fresh = i === 0 || c.session_id !== sorted[i - 1].session_id;
    const status = c.status_code ?? 200;
    const kind = kindOf(c);
    const recorded = !(status < 400 && c.tokens_in == null
      && c.cache_read_tokens == null && c.cache_write_tokens == null);
    const ctx = recorded ? contextOf(c) : 0;
    const cache = recorded ? (c.cache_read_tokens ?? 0) : 0;
    let flags: string[] = [];
    if (c.loop_flags) {
      try {
        const v = JSON.parse(c.loop_flags);
        flags = Array.isArray(v) ? v.map(String) : [String(v)];
      } catch { flags = [c.loop_flags]; }
    }
    const t = Date.parse(c.timestamp);
    if (fresh) {
      convs.push({ first: i, last: i, count: 0, iteration: c.iteration, peakCtx: 0,
                   refused: 0, errors: 0, session: c.session_id });
    }
    const conv = convs[convs.length - 1];
    conv.last = i;
    conv.count += 1;
    conv.peakCtx = Math.max(conv.peakCtx, ctx);
    if (kind === "refused") conv.refused += 1;
    if (kind === "error") conv.errors += 1;
    beats.push({ c, i, ctx, cache, recorded, fresh, kind, conv: convs.length - 1,
                 elapsedMs: Math.max(0, (Number.isNaN(t) ? t0 : t) - t0), flags });
  });
  return { beats, convs };
}

/** Plain words for a lane's shape, derived from its calls. */
/** Plain words for a lane's shape, derived from its calls. The style name
 *  is a convention attached to a measured fact (how the conversations and
 *  iterations line up), kept apart from whatever the run was labelled, so
 *  a wrong label and the record can disagree in plain sight. */
export function describeShape(calls: RunCall[]): string {
  if (!calls.length) return "no calls recorded";
  const { beats, convs } = buildFrame(calls);
  const iterations = new Set(beats.map((b) => b.c.iteration ?? "")).size;
  const ctx = beats.map((b) => b.ctx);
  const n = convs.length;
  const toolCalls = beats.filter((b) => b.kind === "tool").length;
  if (n === 1) {
    const style = toolCalls * 2 >= beats.length ? "One continuous conversation (ReAct style)" : "One conversation";
    return `${style}: ${plural(beats.length, "call")}, context carried throughout, `
      + `${fmtNum(ctx[0])} to ${fmtNum(ctx[ctx.length - 1])} tokens read`;
  }
  if (n === iterations) {
    const avg = Math.round(ctx.reduce((a, b) => a + b, 0) / ctx.length);
    return `Fresh context every iteration (Ralph style): ${plural(n, "conversation")}, `
      + `${plural(beats.length, "call")}, about ${fmtNum(avg)} tokens read each`;
  }
  return `Mixed shape: ${plural(n, "conversation")} over ${plural(iterations, "iteration")}, `
    + `${plural(beats.length, "call")}, context dropped ${n - 1} ${n - 1 === 1 ? "time" : "times"}`;
}

function fmtElapsed(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const two = (v: number) => String(v).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(sec)}` : `${m}:${two(sec)}`;
}

function fmtLatency(ms: number | null): string {
  if (!ms || ms <= 0) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

function didText(b: Beat): string {
  const c = b.c;
  const status = c.status_code ?? 200;
  if (b.kind === "refused") {
    return status === 402 || c.wall ? "refused at the wall, no reply" : "stopped with 429, no reply";
  }
  if (b.kind === "error") return `error ${status}, no reply`;
  let s: string;
  if (b.kind === "tool") {
    const names = Array.from(new Set(c.tools));
    s = names.length <= 2
      ? `calls ${names.join(" + ")}`
      : `calls ${names.slice(0, 2).join(" + ")} + ${names.length - 2} more`;
  } else if (b.kind === "answer") {
    s = "answers in text";
  } else {
    const reason = c.stop_reason;
    s = reason && reason !== "end_turn" && reason !== "stop" ? `stops: ${reason}` : "no tool call, no text";
  }
  const lat = fmtLatency(c.latency_ms);
  return lat ? `${s} in ${lat}` : s;
}

/** Line A: what the model read. Returns the words around the number so
 *  the number alone can be set in mono. */
function readParts(b: Beat, prev: Beat | undefined): { before: string; n: string | null; after: string } {
  if (!b.recorded) return { before: "read: not recorded", n: null, after: "" };
  if ((b.kind === "refused" || b.kind === "error") && b.ctx === 0) return { before: "nothing read", n: null, after: "" };
  const n = fmtNum(b.ctx);
  if (b.i === 0) return { before: "first call, reads ", n, after: "" };
  if (b.fresh) return { before: "fresh start, reads ", n, after: "" };
  if (prev && prev.ctx > b.ctx) return { before: "reads ", n, after: ", shorter than last time" };
  return { before: "reads ", n, after: "" };
}

function wentText(b: Beat, next: Beat | undefined, carried: boolean): string {
  if (!next) return `last recorded call, ${plural(b.i + 1, "call")} in all`;
  if (!carried) return "the conversation ends; the next call starts fresh";
  if (b.kind === "tool") return "the result joins the conversation";
  if (b.kind === "refused" || b.kind === "error") return "nothing comes back; the conversation continues";
  return "the conversation continues";
}

function flagKind(flags: string[]): "warn" | "good" | "info" {
  const kinds = flags.map((f) => flagInfo(f).kind);
  return kinds.includes("warn") ? "warn" : kinds.includes("good") ? "good" : "info";
}

function columnTitle(b: Beat): string {
  const c = b.c;
  const parts = [`call ${b.i + 1}`];
  if (!b.recorded) parts.push("usage not recorded");
  else {
    parts.push(`${fmtNum(b.ctx)} tokens read${b.cache ? ` (${fmtNum(b.cache)} from cache)` : ""}`);
    if (c.tokens_out != null) parts.push(`${fmtNum(c.tokens_out)} written`);
  }
  parts.push(didText(b));
  parts.push(c.cost_usd == null ? "unpriced" : fmtUsd(c.cost_usd));
  if (b.flags.length) parts.push(`flagged: ${b.flags.map((f) => flagInfo(f).title).join(", ")}`);
  return parts.join(" · ");
}

function convTitle(k: number, cv: Conv): string {
  let s = `conversation ${k + 1}: ${plural(cv.count, "call")}`;
  if (cv.iteration != null) s += `, iteration ${cv.iteration}`;
  s += `, up to ${fmtNum(cv.peakCtx)} tokens read`;
  if (cv.refused) s += `, ${cv.refused} refused`;
  if (cv.errors) s += `, ${plural(cv.errors, "error")}`;
  return s;
}

const KIND_ICON = { tool: Wrench, answer: MessageSquare, silent: Minus, refused: Ban, error: TriangleAlert } as const;

// ── Geometry ─────────────────────────────────────────────────────────────────
// Fixed rows: the plot between y_top and the floor, the conversation bars
// and tick labels under the floor, and LOOP_H of headroom above the peak
// column for the loop figure.
const GUT_L = 48, GUT_R = 8, Y_TOP = 76;
// The model node sits this far above the current column, so the loop
// figure is the same size on every call; its height on the page is then
// the context the model carried into that call.
const LOOP_H = 44;
const Y_CUT_TOP = Y_TOP - LOOP_H;
const TIERS = {
  S: { H: 236, yBase: 196 },
  M: { H: 260, yBase: 220 },
  L: { H: 284, yBase: 244 },
} as const;
type Tier = keyof typeof TIERS;

function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const sync = () => setMatches(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, [query]);
  return matches;
}

const SPEEDS: { label: string; ms: number }[] = [
  { label: "slow", ms: 2400 },
  { label: "normal", ms: 1400 },
  { label: "fast", ms: 700 },
];

export function LoopAnatomy({ lanes, onOpenSession, autoplay = false }: {
  lanes: Lane[];
  onOpenSession?: (s: string) => void;
  autoplay?: boolean;
}) {
  const longest = Math.max(0, ...lanes.map((l) => l.calls.length));
  // Each lane keeps its own position: play and step move every lane by one
  // of ITS calls (a lane stops at its own end), so two runs of different
  // length stay side by side call for call, forwards and backwards.
  const [positions, setPositions] = useState<number[]>(() => lanes.map(() => 0));
  const lastOf = (i: number) => Math.max(0, lanes[i].calls.length - 1);
  const allAtEnd = lanes.every((l, i) => positions[i] >= lastOf(i));
  const [playing, setPlaying] = useState(autoplay);
  const [speed, setSpeed] = useState(1);
  const timer = useRef<number | null>(null);

  // One shared scale across lanes: a tall column means more context, not a
  // different lane; a thick loop means more written, not a different lane.
  const allCalls = lanes.flatMap((l) => l.calls);
  const peak = Math.max(1, ...allCalls.map(contextOf));
  const peakOut = Math.max(1, ...allCalls.map((c) => c.tokens_out ?? 0));
  const hasCache = allCalls.some((c) => (c.cache_read_tokens ?? 0) > 0);

  const lengths = lanes.map((l) => l.calls.length).join(",");
  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      setPositions((ps) => {
        const next = ps.map((p, i) => Math.min(p + 1, lastOf(i)));
        if (next.every((p, i) => p === ps[i])) { setPlaying(false); return ps; }
        return next;
      });
    }, SPEEDS[speed].ms);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, speed, lengths]);

  // Lanes added or reloaded (new calls arrived) keep their place.
  useEffect(() => {
    setPositions((ps) => lanes.map((_, i) => Math.min(ps[i] ?? 0, lastOf(i))));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lanes.length, lengths]);

  const stepMs = SPEEDS[speed].ms;
  const atEnd = longest === 0 || allAtEnd;
  const atStart = positions.every((p) => p === 0);
  const stepAll = (d: number) => {
    setPlaying(false);
    setPositions((ps) => ps.map((p, i) => Math.max(0, Math.min(lastOf(i), p + d))));
  };
  const jumpLane = (lane: number, i: number) => {
    setPlaying(false);
    setPositions((ps) => ps.map((p, k) => (k === lane ? Math.max(0, Math.min(lastOf(k), i)) : p)));
  };
  const toggle = () => {
    if (longest === 0) return;
    if (atEnd) { setPositions(lanes.map(() => 0)); setPlaying(true); } else setPlaying((p) => !p);
  };
  const shown = Math.max(0, ...positions) + 1;

  return (
    <section className="anatomy" aria-label="Loop anatomy" style={{ ["--step" as string]: `${stepMs}ms` }}>
      <div className="anat-controls">
        <button type="button" className="link-btn anat-play" onClick={toggle} disabled={longest === 0}>
          {playing ? "Pause" : atEnd && longest > 0 ? "Replay" : "Play"}
        </button>
        <button type="button" className="link-btn" disabled={atStart} onClick={() => stepAll(-1)}>‹ step</button>
        <button type="button" className="link-btn" disabled={atEnd} onClick={() => stepAll(1)}>step ›</button>
        <span className="anat-speed">
          {SPEEDS.map((s, i) => (
            <button key={s.label} type="button" className={`link-btn ${i === speed ? "on" : ""}`} onClick={() => setSpeed(i)}>{s.label}</button>
          ))}
        </span>
        <span className="muted anat-pos" title="Each lane steps through its own calls; the count follows the lane that is furthest along">call {Math.min(shown, longest)} of {longest}</span>
      </div>
      <div className={`anat-grid lanes-${lanes.length}`}>
        {lanes.map((lane, k) => (
          <LaneView key={lane.id} lane={lane} pos={Math.min(positions[k] ?? 0, lastOf(k))} playing={playing}
                    peak={peak} peakOut={peakOut} longest={longest} hasCache={hasCache}
                    onJump={(i) => jumpLane(k, i)} onTogglePlay={toggle} onOpenSession={onOpenSession} />
        ))}
      </div>
    </section>
  );
}

function LaneView({ lane, pos: posIn, playing, peak, peakOut, longest, hasCache, onJump, onTogglePlay, onOpenSession }: {
  lane: Lane; pos: number; playing: boolean;
  peak: number; peakOut: number; longest: number; hasCache: boolean;
  onJump: (i: number) => void; onTogglePlay: () => void; onOpenSession?: (s: string) => void;
}) {
  const frame = useMemo(() => buildFrame(lane.calls), [lane.calls]);
  const { beats, convs } = frame;
  const N = beats.length;

  // The scene mounts only once calls have loaded, so the measurement
  // effect keys on N: an empty lane has no scene to measure.
  const sceneRef = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(600);
  useLayoutEffect(() => {
    const el = sceneRef.current;
    if (!el) return;
    const sync = () => { if (el.clientWidth) setW(el.clientWidth); };
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(el);
    return () => ro.disconnect();
  }, [N]);

  const isM = useMedia("(min-width: 1100px)");
  const isL = useMedia("(min-width: 1400px)");
  const tier: Tier = isL ? "L" : isM ? "M" : "S";
  const { H, yBase } = TIERS[tier];
  const plotH = yBase - Y_TOP;
  const yMid = Y_TOP + plotH / 2;

  const reelW = Math.max(1, W - GUT_L - GUT_R);
  const pitch = Math.max(5, Math.min(72, Math.floor(reelW / Math.max(1, longest))));
  const svgW = Math.max(W, GUT_L + longest * pitch + GUT_R);

  const pos = Math.min(posIn, Math.max(0, N - 1));
  const cur = beats[pos];
  const prev = beats[pos - 1];
  const next = beats[pos + 1];
  const carried = !!next && !!cur && next.c.session_id === cur.c.session_id;

  const xOf = (i: number) => GUT_L + i * pitch;
  const xcOf = (i: number) => xOf(i) + pitch / 2;
  const hOf = (b: Beat) => (b.ctx === 0 ? 0 : Math.max(2, (plotH * b.ctx) / peak));
  const hcOf = (b: Beat) => Math.min(hOf(b), (plotH * b.cache) / peak);

  const xc = cur ? xcOf(pos) : GUT_L;
  const prevXRef = useRef<number | null>(null);
  const xPrev = prevXRef.current ?? xc;
  useEffect(() => { prevXRef.current = xc; });

  // The column rises from the previous call's height when the conversation
  // carried on, and from the floor on a fresh start.
  const fromRatio = cur && prev && !cur.fresh && hOf(prev) > 0 && hOf(cur) > 0 ? hOf(prev) / hOf(cur) : 0;

  useEffect(() => {
    const el = sceneRef.current;
    if (!el || svgW <= W) return;
    el.scrollTo({ left: Math.max(0, xc - W / 2), behavior: playing ? "smooth" : "auto" });
  }, [pos, W, playing, svgW, xc]);

  // The stage needs the full record of the current conversation (messages,
  // tool calls, results); fetch each session once and keep it. The next
  // conversation is fetched a beat early so a fresh start has its cards.
  const [rowsBySid, setRowsBySid] = useState<Record<string, Call[]>>({});
  const curSid = cur?.c.session_id ?? null;
  const nextSid = next?.c.session_id ?? null;
  useEffect(() => {
    let live = true;
    for (const sid of [curSid, nextSid]) {
      if (!sid || rowsBySid[sid]) continue;
      get<Call[]>(`/session/${encodeURIComponent(sid)}`)
        .then((rows) => { if (live) setRowsBySid((m) => (m[sid] ? m : { ...m, [sid]: rows })); })
        .catch(() => { if (live) setRowsBySid((m) => (m[sid] ? m : { ...m, [sid]: [] })); });
    }
    return () => { live = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curSid, nextSid]);
  const curRows = curSid ? rowsBySid[curSid] : undefined;
  const curFull = cur && curRows ? curRows.find((r) => r.action_id === cur.c.action_id) ?? null : null;
  const nextFull = next && carried && curRows ? curRows.find((r) => r.action_id === next.c.action_id) ?? null : null;
  const toolsUsed = useMemo(() => Array.from(new Set(beats.slice(0, pos + 1).flatMap((b) => b.c.tools))), [beats, pos]);
  const filesSoFar = useMemo(() => {
    const seen: string[] = [];
    for (const b of beats.slice(0, pos + 1)) for (const f of b.c.paths ?? []) if (!seen.includes(f)) seen.push(f);
    return seen;
  }, [beats, pos]);

  const totals = useMemo(() => {
    const upto = beats.slice(0, pos + 1);
    return {
      read: upto.reduce((a, b) => a + b.ctx, 0),
      out: upto.reduce((a, b) => a + (b.c.tokens_out ?? 0), 0),
      cost: upto.reduce((a, b) => a + (b.c.cost_usd ?? 0), 0),
      unpriced: upto.filter((b) => b.c.cost_usd == null).length,
    };
  }, [beats, pos]);

  const dragging = useRef(false);
  const jumpAt = (clientX: number) => {
    const el = sceneRef.current;
    if (!el || !N) return;
    const r = el.getBoundingClientRect();
    const i = Math.max(0, Math.min(N - 1, Math.floor((clientX - r.left + el.scrollLeft - GUT_L) / pitch)));
    if (i !== pos || playing) onJump(i);
  };
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    dragging.current = true;
    sceneRef.current?.setPointerCapture(e.pointerId);
    jumpAt(e.clientX);
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => { if (dragging.current) jumpAt(e.clientX); };
  const endDrag = () => { dragging.current = false; };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!N) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); onJump(pos - 1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); onJump(pos + 1); }
    else if (e.key === "Home") { e.preventDefault(); onJump(0); }
    else if (e.key === "End") { e.preventDefault(); onJump(N - 1); }
    else if (e.key === " ") { e.preventDefault(); onTogglePlay(); }
    else if (e.key === "Enter" && cur?.c.session_id) { onOpenSession?.(cur.c.session_id); }
  };

  const name = lane.run?.label ?? lane.id;
  const hatchId = `anat-hatch-${lane.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;

  // ── Static groups (memoized on the geometry, not on the playhead) ──────────
  const grid = useMemo(() => (
    <g className="anat-grid">
      <line x1={GUT_L} x2={svgW - GUT_R} y1={Y_TOP} y2={Y_TOP} />
      <line x1={GUT_L} x2={svgW - GUT_R} y1={yMid} y2={yMid} />
      <line x1={GUT_L} x2={svgW - GUT_R} y1={yBase} y2={yBase} className="floor" />
    </g>
  ), [svgW, yMid, yBase]);

  const ghost = useMemo(() => (
    <g className="anat-ghost">
      {convs.map((cv, k) => {
        let d = `M ${xOf(cv.first) + (k > 0 ? 2 : 0)},${yBase}`;
        for (let i = cv.first; i <= cv.last; i++) {
          const b = beats[i];
          const top = b.recorded ? yBase - hOf(b) : yBase - 5;
          const x0 = xOf(i) + (b.fresh && i > 0 ? 2 : 0);
          d += ` V ${top} H ${x0 + pitch - (b.fresh && i > 0 ? 2 : 0)}`;
          if (i === cv.last) d += ` V ${yBase}`;
          else if (!beats[i + 1].fresh) d += ` V ${beats[i + 1].recorded ? yBase - hOf(beats[i + 1]) : yBase - 5}`;
        }
        return <path key={k} d={d} />;
      })}
    </g>
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ), [beats, convs, pitch, peak, yBase]);

  const ticks = useMemo(() => {
    if (!N) return null;
    const k = Math.max(1, Math.ceil(longest / Math.max(2, Math.floor(reelW / 64))));
    const CH = 6.2, GAP = 14;   // mono glyph width at 10px, and the gap between labels
    type Tick = { i: number; x: number; anchor: "start" | "middle" | "end"; text: string; left: number; right: number };
    const make = (i: number): Tick => {
      const text = `${i + 1} · ${fmtElapsed(beats[i].elapsedMs)}`;
      const w = text.length * CH;
      const anchor = i === 0 ? "start" : i === N - 1 ? "end" : "middle";
      const x = i === 0 ? xOf(0) : i === N - 1 ? xOf(i) + pitch : xcOf(i);
      const left = anchor === "start" ? x : anchor === "end" ? x - w : x - w / 2;
      return { i, x, anchor, text, left, right: left + w };
    };
    const last = make(N - 1);
    const kept: Tick[] = [];
    for (let i = 0; i < N - 1; i += k) {
      const t = make(i);
      const prev = kept[kept.length - 1];
      if (prev && t.left < prev.right + GAP) continue;
      if (N > 1 && t.right > last.left - GAP) continue;
      kept.push(t);
    }
    if (N > 1) kept.push(last);
    return (
      <g className="anat-axis">
        {kept.map((t) => (
          <text key={t.i} x={t.x} y={yBase + 33} textAnchor={t.anchor}>{t.text}</text>
        ))}
      </g>
    );
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [beats, N, longest, reelW, pitch, yBase]);

  // Cuts not yet reached are part of the ghost, drawn faint.
  const futureCuts = beats.filter((b) => b.fresh && b.i > 0 && b.i > pos);
  const pastCuts = beats.filter((b) => b.fresh && b.i > 0 && b.i <= pos);

  function column(b: Beat, x: number, w: number): JSX.Element {
    if (!b.recorded) return <rect className="unrecorded" x={x} y={yBase - 5} width={w} height={5} fill={`url(#${hatchId})`} />;
    if (b.ctx === 0) return <rect className={`tick-floor ${b.kind}`} x={x} y={yBase - 2} width={w} height={2} />;
    const h = hOf(b), hc = hcOf(b);
    return (
      <>
        {hc > 0 && <rect className="cache" x={x} y={yBase - hc} width={w} height={hc} />}
        <rect className="fresh" x={x} y={yBase - h} width={w} height={Math.max(0, h - hc)} />
      </>
    );
  }

  function flagTick(b: Beat, xcenter: number): JSX.Element | null {
    if (!b.flags.length) return null;
    const top = b.recorded ? yBase - hOf(b) : yBase - 5;
    return <rect className={`flag ${flagKind(b.flags)}`} x={xcenter - 1.5} y={top - 6} width={3} height={3} />;
  }

  // The live figure. Coordinates are relative to the current column's
  // center (x = 0), and the group is translated to it by --x.
  const inset = cur && cur.fresh && pos > 0 ? 2 : 0;
  const colX = -pitch / 2 + inset;
  const colW = pitch - inset;
  const curTop = cur ? (cur.recorded ? yBase - hOf(cur) : yBase - 5) : yBase;
  const nodeY = curTop - LOOP_H;
  const xCut = pitch / 2 + 1;
  const arcD = !cur || !next ? "" : carried
    ? `M 0,${nodeY} C 36,${nodeY} 36,${curTop} 3,${curTop}`
    : `M 0,${nodeY} C 36,${nodeY} 36,${nodeY + 30} ${xCut},${nodeY + LOOP_H} L ${xCut},${yBase + 12}`;
  const silentNode = !!cur && (cur.kind === "refused" || cur.kind === "error");
  const narrow = W < 420;
  const Icon = cur ? KIND_ICON[cur.kind] : Minus;
  const read = cur ? readParts(cur, prev) : null;
  const did = cur ? didText(cur) : "";
  const went = cur ? wentText(cur, next, carried) : "";
  const beatKey = `${lane.id}-${pos}`;
  const nowVars = {
    ["--x" as string]: `${xc}px`,
    ["--x-prev" as string]: `${xPrev}px`,
    ["--from" as string]: String(fromRatio),
    ["--base" as string]: `${yBase}px`,
    ["--scan" as string]: `${yBase - nodeY}px`,
    ["--ny" as string]: `${nodeY}px`,
    ["--w" as string]: `${W}px`,
  } as CSSProperties;

  const valueText = cur ? `call ${pos + 1} of ${N}: ${read!.before}${read!.n ?? ""}${read!.after} · ${did} · ${went}` : "";

  return (
    <div className={`anat-lane tier-${tier} ${narrow ? "narrow" : ""} ${playing ? "playing" : ""}`}>
      <div className="anat-head">
        <span className="anat-name"><LabelMark icon={lane.run?.icon} color={lane.run?.color} size={14} />{name}</span>
        <span className="anat-model">{cur?.c.model_id ?? "model not recorded"}</span>
      </div>
      <div className="anat-sub">
        <span className="anat-shape">{describeShape(lane.calls)}</span>
        {N > 0 && (
          <span className="anat-legend">
            <span><i className="sw" />read fresh</span>
            {hasCache && <span><i className="sw cache" />read from cache</span>}
            <span>
              <svg width="14" height="10" viewBox="0 0 14 10" aria-hidden="true">
                <path d="M1 2 C 12 2, 12 9, 3 9" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
              result returns
            </span>
            <span><i className="cut" />conversation ends</span>
          </span>
        )}
      </div>

      {N === 0 ? (
        <div className="empty">No calls recorded for this run.</div>
      ) : (
        <>
          <div className="anat-caption" key={`c-${beatKey}`}>
            {read && (
              <>
                <span className="clause read">{read.before}{read.n != null && <b>{read.n}</b>}{read.after}</span>
                <span className="sep"> · </span>
                <span className={`clause did ${cur?.kind ?? ""}`}><Icon size={12} aria-hidden="true" />{did}</span>
                <span className="sep"> · </span>
              </>
            )}
            <span className="clause went">{went}</span>
          </div>

          {cur && (
            <Stage
              laneId={lane.id}
              beatKey={beatKey}
              call={curFull}
              nextCall={nextFull}
              carried={carried}
              endsHere={!!next && !carried}
              last={!next}
              fresh={cur.fresh}
              kind={cur.kind}
              contextTokens={cur.ctx}
              peak={peak}
              toolsOffered={(curFull?.tools ?? []).map((t) => String(t.name ?? t.function?.name ?? "")).filter(Boolean)}
              toolsUsed={toolsUsed}
              filesSoFar={filesSoFar}
              filesNow={cur.c.paths ?? []}
              onOpenSession={cur.c.session_id ? () => onOpenSession?.(cur.c.session_id!) : undefined}
            />
          )}

          <div className="anat-plot">
            <svg className="anat-yaxis" width={44} height={H} aria-hidden="true">
              <text x={40} y={Y_TOP + 3} textAnchor="end">{fmtNum(peak)}</text>
              <text x={40} y={yMid + 3} textAnchor="end">{fmtNum(Math.round(peak / 2))}</text>
              <text x={40} y={yBase + 3} textAnchor="end">0</text>
            </svg>
            <div
              ref={sceneRef}
              className="anat-scene"
              role="slider"
              tabIndex={0}
              aria-label={`Replay position for ${name}`}
              title="Column height is tokens the model read, one scale across lanes. Drag to scrub; click a conversation bar to jump to its start; double-click opens it."
              aria-valuemin={1}
              aria-valuemax={N}
              aria-valuenow={pos + 1}
              aria-valuetext={valueText}
              aria-live={playing ? "off" : "polite"}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              onDoubleClick={() => cur?.c.session_id && onOpenSession?.(cur.c.session_id)}
              onKeyDown={onKeyDown}
            >
              <div className="anat-reel" style={{ width: svgW, height: H }}>
                <svg className="anat-svg" width={svgW} height={H}>
                  <defs>
                    <pattern id={hatchId} width="4" height="4" patternUnits="userSpaceOnUse">
                      <path d="M0 4 L4 0" stroke="currentColor" strokeWidth="1" />
                    </pattern>
                  </defs>
                  {grid}
                  {ghost}
                  <g className="anat-ghost">
                    {futureCuts.map((b) => (
                      <line key={b.c.action_id} x1={xOf(b.i) + 1} x2={xOf(b.i) + 1} y1={yBase} y2={Y_CUT_TOP} className="cut" />
                    ))}
                  </g>
                  <g className="anat-ribbon">
                    {beats.slice(0, pos).map((b) => {
                      const ins = b.fresh && b.i > 0 ? 2 : 0;
                      const recent = pos - b.i;
                      return (
                        <g key={b.c.action_id} className={`beat past ${recent === 1 ? "recent-1" : recent === 2 ? "recent-2" : ""}`}>
                          <title>{columnTitle(b)}</title>
                          {column(b, xOf(b.i) + ins, pitch - ins)}
                          {flagTick(b, xcOf(b.i))}
                        </g>
                      );
                    })}
                  </g>
                  <g className="anat-cuts">
                    {pastCuts.map((b) => (
                      <line key={b.c.action_id} x1={xOf(b.i) + 1} x2={xOf(b.i) + 1} y1={yBase} y2={Y_CUT_TOP} />
                    ))}
                  </g>
                  <g className="anat-dots">
                    {beats.slice(0, pos).map((b) => {
                      const K = KIND_ICON[b.kind];
                      const top = b.recorded ? yBase - hOf(b) : yBase - 5;
                      return pitch >= 14
                        ? <K key={b.c.action_id} x={xcOf(b.i) - 5} y={top - 19} width={10} height={10} className={`glyph ${b.kind}`} />
                        : <circle key={b.c.action_id} cx={xcOf(b.i)} cy={top - 14} r={2.5} className={b.kind} />;
                    })}
                  </g>
                  <g className="anat-convs">
                    {convs.map((cv, k) => {
                      const x0 = xOf(cv.first) + (k > 0 ? 2 : 0);
                      const w = xOf(cv.last) + pitch - x0;
                      const current = cur ? cur.conv === k : false;
                      return (
                        <g key={k}
                           onPointerDown={(e) => e.stopPropagation()}
                           onClick={(e) => { e.stopPropagation(); onJump(cv.first); }}
                           onDoubleClick={(e) => { e.stopPropagation(); if (cv.session) onOpenSession?.(cv.session); }}>
                          <title>{convTitle(k, cv)}</title>
                          <rect className="anat-conv-hit" x={x0} y={yBase + 12} width={w} height={12} />
                          <rect className={`anat-conv ${current ? "current" : ""}`} x={x0} y={yBase + 17} width={w} height={2} />
                        </g>
                      );
                    })}
                  </g>
                  {ticks}
                  {cur && (
                    <g className="anat-now" key={beatKey} style={nowVars}>
                      <g className="rise">
                        <title>{columnTitle(cur)}</title>
                        {column(cur, colX, colW)}
                        {flagTick(cur, 0)}
                      </g>
                      {!silentNode && cur.recorded && <rect className="scan" x={colX} y={yBase - 2} width={colW} height={2} />}
                      <circle className={`node ${cur.kind} ${silentNode ? "off" : ""}`} cx={0} cy={nodeY} r={6} />
                      {next && (
                        <path className={`arc ${cur.kind} ${carried ? "carried" : "dropped"}`} d={arcD} pathLength={1}
                              style={{ strokeWidth: 1 + (3 * (cur.c.tokens_out ?? 0)) / peakOut }} />
                      )}
                      {next && !carried && <line className="cut" x1={xCut} x2={xCut} y1={yBase} y2={Y_CUT_TOP} />}
                      {!next && <rect className="end" x={14} y={nodeY - 2} width={4} height={4} />}
                    </g>
                  )}
                </svg>
                {cur && (
                  <div className="anat-beat" key={`b-${beatKey}`} style={nowVars}>
                    {next && (
                      <span className={`anat-bead ${carried ? "carried" : "dropped"} ${cur.kind}`}
                            style={{ offsetPath: `path("${arcD}")` }} />
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="anat-counters">
            <span>call <b>{pos + 1}</b> / {N}</span>
            {cur?.c.iteration != null && <span>iteration <b>{cur.c.iteration}</b></span>}
            {cur && <span><b>{fmtElapsed(cur.elapsedMs)}</b> elapsed</span>}
            <span><b>{fmtNum(totals.read)}</b> read so far</span>
            <span><b>{fmtNum(totals.out)}</b> written</span>
            <span><b>{fmtUsd(totals.cost)}</b> so far{totals.unpriced ? ` (${totals.unpriced} unpriced)` : ""}</span>
            {cur?.flags.map((f) => (
              <span key={f} className={`badge ${flagBadgeClass(f)}`} title={flagInfo(f).detail}>{flagInfo(f).title}</span>
            ))}
          </div>

        </>
      )}
    </div>
  );
}
