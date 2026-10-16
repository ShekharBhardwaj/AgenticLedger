import { useEffect, useMemo, useRef, useState } from "react";
import { fmtNum, fmtUsd, plural, Run, RunCall } from "./api";
import { LabelMark } from "./LabelMarks";

/** The anatomy of a loop, replayed from its recorded calls.
 *
 *  A loop is a conversation that keeps calling a model: the model reads a
 *  context, makes a move (calls a tool, or answers), and the result comes
 *  back into the conversation, or the conversation ends and the next call
 *  starts fresh. That is the whole difference between loop shapes, so the
 *  stage shows exactly that, one recorded call at a time: the context pile
 *  fills to what the model read, a pulse goes into the model, the move goes
 *  out to the tool, and the result either rejoins the pile or is dropped.
 *  Several lanes play side by side from the same start, each at its own
 *  recorded pace, on one shared height scale. Nothing here is staged: every
 *  number is a stored call. */

export interface Lane {
  id: string;
  run: Run | null;
  calls: RunCall[];
}

/** Everything the model read on one call: fresh tokens plus cache traffic. */
export function contextOf(c: RunCall): number {
  return (c.tokens_in ?? 0) + (c.cache_read_tokens ?? 0) + (c.cache_write_tokens ?? 0);
}

/** A new conversation starts whenever the session changes. */
function isFresh(calls: RunCall[], i: number): boolean {
  return i === 0 || calls[i].session_id !== calls[i - 1].session_id;
}

type Move = { kind: "tool" | "answer" | "refused" | "error"; label: string };

function moveOf(c: RunCall): Move {
  if (c.status_code != null && c.status_code >= 400) {
    return c.status_code === 402 || c.status_code === 429
      ? { kind: "refused", label: "refused at the wall" }
      : { kind: "error", label: `error ${c.status_code}` };
  }
  if (c.tools.length) {
    const names = Array.from(new Set(c.tools));
    return { kind: "tool", label: names.length > 2 ? `${names.slice(0, 2).join(" + ")} +${names.length - 2}` : names.join(" + ") };
  }
  return { kind: "answer", label: "answer" };
}

/** Plain words for a lane's shape, derived from its calls. */
export function describeShape(calls: RunCall[]): string {
  if (!calls.length) return "no calls recorded";
  const sessions = new Set(calls.map((c) => c.session_id ?? "")).size;
  const iterations = new Set(calls.map((c) => c.iteration ?? "")).size;
  const ctx = calls.map(contextOf);
  const first = ctx[0], last = ctx[ctx.length - 1], peak = Math.max(...ctx);
  const parts = [
    `${plural(sessions, "conversation")}`,
    `${plural(iterations, "iteration")}`,
    `${plural(calls.length, "call")}`,
  ];
  const shape = sessions === 1
    ? `context carried throughout, ${fmtNum(first)} to ${fmtNum(last)} tokens (peak ${fmtNum(peak)})`
    : sessions === iterations && iterations > 1
      ? `fresh context every iteration, ${fmtNum(Math.round(ctx.reduce((a, b) => a + b, 0) / ctx.length))} tokens on average (peak ${fmtNum(peak)})`
      : `context dropped ${sessions - 1} ${sessions - 1 === 1 ? "time" : "times"}, peak ${fmtNum(peak)} tokens`;
  return `${parts.join(" · ")} · ${shape}`;
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
  const [tick, setTick] = useState(0);
  const [playing, setPlaying] = useState(autoplay);
  const [speed, setSpeed] = useState(1);
  const timer = useRef<number | null>(null);

  // One shared height scale, so a tall pile means more context, not a
  // different lane.
  const peak = Math.max(1, ...lanes.flatMap((l) => l.calls.map(contextOf)));

  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      setTick((t) => {
        if (t + 1 >= longest) { setPlaying(false); return t; }
        return t + 1;
      });
    }, SPEEDS[speed].ms);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [playing, speed, longest]);

  // A lane that was reloaded (new calls arrived) keeps its place.
  useEffect(() => { if (tick >= longest) setTick(Math.max(0, longest - 1)); }, [longest, tick]);

  const stepMs = SPEEDS[speed].ms;
  const atEnd = longest === 0 || tick >= longest - 1;

  return (
    <section className="anatomy" aria-label="Loop anatomy" style={{ ["--step" as string]: `${stepMs}ms` }}>
      <div className="anat-controls">
        <button type="button" className="link-btn anat-play" onClick={() => {
          if (atEnd) { setTick(0); setPlaying(true); } else setPlaying((p) => !p);
        }} disabled={longest === 0}>
          {playing ? "Pause" : atEnd && longest > 0 ? "Replay" : "Play"}
        </button>
        <button type="button" className="link-btn" disabled={tick === 0} onClick={() => { setPlaying(false); setTick((t) => Math.max(0, t - 1)); }}>‹ step</button>
        <button type="button" className="link-btn" disabled={atEnd} onClick={() => { setPlaying(false); setTick((t) => Math.min(longest - 1, t + 1)); }}>step ›</button>
        <span className="anat-speed">
          {SPEEDS.map((s, i) => (
            <button key={s.label} type="button" className={`link-btn ${i === speed ? "on" : ""}`} onClick={() => setSpeed(i)}>{s.label}</button>
          ))}
        </span>
        <span className="muted anat-pos">call {Math.min(tick + 1, longest)} of {longest}</span>
      </div>
      <div className={`anat-grid lanes-${lanes.length}`}>
        {lanes.map((lane) => (
          <LaneView key={lane.id} lane={lane} tick={tick} peak={peak} playing={playing}
                    onJump={(i) => { setPlaying(false); setTick(i); }} onOpenSession={onOpenSession} />
        ))}
      </div>
    </section>
  );
}

