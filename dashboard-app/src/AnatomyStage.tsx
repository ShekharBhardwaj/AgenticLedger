import { useEffect, useMemo, useRef, type CSSProperties } from "react";
import { Ban, MessageSquare, Minus, TriangleAlert, Wrench } from "lucide-react";
import { Call, fmtNum } from "./api";

/** The content stage of the loop anatomy: one recorded call, played as
 *  the model experienced it.
 *
 *  Left, the pad: everything the model could see on this call, card by
 *  card, straight from the stored messages. Right, the world: the tools
 *  the model was offered and the files its calls have touched. Between
 *  them a gate: the model's call crosses it, the result crosses back and
 *  is appended to the pad, or the conversation ends and the pad is
 *  dropped. A small ring says which move is happening: read, think, act,
 *  observe. Every card is recorded text, cut short on screen but never
 *  rewritten; double-click opens the conversation to read it whole. */

export type CardKind = "system" | "user" | "text" | "tool_call" | "tool_result";

export interface Card {
  id: string;
  kind: CardKind;
  title: string;
  body: string;
  toolId?: string;
  isError?: boolean;
}

const PREVIEW = 160;

export function preview(s: string, n = PREVIEW): string {
  // Collapse whitespace and the long ruler lines test runners print
  // (==== and ----), so the words get the room, not the decoration.
  const flat = s.replace(/([=\-_*#~])\1{3,}/g, "$1$1$1").replace(/\s+/g, " ").trim();
  return flat.length > n ? flat.slice(0, n - 3).trimEnd() + "..." : flat;
}

function textOf(content: unknown): string {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map((b) => {
      if (typeof b === "string") return b;
      if (b && typeof b === "object") {
        const o = b as Record<string, unknown>;
        if (typeof o.text === "string") return o.text;
        if (o.type === "tool_result") return textOf(o.content);
      }
      return "";
    }).filter(Boolean).join("\n");
  }
  if (typeof content === "object") {
    const o = content as Record<string, unknown>;
    if (typeof o.text === "string") return o.text;
    try { return JSON.stringify(content); } catch { return String(content); }
  }
  return String(content);
}

/** Agents pass absolute paths; the audience needs the file's name. */
function baseName(path: string): string {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function parseArgs(args: unknown): Record<string, unknown> | null {
  if (args == null) return null;
  if (typeof args === "string") {
    try { const v = JSON.parse(args); return v && typeof v === "object" ? v as Record<string, unknown> : null; }
    catch { return null; }
  }
  return typeof args === "object" ? args as Record<string, unknown> : null;
}

/** "read_file(textkit.py)": the tool and its most telling argument. */
export function callLabel(name: string, args: unknown): string {
  const o = parseArgs(args);
  if (!o) return `${name}()`;
  for (const key of ["path", "file", "filename", "file_path", "filepath", "query", "command", "url"]) {
    const v = o[key];
    if (typeof v === "string" && v) {
      const shown = key === "path" || key.startsWith("file") ? baseName(v) : v;
      return `${name}(${preview(shown, 40)})`;
    }
  }
  const firstString = Object.values(o).find((v) => typeof v === "string" && v) as string | undefined;
  if (firstString) return `${name}(${preview(firstString, 40)})`;
  const keys = Object.keys(o);
  return keys.length ? `${name}(${keys.join(", ")})` : `${name}()`;
}

/** The pad's cards for one stored call: the system prompt, then every
 *  message, with assistant text, tool calls and tool results as their own
 *  cards. Handles the Anthropic block shape and the OpenAI role shape. */
export function cardsFromCall(call: Call): Card[] {
  const cards: Card[] = [];
  if (call.system_prompt) cards.push({ id: "system", kind: "system", title: "system", body: call.system_prompt });
  const messages = Array.isArray(call.messages) ? call.messages as unknown[] : [];
  messages.forEach((m, mi) => {
    if (!m || typeof m !== "object") return;
    const msg = m as Record<string, unknown>;
    const role = String(msg.role ?? "user");
    if (role === "system") {
      if (!call.system_prompt) cards.push({ id: `m${mi}`, kind: "system", title: "system", body: textOf(msg.content) });
      return;
    }
    if (role === "tool") {
      cards.push({ id: `m${mi}`, kind: "tool_result", title: "tool result", body: textOf(msg.content),
                   toolId: typeof msg.tool_call_id === "string" ? msg.tool_call_id : undefined });
      return;
    }
    const content = msg.content;
    if (Array.isArray(content)) {
      content.forEach((b, bi) => {
        if (!b || typeof b !== "object") {
          if (typeof b === "string" && b.trim()) {
            cards.push({ id: `m${mi}b${bi}`, kind: role === "assistant" ? "text" : "user", title: role === "assistant" ? "model says" : "user", body: b });
          }
          return;
        }
        const o = b as Record<string, unknown>;
        if (o.type === "tool_use") {
          cards.push({ id: `m${mi}b${bi}`, kind: "tool_call", title: "tool call",
                       body: callLabel(String(o.name ?? "tool"), o.input), toolId: typeof o.id === "string" ? o.id : undefined });
        } else if (o.type === "tool_result") {
          cards.push({ id: `m${mi}b${bi}`, kind: "tool_result", title: o.is_error ? "tool error" : "tool result",
                       body: textOf(o.content), toolId: typeof o.tool_use_id === "string" ? o.tool_use_id : undefined,
                       isError: !!o.is_error });
        } else if (typeof o.text === "string" && o.text.trim()) {
          cards.push({ id: `m${mi}b${bi}`, kind: role === "assistant" ? "text" : "user",
                       title: role === "assistant" ? "model says" : "user", body: o.text });
        }
      });
    } else if (typeof content === "string" && content.trim()) {
      cards.push({ id: `m${mi}`, kind: role === "assistant" ? "text" : "user", title: role === "assistant" ? "model says" : "user", body: content });
    }
    // OpenAI shape: tool calls ride beside the assistant content.
    const tcs = msg.tool_calls;
    if (Array.isArray(tcs)) {
      tcs.forEach((t, ti) => {
        if (!t || typeof t !== "object") return;
        const o = t as Record<string, unknown>;
        const fn = (o.function ?? {}) as Record<string, unknown>;
        cards.push({ id: `m${mi}t${ti}`, kind: "tool_call", title: "tool call",
                     body: callLabel(String(fn.name ?? o.name ?? "tool"), fn.arguments ?? o.arguments),
                     toolId: typeof o.id === "string" ? o.id : undefined });
      });
    }
  });
  return cards;
}

/** What the model wrote on this call: its text, then its tool calls. */
export function outputCards(call: Call): Card[] {
  const cards: Card[] = [];
  if (call.content && call.content.trim()) cards.push({ id: "out-text", kind: "text", title: "model says", body: call.content });
  (call.tool_calls ?? []).forEach((t, i) => {
    cards.push({ id: `out-tc${i}`, kind: "tool_call", title: "tool call",
                 body: callLabel(String(t.name ?? "tool"), t.arguments), toolId: t.id });
  });
  return cards;
}

/** The results that came back for this call's tool calls, read from the
 *  next call in the same conversation (the record of what the model saw
 *  next). Empty when the conversation ended here. */
export function observationCards(call: Call, next: Call | null): Card[] {
  if (!next) return [];
  const ids = new Set((call.tool_calls ?? []).map((t) => t.id).filter(Boolean) as string[]);
  const out: Card[] = [];
  const results = Array.isArray(next.tool_results) ? next.tool_results as unknown[] : [];
  results.forEach((r, i) => {
    if (!r || typeof r !== "object") return;
    const o = r as Record<string, unknown>;
    const id = (o.tool_use_id ?? o.tool_call_id) as string | undefined;
    if (ids.size && (!id || !ids.has(id))) return;
    out.push({ id: `obs${i}`, kind: "tool_result", title: o.is_error ? "tool error" : "tool result",
               body: textOf(o.content), toolId: id, isError: !!o.is_error });
  });
  if (out.length) return out;
  // Older records carry no tool_results column: read the next call's
  // trailing tool_result cards instead.
  const cards = cardsFromCall(next).filter((c) => c.kind === "tool_result");
  return cards.filter((c) => !ids.size || (c.toolId && ids.has(c.toolId))).slice(-Math.max(1, ids.size));
}

const KIND_ICON = { tool: Wrench, answer: MessageSquare, silent: Minus, refused: Ban, error: TriangleAlert } as const;

export interface StageProps {
  laneId: string;
  beatKey: string;
  call: Call | null;             // the full record of the current call (null while loading)
  nextCall: Call | null;         // the next call in the same conversation, if any
  carried: boolean;              // the result goes back into this conversation
  endsHere: boolean;             // the conversation ends after this call (another follows fresh)
  last: boolean;                 // the run's last recorded call
  fresh: boolean;                // this call opened a new conversation
  kind: "tool" | "answer" | "silent" | "refused" | "error";
  contextTokens: number;
  peak: number;
  toolsOffered: string[];
  toolsUsed: string[];           // tools called anywhere in the run so far
  filesSoFar: string[];          // files touched by the run up to and including this call
  filesNow: string[];            // files this call touched
  onOpenSession?: () => void;
}

export function Stage(p: StageProps) {
  const padRef = useRef<HTMLDivElement>(null);
  const ctxCards = useMemo(() => (p.call ? cardsFromCall(p.call) : []), [p.call]);
  const outCards = useMemo(() => (p.call ? outputCards(p.call) : []), [p.call]);
  const obsCards = useMemo(() => (p.call && p.carried ? observationCards(p.call, p.nextCall) : []), [p.call, p.nextCall, p.carried]);
  const calledTools = useMemo(() => new Set((p.call?.tool_calls ?? []).map((t) => String(t.name ?? ""))), [p.call]);

  // Keep the newest cards in view: the pad is the model's growing memory,
  // and the audience should see the bottom of it.
  useEffect(() => {
    const el = padRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [p.beatKey, ctxCards.length]);

  const hasText = outCards.some((c) => c.kind === "text");
  const hasTool = outCards.some((c) => c.kind === "tool_call");
  const observes = obsCards.length > 0;
  const Icon = KIND_ICON[p.kind];
  const stations: { key: string; label: string; on: boolean }[] = [
    { key: "read", label: "read", on: true },
    { key: "think", label: "think", on: hasText },
    { key: "act", label: "act", on: hasTool },
    { key: "observe", label: "observe", on: observes },
  ];
  // Agents like Claude Code offer dozens of tools; the ones this run has
  // used come first, a few more follow, the rest fold into a count.
  const shownTools = useMemo(() => {
    const used = p.toolsOffered.filter((t) => p.toolsUsed.includes(t) || calledTools.has(t));
    const rest = p.toolsOffered.filter((t) => !used.includes(t));
    return [...used, ...rest.slice(0, Math.max(0, 8 - used.length))];
  }, [p.toolsOffered, p.toolsUsed, calledTools]);
  const hiddenTools = p.toolsOffered.length - shownTools.length;
  const vars = { ["--ctx" as string]: String(Math.min(1, p.contextTokens / Math.max(1, p.peak))) } as CSSProperties;
  const tail = ctxCards.length;   // cards older than the last few render compressed
  const compressBefore = Math.max(0, tail - 4);

  return (
    <div className={`anat-stage ${p.fresh ? "fresh" : ""} ${p.endsHere ? "ending" : ""} ${p.last ? "last" : ""}`}
         key={p.beatKey} style={vars}>
      {/* The pad */}
      <div className="stage-pad" onDoubleClick={p.onOpenSession} title="Double-click to open this conversation">
        <div className="stage-head">
          <span title="Everything the model could see on this call, from the stored messages">Context</span>
          <b>{fmtNum(p.contextTokens)} tokens</b>
        </div>
        <div className="pad-gauge" aria-hidden="true"><i /></div>
        <div className="pad-scroll" ref={padRef}>
          {!p.call && <div className="pad-loading">loading the recorded conversation</div>}
          {ctxCards.map((c, i) => (
            <div key={c.id} className={`card ctx ${c.kind} ${i < compressBefore ? "old" : ""} ${c.isError ? "error" : ""}`}>
              <span className="card-title">{c.title}</span>
              <span className="card-body">{preview(c.body, i < compressBefore ? 70 : PREVIEW)}</span>
            </div>
          ))}
          {outCards.map((c) => (
            <div key={c.id} className={`card out ${c.kind}`}>
              <span className="card-title">{c.title}</span>
              <span className="card-body">{preview(c.body)}</span>
            </div>
          ))}
          {obsCards.map((c) => (
            <div key={c.id} className={`card obs ${c.kind} ${c.isError ? "error" : ""}`}>
              <span className="card-title">{c.title}</span>
              <span className="card-body">{preview(c.body)}</span>
            </div>
          ))}
          {p.call && p.endsHere && (
            <div className="pad-end">conversation ends: this context is dropped; the next call starts with an empty pad</div>
          )}
          {p.call && p.last && (
            <div className="pad-end final">last recorded call</div>
          )}
        </div>
      </div>

      {/* The gate */}
      <div className="stage-gate" aria-hidden="true">
        <div className="ring">
          {stations.map((s) => (
            <span key={s.key} className={`st ${s.key} ${s.on ? "on" : "na"}`}>{s.label}</span>
          ))}
        </div>
        <div className="gate-line">
          {hasTool && <span className="token out" />}
          {observes && <span className="token back" />}
        </div>
        <div className="gate-word">{hasTool ? "runtime" : p.kind === "answer" ? "no tool call" : p.kind === "refused" ? "refused" : p.kind === "error" ? "error" : "no call"}</div>
      </div>

      {/* The world */}
      <div className="stage-world">
        <div className="stage-head"><span title="The tools the model was offered, and the files its calls have touched">World: tools and files</span><Icon size={12} aria-hidden="true" /></div>
        <div className="tiles">
          {p.toolsOffered.length === 0 && <span className="muted">no tools offered on this call</span>}
          {shownTools.map((t) => (
            <span key={t} className={`tile ${calledTools.has(t) ? "lit" : ""}`}>{t}</span>
          ))}
          {hiddenTools > 0 && (
            <span className="tile more" title={p.toolsOffered.join(", ")}>+{hiddenTools} more offered</span>
          )}
        </div>
        {p.filesSoFar.length > 0 && (
          <div className="files">
            <span className="files-label">files touched</span>
            {p.filesSoFar.map((f) => (
              <span key={f} className={`file ${p.filesNow.includes(f) ? "lit" : ""}`} title={f}>{baseName(f)}</span>
            ))}
          </div>
        )}
        <div className="world-result">
          {obsCards.length > 0 ? obsCards.map((c) => (
            <div key={c.id} className={`card result ${c.isError ? "error" : ""}`}>
              <span className="card-title">{c.title}</span>
              <span className="card-body">{preview(c.body, 220)}</span>
            </div>
          )) : p.call && hasTool && !p.carried ? (
            <div className="muted small">the call was made, but the conversation ended before a result was read</div>
          ) : p.call && p.kind === "answer" ? (
            <div className="muted small">the model answered in text; nothing crossed the gate</div>
          ) : p.call && (p.kind === "refused" || p.kind === "error") ? (
            <div className="muted small">no model work on this call</div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
