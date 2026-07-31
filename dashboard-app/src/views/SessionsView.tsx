import { useCallback, useEffect, useState } from "react";
import {
  Call, del, flagBadgeClass, flagInfo, fmtAgo, fmtNum, fmtTime, fmtUsd, get,
  interactionTags, liveUpdates, post, ReplayResult, Session, toolNames,
} from "../api";

function cacheStats(side: { cache_read_tokens: number | null; cache_write_tokens: number | null }): string {
  const parts: string[] = [];
  if (side.cache_read_tokens) parts.push(`⚡ ${fmtNum(side.cache_read_tokens)} cached`);
  if (side.cache_write_tokens) parts.push(`✍ ${fmtNum(side.cache_write_tokens)} written`);
  return parts.length ? " · " + parts.join(" · ") : "";
}

function ReplayPanel({ call }: { call: Call }) {
  const [model, setModel] = useState(call.model_id);
  const [provider, setProvider] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReplayResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = () => {
    setBusy(true);
    setError(null);
    post<ReplayResult>("/api/replay", {
      action_id: call.action_id,
      model: model.trim(),
      ...(provider !== "auto" ? { provider } : {}),
    })
      .then(setResult)
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="replay-panel">
      <div className="replay-controls">
        <input
          className="replay-model"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          title="Model to replay on — any provider; the wire format is translated automatically"
        />
        <select
          className="replay-provider"
          value={provider}
          onChange={(e) => setProvider(e.target.value)}
          title="Where to send it — auto recognizes gpt-*/claude-* names; pick openai for a local model behind an OpenAI-style server (LM Studio)"
        >
          <option value="auto">auto</option>
          <option value="openai">openai</option>
          <option value="anthropic">anthropic</option>
        </select>
        <button className="link-btn" disabled={busy} onClick={run}>
          {busy ? "Replaying…" : "Run replay"}
        </button>
        <span className="muted">
          re-sends this exact call — type any model: gpt-*, claude-*, or a local
          one (pick openai + an LM Studio target). Cloud replays cost real tokens.
        </span>
      </div>
      {error && <div className="replay-error">{error}</div>}
      {result && (
        <div className="replay-grid">
          <div>
            <div className="muted mono">{result.original.model_id} (original)</div>
            <div className="replay-stats">
              {fmtNum(result.original.tokens_in)} → {fmtNum(result.original.tokens_out)} tok{cacheStats(result.original)}
              · {fmtUsd(result.original.cost_usd)}
              · {result.original.latency_ms != null ? `${Math.round(result.original.latency_ms)}ms` : "—"}
            </div>
            <pre>{result.original.content ?? "(no text content)"}</pre>
          </div>
          <div>
            <div className="muted mono">{result.replay.model_id} (replay)</div>
            <div className="replay-stats">
              {fmtNum(result.replay.tokens_in)} → {fmtNum(result.replay.tokens_out)} tok{cacheStats(result.replay)}
              · {fmtUsd(result.replay.cost_usd)}
              · {result.replay.latency_ms != null ? `${Math.round(result.replay.latency_ms)}ms` : "—"}
            </div>
            <pre>{result.replay.content ?? "(no text content)"}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
import FlowView from "./FlowView";
import TraceView from "./TraceView";
import WhatIf from "./WhatIf";

type Mode = "calls" | "flow" | "trace";

function CallCard({ call }: { call: Call }) {
  const [open, setOpen] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const failed = (call.status_code ?? 200) !== 200;
  const tools = toolNames(call);
  return (
    <div className="card call-card">
      <div className="call-head" onClick={() => setOpen(!open)}>
        <span className="model">{call.model_id}</span>
        {interactionTags(call).map(({ tag, label }) => (
          <span key={tag} className={`badge proto ${tag.toLowerCase()}`} title={label}>
            {tag}
          </span>
        ))}
        {failed && <span className="badge error">{call.status_code}</span>}
        {call.error_detail?.startsWith("partial:") && (
          <span className="badge fw" title={call.error_detail}>partial</span>
        )}
        {call.loop_flags &&
          (JSON.parse(call.loop_flags) as string[]).map((n) => (
            <span
              key={n}
              className={`badge ${flagBadgeClass(n)}`}
              title={`${flagInfo(n).title}: ${flagInfo(n).detail}`}
            >
              {n}
            </span>
          ))}
        {call.framework && <span className="badge fw">{call.framework}</span>}
        {tools.length > 0 && (
          <span className="tools-chip" title={tools.join(", ")}>
            ⚙ {tools.slice(0, 3).join(" · ")}
            {tools.length > 3 ? ` +${tools.length - 3}` : ""}
          </span>
        )}
        {call.step_index != null && <span className="dim">step {call.step_index}</span>}
        {call.iteration != null && <span className="dim">iter {call.iteration}</span>}
        <span className="dim">{fmtNum(call.tokens_in)} → {fmtNum(call.tokens_out)} tok</span>
        {call.cache_read_tokens != null && call.cache_read_tokens > 0 && (
          <span className="dim" title="prompt-cache reads — billed at a fraction of the input rate">
            ⚡ {fmtNum(call.cache_read_tokens)} cached
          </span>
        )}
        {call.cache_write_tokens != null && call.cache_write_tokens > 0 && (
          <span className="dim" title="prompt-cache writes — billed at a premium over the input rate; this is usually where a surprising cost comes from">
            ✍ {fmtNum(call.cache_write_tokens)} written
          </span>
        )}
        <span className="dim">{fmtUsd(call.cost_usd)}</span>
        <span className="dim">{call.latency_ms != null ? `${call.latency_ms}ms` : ""}</span>
        <span className="spacer" />
        <span className="dim">{fmtTime(call.timestamp)}</span>
      </div>
      {open && (
        <div className="call-body">
          {(call.framework !== "replay") && (
            <button
              className="link-btn"
              onClick={(e) => { e.stopPropagation(); setReplaying(!replaying); }}
            >
              {replaying ? "Hide replay" : "↻ Replay this call"}
            </button>
          )}
          {replaying && <ReplayPanel call={call} />}
          {call.error_detail && (<><h4>Error</h4><pre>{call.error_detail}</pre></>)}
          {call.system_prompt && (<><h4>System prompt</h4><pre>{call.system_prompt}</pre></>)}
          {call.thinking && (<><h4>Thinking</h4><pre>{call.thinking}</pre></>)}
          {call.content && (<><h4>Response</h4><pre>{call.content}</pre></>)}
          {call.tool_calls && (
            <><h4>Tool calls</h4><pre>{JSON.stringify(call.tool_calls, null, 2)}</pre></>
          )}
          {call.tool_results != null && (
            <><h4>Tool results (fed into this call)</h4><pre>{JSON.stringify(call.tool_results, null, 2)}</pre></>
          )}
          <h4>Messages</h4>
          <pre>{JSON.stringify(call.messages, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}

export default function SessionsView({ focusSession }: { focusSession?: string | null }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<string | null>(focusSession ?? null);

  useEffect(() => {
    if (focusSession) setSelected(focusSession);
  }, [focusSession]);
  const [calls, setCalls] = useState<Call[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Call[] | null>(null);
  const [mode, setMode] = useState<Mode>("calls");

  const refresh = useCallback(() => {
    get<Session[]>("/api/sessions").then(setSessions).catch(() => {});
    // keep an open session view fresh too
    setSelected((cur) => {
      if (cur) get<Call[]>(`/session/${encodeURIComponent(cur)}`).then(setCalls).catch(() => {});
      return cur;
    });
  }, []);

  useEffect(() => {
    refresh();
    return liveUpdates(refresh);
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    get<Call[]>(`/session/${encodeURIComponent(selected)}`).then(setCalls).catch(() => setCalls([]));
  }, [selected]);

  useEffect(() => {
    if (!query.trim()) { setResults(null); return; }
    const t = window.setTimeout(() => {
      get<Call[]>(`/api/search?q=${encodeURIComponent(query.trim())}`)
        .then(setResults).catch(() => setResults([]));
    }, 300);
    return () => window.clearTimeout(t);
  }, [query]);

  const shown = results ?? calls;

  return (
    <div className="layout">
      <div className="sidebar">
        <input
          className="search"
          placeholder="Search prompts, outputs, agents…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {sessions.map((s) => (
          <div
            key={s.session_id}
            className={`card ${selected === s.session_id ? "selected" : ""}`}
            onClick={() => { setQuery(""); setSelected(s.session_id); }}
          >
            <button
              className="card-del"
              title="Delete this session's captured calls"
              onClick={(e) => {
                e.stopPropagation();
                if (!window.confirm(
                  `Delete session ${s.session_id} (${s.call_count} calls)? This cannot be undone.`,
                )) return;
                del(`/api/sessions/${encodeURIComponent(s.session_id)}`)
                  .then(() => {
                    setSelected((cur) => (cur === s.session_id ? null : cur));
                    refresh();
                  })
                  .catch((err) => window.alert(`Delete failed: ${err.message}`));
              }}
            >
              ×
            </button>
            <div className="card-title">{s.session_id}</div>
            <div className="card-sub">
              <span>{fmtAgo(s.started_at)}</span>
              <span>{s.call_count} calls</span>
              <span>{fmtUsd(s.total_cost_usd)}</span>
              {s.agent_name && <span className="badge fw">{s.agent_name}</span>}
            </div>
          </div>
        ))}
      </div>
      <div className="main">
        {results === null && selected && (
          <WhatIf params={`session_id=${encodeURIComponent(selected)}`} />
        )}
        {results !== null && (
          <div className="section-title">{results.length} search results</div>
        )}
        {results === null && selected && shown.length > 0 && (
          <div className="seg">
            {(["calls", "flow", "trace"] as Mode[]).map((m) => (
              <button key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>
                {m === "calls" ? "Calls" : m === "flow" ? "Flow" : "Trace"}
              </button>
            ))}
          </div>
        )}
        {shown.length === 0 ? (
          <div className="empty">
            {results !== null ? "No matches." : "Select a session to inspect its calls."}
          </div>
        ) : results !== null || mode === "calls" ? (
          shown.map((c) => <CallCard key={c.action_id} call={c} />)
        ) : mode === "flow" ? (
          <FlowView calls={calls} />
        ) : (
          <TraceView calls={calls} />
        )}
      </div>
    </div>
  );
}