function LaneView({ lane, tick, peak, playing, onJump, onOpenSession }: {
  lane: Lane; tick: number; peak: number; playing: boolean;
  onJump: (i: number) => void; onOpenSession?: (s: string) => void;
}) {
  const { calls, run } = lane;
  const pos = Math.min(tick, Math.max(0, calls.length - 1));
  const finished = calls.length > 0 && tick > calls.length - 1;
  const cur = calls[pos];
  const next = calls[pos + 1];
  const fresh = cur ? isFresh(calls, pos) : false;
  // Where the result goes: back into this conversation, or nowhere, because
  // the next call starts fresh (or this was the last call).
  const carried = !!next && next.session_id === cur?.session_id;
  const move = cur ? moveOf(cur) : null;
  const ctx = cur ? contextOf(cur) : 0;

  const totals = useMemo(() => {
    const upto = calls.slice(0, pos + 1);
    return {
      read: upto.reduce((a, c) => a + contextOf(c), 0),
      out: upto.reduce((a, c) => a + (c.tokens_out ?? 0), 0),
      cost: upto.reduce((a, c) => a + (c.cost_usd ?? 0), 0),
    };
  }, [calls, pos]);

  const name = run?.label ?? lane.id;
  const model = cur?.model_id ? cur.model_id.replace(/^.*\//, "") : "model";

  return (
    <div className={`anat-lane ${finished ? "finished" : ""} ${playing ? "playing" : ""}`}>
      <div className="anat-head">
        <span className="anat-name"><LabelMark icon={run?.icon} color={run?.color} size={14} />{name}</span>
        <span className="muted anat-shape">{describeShape(calls)}</span>
      </div>

      {calls.length === 0 ? (
        <div className="empty">No calls recorded for this run.</div>
      ) : (
        <>
          {/* The stage: pile -> model -> move, and the result's way back. */}
          <div className="anat-stage" key={`${lane.id}-${pos}`}>
            <div className={`anat-node anat-pile ${fresh ? "fresh" : ""}`}
                 title={fresh ? "A fresh conversation: the model starts with none of the earlier calls" : "The conversation so far, carried into this call"}>
              <div className="pile-well">
                <div className="pile-fill" style={{ height: `${Math.max(4, (100 * ctx) / peak)}%` }} />
              </div>
              <div className="node-label">
                <b>{fmtNum(ctx)}</b> tokens read
                {fresh && <small className="fresh-tag">fresh context</small>}
              </div>
            </div>
            <div className="anat-wire in"><span className="pulse" /></div>
            <div className="anat-node anat-model" title={cur?.model_id ?? ""}>
              <b>{model}</b>
              <small>{fmtNum(cur?.tokens_out ?? 0)} tokens out</small>
            </div>
            <div className="anat-wire out"><span className="pulse" /></div>
            <div className={`anat-node anat-move ${move?.kind}`} title={cur?.tools.join(", ") || move?.label}>
              <b>{move?.label}</b>
              <small>{move?.kind === "tool" ? "tool call" : move?.kind === "answer" ? "final text" : "no model work"}</small>
            </div>
            <div className={`anat-return ${carried ? "carried" : "dropped"}`}>
              <span className="pulse" />
              <span className="return-label">
                {carried
                  ? "result joins the conversation"
                  : next ? "conversation ends: context dropped, next call starts fresh" : "last call: run ends"}
              </span>
            </div>
          </div>

          <div className="anat-counters">
            <span><b>{Math.min(pos + 1, calls.length)}</b> / {plural(calls.length, "call")}</span>
            <span><b>{fmtNum(totals.read)}</b> tokens read so far</span>
            <span><b>{fmtNum(totals.out)}</b> tokens written</span>
            <span><b>{fmtUsd(totals.cost)}</b> so far</span>
            {cur?.loop_flags && <span className="badge flagged">flagged</span>}
          </div>

          {/* The map: every call as a chip, grouped by iteration; click to jump. */}
          <div className="anat-strip" role="list">
            {calls.map((c, i) => {
              const m = moveOf(c);
              const f = isFresh(calls, i);
              const newIter = i === 0 || c.iteration !== calls[i - 1].iteration;
              return (
                <div key={c.action_id} className={`anat-chip-wrap ${f && i > 0 ? "fresh" : ""}`} role="listitem">
                  {newIter && <span className="iter-tag">{c.iteration == null ? "-" : `it ${c.iteration}`}</span>}
                  <button type="button"
                          className={`anat-chip ${m.kind} ${i === pos ? "current" : ""} ${i < pos ? "done" : ""} ${c.loop_flags ? "flagged" : ""}`}
                          style={{ height: `${Math.max(6, (100 * contextOf(c)) / peak)}%` }}
                          title={`call ${i + 1}: ${fmtNum(contextOf(c))} tokens read, ${m.label}${f ? " (fresh context)" : ""}`}
                          aria-label={`call ${i + 1}, ${fmtNum(contextOf(c))} tokens read, ${m.label}`}
                          onClick={() => onJump(i)}
                          onDoubleClick={() => c.session_id && onOpenSession?.(c.session_id)} />
                </div>
              );
            })}
          </div>
          <div className="chart-caption">
            Chip height is context read, on one scale across lanes. Click a call to jump to it; double-click to open its conversation.
          </div>
        </>
      )}
    </div>
  );
}
