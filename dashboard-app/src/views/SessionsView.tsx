import { useRef, useCallback, useEffect, useState } from "react";
import {
  Call, del, flagBadgeClass, flagInfo, fmtAgo, fmtNum, fmtTime, fmtUsd, get,
  getCall, interactionTags, listProjects, liveUpdates, post, ReplayResult,
  replayModels, replayTargets, ReplayTarget, Session, toolNames,
} from "../api";
import { LabelEditor, matchesFilter, PinButton, pinnedFirst, ProjectFilter, RUN_PREFIX, TimeSortToggle, timeSorted } from "./LabelBits";
import { JobSummary, listReplayJobs } from "../api";
import ProviderMark from "./ProviderMark";

function cacheStats(side: { cache_read_tokens: number | null; cache_write_tokens: number | null }): string {
  const parts: string[] = [];
  if (side.cache_read_tokens) parts.push(`⚡ ${fmtNum(side.cache_read_tokens)} cached`);
  if (side.cache_write_tokens) parts.push(`✍ ${fmtNum(side.cache_write_tokens)} written`);
  return parts.length ? " · " + parts.join(" · ") : "";
}

const DEST_KEY = "agenticledger.replay.dest";

function ReplayPanel({ call }: { call: Call }) {
  const [model, setModel] = useState(call.model_id);
  // Destination first: where the replay runs, remembered across panels —
  // a user's replay target rarely changes.
  const [provider, setProvider] = useState(localStorage.getItem(DEST_KEY) ?? "auto");
  const [targets, setTargets] = useState<ReplayTarget[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ReplayResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    replayTargets().then((r) => setTargets(r.targets)).catch(() => {});
  }, []);

  // Ask a local destination what models it actually has loaded, and offer
  // them — nobody should have to type "qwen/qwen3.6-35b-a3b" from memory.
  useEffect(() => {
    const t = targets.find((x) => x.provider === provider);
    if (!t) { setModels([]); return; }
    replayModels(provider)
      .then((r) => {
        setModels(r.models);
        if (t.local && r.models.length > 0) {
          // On a local destination the original cloud model name is never
          // right — offer the first loaded model instead.
          setModel((cur) => (cur === call.model_id ? r.models[0] : cur));
        }
      })
      .catch(() => setModels([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, targets]);

  const pickDest = (v: string) => {
    setProvider(v);
    localStorage.setItem(DEST_KEY, v);
  };

  const destLabel = (t: ReplayTarget) =>
    t.local ? `local · ${t.host}` : `${t.provider} · ${t.host}`;

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
        <select
          className="replay-provider"
          value={provider}
          onChange={(e) => pickDest(e.target.value)}
          title="Where the replay runs. auto recognizes gpt-*/claude-* names and otherwise uses your only configured target."
        >
          <option value="auto">auto</option>
          {targets.map((t) => (
            <option key={t.provider} value={t.provider}>{destLabel(t)}</option>
          ))}
          {!targets.some((t) => t.provider === "openai") && <option value="openai">openai</option>}
          {!targets.some((t) => t.provider === "anthropic") && <option value="anthropic">anthropic</option>}
        </select>
        <input
          className="replay-model"
          value={model}
          list={models.length > 0 ? `replay-models-${call.action_id}` : undefined}
          onChange={(e) => setModel(e.target.value)}
          title="Model to replay on. The wire format is translated automatically"
        />
        {models.length > 0 && (
          <datalist id={`replay-models-${call.action_id}`}>
            {models.map((m) => <option key={m} value={m} />)}
          </datalist>
        )}
        <button className="link-btn" disabled={busy} onClick={run}>
          {busy ? "Replaying…" : "Run replay"}
        </button>
        <span className="muted">
          re-sends this exact call where you point it; a local destination is
          free; cloud replays cost real tokens.
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
import BatchReplay from "./BatchReplay";
import WhatIf from "./WhatIf";

type Mode = "calls" | "flow" | "trace";

/** Zero-state that diagnoses instead of shrugging: wrong wiring produces
 *  silence, so the silence itself must say what to check. The dashboard's
 *  own origin IS the proxy address — show the exact URLs to use. */
function WiringGuide() {
  const origin = window.location.origin;
  const port = window.location.port || "8000";
  return (
    <div className="wiring-guide">
      <div className="section-title">Nothing captured yet</div>
      <div className="muted" style={{ maxWidth: 700 }}>
        The proxy is up (this page is served by it) but no agent traffic
        has arrived. The three usual reasons, in order:
      </div>
      <ol className="wiring-list">
        <li>
          <b>The agent's base URL isn't pointed here.</b> Point it at{" "}
          <span className="mono">{origin}</span>
          {" "}(OpenAI-style clients add <span className="mono">/v1</span>:{" "}
          <span className="mono">{origin}/v1</span>). Claude Code:{" "}
          <span className="mono">ANTHROPIC_BASE_URL={origin}</span>.
        </li>
        <li>
          <b>The agent runs in Docker.</b> Inside a container,{" "}
          <span className="mono">localhost</span> means the container itself;
          use <span className="mono">http://host.docker.internal:{port}</span>
          {" "}instead.
        </li>
        <li>
          <b>The upstream doesn't match the agent.</b> An Anthropic agent needs{" "}
          <span className="mono">upstream_url = "https://api.anthropic.com"</span>;
          an OpenAI-style one needs the OpenAI URL or your gateway. The ⚙
          settings page shows what this proxy is running with.
        </li>
      </ol>
      <div className="muted" style={{ maxWidth: 700 }}>
        Mis-wired calls that DO reach the proxy are captured with the reason
        named, so a fully silent dashboard means traffic never arrived here
        at all. Per-framework recipes: docs/integrations in the repo.
      </div>
    </div>
  );
}

/** #62 — the call list says what it is: name, the id (always visible and
 *  copyable, even after a rename), chips, and totals that update live. */
function SessionHeader({ session, sessionId, calls, onOpenSession }: {
  session: Session | null; sessionId: string; calls: Call[];
  onOpenSession?: (sid: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [sourceJob, setSourceJob] = useState<JobSummary | null>(null);
  useEffect(() => {
    setSourceJob(null);
    if (!sessionId.startsWith("replay-sess-") && !sessionId.startsWith("replay-run-")) return;
    listReplayJobs({ replaySessionId: sessionId })
      .then((r) => setSourceJob(r.jobs[r.jobs.length - 1] ?? null))
      .catch(() => {});
  }, [sessionId]);
  const cost = calls.reduce((a, c) => a + (c.cost_usd ?? 0), 0);
  const tokIn = calls.reduce((a, c) => a + (c.tokens_in ?? 0)
    + (c.cache_read_tokens ?? 0) + (c.cache_write_tokens ?? 0), 0);
  const tokOut = calls.reduce((a, c) => a + (c.tokens_out ?? 0), 0);
  return (
    <div className="session-header">
      <div className="session-header-title">
        {session?.label ?? sessionId}
        {session?.team && <span className="badge team">{session.team}</span>}
        {session?.project && <span className="badge fw">{session.project}</span>}
      </div>
      {sourceJob && (
        <div className="replay-signpost">
          This is {sourceJob.model}'s answer sheet: nothing here was executed.
          The side-by-side comparison lives on the original {sourceJob.scope}
          {sourceJob.scope === "session" && onOpenSession && (
            <>
              {": "}
              <button className="link-btn" style={{ marginTop: 0 }}
                      onClick={() => onOpenSession(sourceJob.ref_id)}>
                open the comparison
              </button>
            </>
          )}
          {sourceJob.scope === "run" && <> ({sourceJob.ref_id} in Loop Lens).</>}
        </div>
      )}
      <div className="session-header-sub">
        <button
          className="session-id mono"
          title="click to copy the session id"
          onClick={() => {
            navigator.clipboard?.writeText(sessionId).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1200);
            }).catch(() => {});
          }}
        >
          {sessionId} {copied ? "✓ copied" : "⧉"}
        </button>
        <span>{calls.length} calls</span>
        <span>{fmtNum(tokIn)} → {fmtNum(tokOut)} tok</span>
        <span>{fmtUsd(cost)}</span>
      </div>
    </div>
  );
}

function CallCard({ call, num, onOpenSession }: {
  call: Call; num?: number; onOpenSession?: (sid: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [replaying, setReplaying] = useState(false);
  // Inspector opens on the response (or the relevant error, shown first).
  const [itab, setItab] = useState<"response" | "tools" | "prompt" | "raw">("response");
  const blocked = call.error_detail?.startsWith("blocked:") ?? false;
  const transient = call.error_detail?.startsWith("transient:") ?? false;
  const probe = call.error_detail?.startsWith("probe:") ?? false;
  const failed = !blocked && !transient && !probe && (call.status_code ?? 200) !== 200;
  const tools = toolNames(call);
  const openOriginal = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!call.parent_action_id || !onOpenSession) return;
    getCall(call.parent_action_id)
      .then((orig) => { if (orig.session_id) onOpenSession(orig.session_id); })
      .catch(() => {});
  };
  // Spec 8: one status per row, chosen by precedence; everything else
  // lives in the inspector. Blocked/transient/probe/partial are refusals
  // and hiccups, not failures, and never aggregate into one red count.
  const flagNames: string[] = call.loop_flags ? JSON.parse(call.loop_flags) : [];
  const status = blocked ? { cls: "blocked", text: "Blocked" }
    : transient ? { cls: "blocked", text: `Transient ${call.status_code}` }
    : probe ? { cls: "fw", text: "Probe" }
    : call.error_detail?.startsWith("partial:") ? { cls: "fw", text: "Partial" }
    : failed ? { cls: "error", text: `Failed ${call.status_code}` }
    : flagNames.length > 0 ? { cls: "flagged", text: "Flagged" }
    : { cls: "ok", text: "OK" };
  const shortModel = (id: string | null) => {
    if (!id) return "unknown";
    const tail = id.replace(/^(us|eu|apac)\./, "").replace(/^(anthropic|amazon|meta)\./, "");
    return tail.length > 30 ? tail.slice(0, 29) + "…" : tail;
  };
  const statusTitle = call.error_detail
    || (flagNames.length ? flagNames.map((n) => `${flagInfo(n).title}`).join("; ") : undefined);
  return (
    <div className="card call-card">
      <div className="call-row" onClick={() => setOpen(!open)}
           role="button" aria-expanded={open} tabIndex={0}
           onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); } }}>
        {num != null && (
          <span className="call-num" title="call number within this session, in time order; the report card uses the same numbers">
            #{num}
          </span>
        )}
        <span className="call-time mono">{fmtTime(call.timestamp)}</span>
        <span className="call-model" title={call.model_id ?? undefined}>
          <ProviderMark provider={call.provider} model={call.model_id} />
          <span className="model">{shortModel(call.model_id)}</span>
        </span>
        <span className={`call-status badge ${status.cls}`} title={statusTitle}>{status.text}</span>
        <span className="call-latency mono dim">{call.latency_ms != null ? `${call.latency_ms}ms` : ""}</span>
        <span className="call-cost mono" title={call.cost_usd == null
          ? "no price is on record for this call's model; budgets do not see this spend"
          : undefined}>
          {call.cost_usd == null ? "Unknown" : fmtUsd(call.cost_usd)}
        </span>
      </div>
      {open && (
        <div className="call-body">
          <div className="inspector-meta">
            {interactionTags(call).map(({ tag, label }) => (
              <span key={tag} className={`badge proto ${tag.toLowerCase()}`} title={label}>{tag}</span>
            ))}
            {call.framework && <span className="badge fw">{call.framework}</span>}
            {flagNames.map((n) => (
              <span key={n} className={`badge ${flagBadgeClass(n)}`}
                    title={`${flagInfo(n).title}: ${flagInfo(n).detail}`}>{n}</span>
            ))}
            {call.step_index != null && <span className="dim">step {call.step_index}</span>}
            {call.iteration != null && <span className="dim">iter {call.iteration}</span>}
            <span className="dim mono"
                  title={`input: ${fmtNum(call.tokens_in)} new` +
                    (call.cache_read_tokens ? ` + ${fmtNum(call.cache_read_tokens)} cache reads` : "") +
                    (call.cache_write_tokens ? ` + ${fmtNum(call.cache_write_tokens)} cache writes` : "") +
                    ` · output: ${fmtNum(call.tokens_out)}`}>
              {fmtNum((call.tokens_in ?? 0) + (call.cache_read_tokens ?? 0) + (call.cache_write_tokens ?? 0))}
              {" → "}{fmtNum(call.tokens_out)} tok
            </span>
            {(call.cache_read_tokens ?? 0) > 0 && (
              <span className="dim" title="prompt-cache reads, billed at a fraction of the input rate">
                ⚡ {fmtNum(call.cache_read_tokens)} cached</span>
            )}
            {(call.cache_write_tokens ?? 0) > 0 && (
              <span className="dim" title="prompt-cache writes, billed at a premium over the input rate">
                ✍ {fmtNum(call.cache_write_tokens)} written</span>
            )}
            {tools.length > 0 && (
              <span className="tools-chip" title={tools.join(", ")}>
                ⚙ {tools.slice(0, 3).join(" · ")}{tools.length > 3 ? ` +${tools.length - 3}` : ""}</span>
            )}
            <span className="spacer" />
            {(call.framework !== "replay") && (
              <button className="link-btn"
                      onClick={(e) => { e.stopPropagation(); setReplaying(!replaying); }}>
                {replaying ? "Hide replay" : "↻ Replay this call"}
              </button>
            )}
            {call.framework === "replay" && call.parent_action_id && onOpenSession && (
              <button className="link-btn" title="jump to the call this replay re-ran"
                      onClick={openOriginal}>↩ Open original</button>
            )}
          </div>
          {replaying && <ReplayPanel call={call} />}
          <div role="tablist" aria-label="Call detail" className="subtabs inspector-tabs">
            {(["response", "tools", "prompt", "raw"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={itab === t}
                      className={`subtab ${itab === t ? "active" : ""}`}
                      onClick={(e) => { e.stopPropagation(); setItab(t); }}>
                {t === "response" ? "Response" : t === "tools" ? "Tools" : t === "prompt" ? "Prompt" : "Raw"}
              </button>
            ))}
          </div>
          {itab === "response" && (
            <>
              {call.error_detail && (<><h4>{failed ? "Error" : "Refusal / note"}</h4><pre>{call.error_detail}</pre></>)}
              {call.thinking && (
                <details><summary>Captured thinking (what the provider returned; not proof of full reasoning)</summary>
                  <pre>{call.thinking}</pre></details>
              )}
              {call.content?.trim() ? (
                <><h4>Response</h4><pre>{call.content}</pre></>
              ) : call.tool_calls && call.tool_calls.length > 0 ? (
                <div className="muted">No text: the model answered with tool calls. See the Tools tab.</div>
              ) : call.error_detail ? null : (
                <div className="muted">No captured text content for this call.</div>
              )}
            </>
          )}
          {itab === "tools" && (
            <>
              {call.tool_calls
                ? (<><h4>Tool calls</h4><pre>{JSON.stringify(call.tool_calls, null, 2)}</pre></>)
                : <div className="muted">This call requested no tools.</div>}
              {call.tool_results != null && (
                <><h4>Tool results (fed into this call)</h4><pre>{JSON.stringify(call.tool_results, null, 2)}</pre></>
              )}
            </>
          )}
          {itab === "prompt" && (
            <>
              {call.system_prompt
                ? (<details open={call.system_prompt.length < 2000}>
                     <summary>System prompt ({call.system_prompt.length.toLocaleString()} chars)</summary>
                     <pre>{call.system_prompt}</pre></details>)
                : <div className="muted">No system prompt on record (capture level, or none sent).</div>}
              <h4>Messages</h4>
              <pre>{JSON.stringify(call.messages, null, 2)}</pre>
            </>
          )}
          {itab === "raw" && (
            <>
              <button className="link-btn"
                      onClick={(e) => { e.stopPropagation();
                        navigator.clipboard?.writeText(JSON.stringify(call, null, 2)).catch(() => {}); }}>
                ⧉ Copy record
              </button>
              <pre>{JSON.stringify(call, null, 2)}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function SessionsView({ focusSession, onOpenRun, onSelectedChange }: {
  focusSession?: string | null;
  onOpenRun?: (runId: string) => void;
  onSelectedChange?: (id: string | null) => void;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<string | null>(focusSession ?? null);

  const focusSessionRef = useRef<string | null>(focusSession ?? null);
  useEffect(() => { focusSessionRef.current = focusSession ?? null; setSelected(focusSession ?? null); }, [focusSession]);
  useEffect(() => {
    if (focusSessionRef.current === selected) return;
    onSelectedChange?.(selected);
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [selected]);
  const [calls, setCalls] = useState<Call[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Call[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);   // sessions fetch failed
  const [searchError, setSearchError] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("calls");
  const [projects, setProjects] = useState<string[]>([]);
  const [projectFilter, setProjectFilter] = useState("");
  const [oldestFirst, setOldestFirst] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const refresh = useCallback(() => {
    get<Session[]>("/api/sessions")
      .then((v) => { setSessions(v); setLoadError(null); })
      .catch((e) => setLoadError(String(e?.message || e)));
    listProjects().then((r) => setProjects(r.projects)).catch(() => {});
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
    if (!selected) { setCalls([]); return; }
    let alive = true;
    const id = selected;
    // Discard an out-of-order response for a session no longer selected.
    get<Call[]>(`/session/${encodeURIComponent(id)}`)
      .then((v) => { if (alive && id === selected) setCalls(v); })
      .catch(() => { if (alive && id === selected) setCalls([]); });
    return () => { alive = false; };
  }, [selected]);

  useEffect(() => {
    if (!query.trim()) { setResults(null); setSearchError(null); return; }
    const t = window.setTimeout(() => {
      get<Call[]>(`/api/search?q=${encodeURIComponent(query.trim())}`)
        .then((v) => { setResults(v); setSearchError(null); })
        .catch((e) => { setResults(null); setSearchError(String(e?.message || e)); });
    }, 300);
    return () => window.clearTimeout(t);
  }, [query]);

  const shown = results ?? calls;

  // The grouped list in its rendered order. Computed once so the JSX and
  // the phone's prev/next arrows walk the same sequence — the eye's order,
  // headings and pins included.
  const groupedList = (() => {
    const list = pinnedFirst(timeSorted(sessions.filter((s) => matchesFilter(s, projectFilter)), oldestFirst));
    const groups: { key: string | null; title: string | null; items: typeof list }[] = [];
    if (projectFilter !== "") {
      groups.push({ key: null, title: null, items: list });
    } else {
      const byKey = new Map<string, typeof list>();
      for (const sess of list) {
        const key = sess.project ?? (sess.run_id ? RUN_PREFIX + sess.run_id : "");
        if (!byKey.has(key)) byKey.set(key, []);
        byKey.get(key)!.push(sess);
      }
      for (const [key, items] of byKey) {
        groups.push({
          key: key || null,
          title: key.startsWith(RUN_PREFIX)
            ? key.slice(RUN_PREFIX.length)
            : (key || "unfiled"),
          items,
        });
      }
    }
    return groups;
  })();
  const ordered = groupedList.flatMap((g) => g.items);

  return (
    <div className={`layout ${selected ? "has-detail" : ""}`}>
      <div className="sidebar">
        <input
          className="search"
          placeholder="Search prompts, outputs, agents…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <ProjectFilter projects={projects} value={projectFilter} onChange={setProjectFilter}
                       runGroups={[...new Set(sessions.filter((x) => !x.project && x.run_id).map((x) => x.run_id!))]}
                       hasPinned={sessions.some((x) => x.pinned)}
                       knownApps={[...new Set(sessions.map((x) => x.app_id).filter(Boolean))] as string[]}
                       onCreated={refresh}
                       sessionCount={sessions.filter((x) => matchesFilter(x, projectFilter)).length} />
        <TimeSortToggle oldestFirst={oldestFirst} onChange={setOldestFirst} />
        {(() => {
          return groupedList.map((g) => (
            <div key={g.key ?? g.title ?? "all"} className="proj-group">
              {g.title && (
                <div
                  className={`proj-heading ${g.key ? "" : "unfiled"} ${g.key?.startsWith(RUN_PREFIX) ? "run-group" : ""}`}
                  title={g.key === null
                    ? "sessions with no project and no run"
                    : g.key.startsWith(RUN_PREFIX)
                      ? "unfiled sessions of this run — the run is their default group; click to focus"
                      : "click to focus this project"}
                  onClick={() => { if (g.key) setProjectFilter(g.key); }}
                >
                  {g.title}<span className="proj-count">{g.items.length}</span>
                </div>
              )}
              {g.items.map((s) => (
          <div
            key={s.session_id}
            className={`card ${selected === s.session_id ? "selected" : ""} ${s.session_id.startsWith("replay-") ? "replay" : ""} ${(s.error_count ?? 0) > 0 ? "has-errors" : ""}`}
            onClick={() => { setQuery(""); setSelected(s.session_id); }}
          >
            <PinButton scope="session" refId={s.session_id} pinned={s.pinned}
                       onSaved={refresh} />
            <button
              className="card-edit"
              title="Rename / assign to a project"
              onClick={(e) => {
                e.stopPropagation();
                setEditing(editing === s.session_id ? null : s.session_id);
              }}
            >
              ✎
            </button>
            <button
              className="card-del"
              title="Delete this session's captured calls"
              onClick={(e) => {
                e.stopPropagation();
                setDeleting(deleting === s.session_id ? null : s.session_id);
              }}
            >
              ×
            </button>
            {deleting === s.session_id && (
              <div className="label-edit" onClick={(e) => e.stopPropagation()}>
                <div className="muted" style={{ fontSize: 12.5 }}>
                  Delete this session and its {s.call_count} calls, permanently?
                </div>
                <div className="key-actions">
                  <button
                    className="link-btn project-purge"
                    onClick={() => {
                      del(`/api/sessions/${encodeURIComponent(s.session_id)}`)
                        .then(() => {
                          setDeleting(null);
                          setSelected((cur) => (cur === s.session_id ? null : cur));
                          refresh();
                        })
                        .catch(() => setDeleting(null));
                    }}
                  >
                    delete permanently
                  </button>
                  <button className="link-btn" onClick={() => setDeleting(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
            <div className="card-title title-with-chip" title={s.session_id}>
              <span className="title-text">{s.label ?? s.session_id}</span>
              {s.run_id && (
                <span className="run-chip"
                      title={`this session belongs to run "${s.run_id}" — click to open it in the Loop Lens`}
                      onClick={(e) => { e.stopPropagation(); onOpenRun?.(s.run_id!); }}>
                  ↻ {s.run_id}
                </span>
              )}
            </div>
            {editing === s.session_id && (
              <LabelEditor scope="session" refId={s.session_id}
                           label={s.label} project={s.project} projects={projects}
                           onSaved={refresh} onClose={() => setEditing(null)} />
            )}
            <div className="card-meta-row">
              <span className="dim">{fmtAgo(s.last_call_at ?? s.started_at)}</span>
              <span className="dim">{s.call_count} calls</span>
              <span className="card-cost">{fmtUsd(s.total_cost_usd)}</span>
            </div>
            <div className="card-sub">
              {s.session_id.startsWith("replay-") && (
                <span className="badge replay" title="a re-run of a captured call">replay</span>
              )}
              {s.agent_name && <span className="badge fw">{s.agent_name}</span>}
              {s.team && <span className="badge team" title="team card that made these calls">{s.team}</span>}
              {s.project && (
                <span className="badge fw"
                      title={s.project_auto
                        ? `filed automatically: this session's app matches the project's binding; assign a project by hand (✎) to override`
                        : "project"}>
                  {s.project}{s.project_auto ? " ·auto" : ""}
                </span>
              )}
              {(s.error_count ?? 0) > 0 && (
                <span className="badge error" title="calls that actually failed">{s.error_count} failed</span>
              )}
              {(s.blocked_count ?? 0) > 0 && (
                <span className="badge blocked" title="calls the ledger refused on purpose (over budget)">{s.blocked_count} blocked</span>
              )}
            </div>
          </div>
              ))}
            </div>
          ));
        })()}
      </div>
      <div className="main">
        {selected && (() => {
          const idx = ordered.findIndex((x) => x.session_id === selected);
          return (
            <div className="mobile-nav">
              <button className="mobile-back" onClick={() => setSelected(null)}>
                ← all sessions
              </button>
              <span className="mnav-spacer" />
              {idx >= 0 && <span className="mobile-pos">{idx + 1} / {ordered.length}</span>}
              <button className="mobile-step" disabled={idx <= 0}
                      onClick={() => { setQuery(""); setSelected(ordered[idx - 1].session_id); }}>‹</button>
              <button className="mobile-step" disabled={idx < 0 || idx >= ordered.length - 1}
                      onClick={() => { setQuery(""); setSelected(ordered[idx + 1].session_id); }}>›</button>
            </div>
          );
        })()}
        {results === null && selected && (
          <>
            <SessionHeader
              session={sessions.find((x) => x.session_id === selected) ?? null}
              sessionId={selected}
              calls={calls}
              onOpenSession={(sid) => { setQuery(""); setSelected(sid); }}
            />
            <WhatIf params={`session_id=${encodeURIComponent(selected)}`} />
            <BatchReplay scope="session" refId={selected}
                         numberOf={(aid) => {
                           const i = calls.findIndex((c) => c.action_id === aid);
                           return i >= 0 ? i + 1 : null;
                         }}
                         onOpenSession={(sid) => { setQuery(""); setSelected(sid); }} />
          </>
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
        {searchError ? (
          <div className="empty">Search failed: {searchError} <button className="link-btn" onClick={() => setQuery((q) => q + " ")}>Retry</button></div>
        ) : loadError && sessions.length === 0 ? (
          <div className="empty">Could not load sessions: {loadError} <button className="link-btn" onClick={refresh}>Retry</button></div>
        ) : sessions.length === 0 && results === null ? (
          <WiringGuide />
        ) : shown.length === 0 ? (
          <div className="empty">
            {results !== null ? "No matches." : "Select a session to inspect its calls."}
          </div>
        ) : results !== null || mode === "calls" ? (
          // Latest call on top for live watching. Search results already
          // arrive newest-first; session calls arrive in conversation order,
          // so flip them for display only — Flow, Trace, and replay keep
          // true chronological order.
          (results !== null ? shown : [...shown].reverse()).map((c) => (
            <CallCard key={c.action_id} call={c}
                      num={results !== null ? undefined : calls.indexOf(c) + 1}
                      onOpenSession={(sid) => { setQuery(""); setSelected(sid); }} />
          ))
        ) : mode === "flow" ? (
          <FlowView calls={calls} />
        ) : (
          <TraceView calls={calls} />
        )}
      </div>
    </div>
  );
}
