import { useCallback, useEffect, useState } from "react";
import {
  del as apiDel,
  FlaggedCall, flagBadgeClass, flagInfo, fmtAgo, fmtNum, fmtTime, fmtUsd, get,
  Iteration, liveUpdates, post, Run, runStatusInfo,
 listProjects,
} from "../api";
import CompareView from "./CompareView";
import { LabelEditor, matchesFilter, PinButton, pinnedFirst, ProjectFilter } from "./LabelBits";
import ProviderMark from "./ProviderMark";
import BatchReplay from "./BatchReplay";
import WhatIf from "./WhatIf";

/** The bookkeeper: a small cartoon raccoon whose expression is the run's
 *  status. Decorative only: inline SVG, aria-hidden, fixed box (no layout
 *  shift), compositor-only animation so slow it barely moves, and it holds
 *  still entirely under prefers-reduced-motion. */
function RunMascot({ status, small }: { status: Run["status"]; small?: boolean }) {
  const mood: Record<Run["status"], string> = {
    running: "on the clock: keeping the books while your loop spends",
    flagged: "smelled something odd in this loop",
    complete: "the loop says it finished; the raccoon believes it",
    ended: "the loop went quiet, so the bookkeeper naps",
    stopped: "playing dead until you allow calls again",
  };
  const eyes =
    status === "ended" ? (
      // asleep: gentle closed lids
      <g className="rac-eyes" stroke="var(--bg-panel)" strokeWidth="1.1"
         strokeLinecap="round" fill="none">
        <path d="M6.9,11.9 q1.2,0.9 2.4,0" />
        <path d="M14.7,11.9 q1.2,0.9 2.4,0" />
      </g>
    ) : status === "complete" ? (
      // content: happy upward arcs
      <g className="rac-eyes" stroke="#fff" strokeWidth="1.1"
         strokeLinecap="round" fill="none">
        <path d="M6.9,12.1 q1.2,-1.1 2.4,0" />
        <path d="M14.7,12.1 q1.2,-1.1 2.4,0" />
      </g>
    ) : status === "stopped" ? (
      // playing dead: little x eyes
      <g className="rac-eyes" stroke="#fff" strokeWidth="0.95"
         strokeLinecap="round" fill="none">
        <path d="M7.2,10.9 l1.8,1.8 M9,10.9 l-1.8,1.8" />
        <path d="M15,10.9 l1.8,1.8 M16.8,10.9 l-1.8,1.8" />
      </g>
    ) : (
      // awake (running / flagged): round eyes catching the light
      <g className="rac-eyes" fill="#fff">
        <circle cx="8.1" cy="11.8" r="1.25" />
        <circle cx="15.9" cy="11.8" r="1.25" />
        <circle cx="8.45" cy="11.45" r="0.4" fill="var(--bg-panel)" />
        <circle cx="16.25" cy="11.45" r="0.4" fill="var(--bg-panel)" />
      </g>
    );
  return (
    <span className={`rac rac-${status} ${small ? "rac-sm" : ""}`}
          aria-hidden="true" title={mood[status]}>
      <svg viewBox="0 0 24 22" width="100%" height="100%">
        {status === "flagged" && (
          <g className="rac-flag">
            <line x1="22" y1="16" x2="22" y2="3.4" stroke="var(--text-dim)"
                  strokeWidth="0.9" strokeLinecap="round" />
            <path className="rac-flag-cloth" d="M22.4,3.2 L27.2,4.8 L22.4,6.6 Z"
                  fill="var(--amber)" />
          </g>
        )}
        <g className="rac-head">
          {/* ears, with darker inner ear */}
          <path d="M4.2,8.2 L6.2,2.6 L10.4,5.9 Z" fill="var(--text-dim)" />
          <path d="M19.8,8.2 L17.8,2.6 L13.6,5.9 Z" fill="var(--text-dim)" />
          <path d="M5.6,7.2 L6.6,4.4 L8.7,6.1 Z" fill="var(--bg-panel)" />
          <path d="M18.4,7.2 L17.4,4.4 L15.3,6.1 Z" fill="var(--bg-panel)" />
          {/* head */}
          <ellipse cx="12" cy="13" rx="8.6" ry="7.6" fill="var(--text-dim)" />
          {/* the mask */}
          <path d="M3.6,11.4 Q7,8.6 12,9.6 Q17,8.6 20.4,11.4
                   Q19.6,15.2 15.6,14.4 Q12,13.6 8.4,14.4 Q4.4,15.2 3.6,11.4 Z"
                fill="var(--bg-panel)" opacity="0.92" />
          {eyes}
          {/* snout */}
          <ellipse cx="12" cy="17.1" rx="3.7" ry="2.7" fill="var(--text)" opacity="0.9" />
          <ellipse cx="12" cy="15.9" rx="1.35" ry="1.05" fill="var(--bg-panel)" />
        </g>
      </svg>
    </span>
  );
}

function FlagCard({ flag, onOpenSession }: { flag: FlaggedCall; onOpenSession: (s: string) => void }) {
  const names: string[] = JSON.parse(flag.loop_flags);
  const tools = (flag.tool_calls ?? [])
    .map((tc) => tc.name)
    .filter(Boolean)
    .join(", ");
  return (
    <div className="card flag-card">
      <div className="flag-head">
        {names.map((n) => (
          <span key={n} className={`badge ${flagBadgeClass(n)}`}>
            {n}
          </span>
        ))}
        <span className="flag-title">{names.map((n) => flagInfo(n).title).join(" · ")}</span>
        <span className="spacer" />
        <span className="muted">
          iteration {flag.iteration ?? "—"} · step {flag.step_index ?? "—"} · {fmtTime(flag.timestamp)}
        </span>
      </div>
      <div className="flag-detail">
        {names.map((n) => (
          <p key={n}>{flagInfo(n).detail}</p>
        ))}
        {tools && (
          <p className="muted">
            Tool call on this step: <code>{tools}</code>
            {flag.tool_calls?.[0]?.arguments ? (
              <> · args <code>{JSON.stringify(flag.tool_calls[0].arguments).slice(0, 120)}</code></>
            ) : null}
          </p>
        )}
      </div>
      {flag.session_id && (
        <button className="link-btn" onClick={() => onOpenSession(flag.session_id!)}>
          Open session {flag.session_id} →
        </button>
      )}
    </div>
  );
}

export default function RunsView({ onOpenSession }: { onOpenSession: (s: string) => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [compare, setCompare] = useState<string[]>([]);
  const toggleCompare = (id: string) =>
    setCompare((cur) =>
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur.slice(-1), id],
    );
  const [detail, setDetail] = useState<Run | null>(null);
  const [iterations, setIterations] = useState<Iteration[]>([]);
  const [flags, setFlags] = useState<FlaggedCall[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [projects, setProjects] = useState<string[]>([]);
  const [projectFilter, setProjectFilter] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => { setConfirmStop(false); setCopied(false); }, [selected]);

  // #75 — stop/resume must flip the sidebar tile and the detail badge in
  // the same render: update both local copies first, then re-fetch.
  const setRunStatus = (id: string, status: Run["status"]) => {
    setRuns((cur) => cur.map((r) => (r.run_id === id ? { ...r, status } : r)));
    setDetail((cur) => (cur && cur.run_id === id ? { ...cur, status } : cur));
  };

  const refresh = useCallback(() => {
    get<Run[]>("/api/runs").then(setRuns).catch((e) => setError(String(e)));
    listProjects().then((r) => setProjects(r.projects)).catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    return liveUpdates(refresh);
  }, [refresh]);

  useEffect(() => {
    if (!selected) return;
    get<Run>(`/api/runs/${encodeURIComponent(selected)}`).then(setDetail).catch(() => setDetail(null));
    get<Iteration[]>(`/api/runs/${encodeURIComponent(selected)}/iterations`)
      .then(setIterations)
      .catch(() => setIterations([]));
    get<FlaggedCall[]>(`/api/runs/${encodeURIComponent(selected)}/flags`)
      .then(setFlags)
      .catch(() => setFlags([]));
  }, [selected, runs]);

  const maxCost = Math.max(...iterations.map((i) => i.cost_usd || 0), 0.000001);

  return (
    <div className="layout">
      <div className="sidebar">
        {error && <div className="empty">{error}</div>}
        {runs.length === 0 && !error && (
          <div className="empty">
            No loop runs yet.
            <br />
            <span className="muted">
              Start one with <code>agenticledger run -- …</code> or send
              x-agenticledger-run-id headers.
            </span>
          </div>
        )}
        <ProjectFilter projects={projects} value={projectFilter} onChange={setProjectFilter}
                       hasPinned={runs.some((x) => x.pinned)}
                       knownApps={[...new Set(runs.map((x) => x.app_id).filter(Boolean))] as string[]}
                       onCreated={refresh}
                       sessionCount={runs.filter((x) => matchesFilter(x, projectFilter)).length} />
        {pinnedFirst(runs.filter((r) => matchesFilter(r, projectFilter))).map((r) => (
          <div
            key={r.run_id}
            className={`card ${selected === r.run_id ? "selected" : ""}`}
            onClick={() => setSelected(r.run_id)}
          >
            <PinButton scope="run" refId={r.run_id} pinned={r.pinned} onSaved={refresh} />
            <button
              className="card-edit"
              title="Rename / assign to a project"
              onClick={(e) => {
                e.stopPropagation();
                setEditing(editing === r.run_id ? null : r.run_id);
              }}
            >
              ✎
            </button>
            <button
              className={`card-cmp ${compare.includes(r.run_id) ? "on" : ""}`}
              title={compare.includes(r.run_id)
                ? "Remove from comparison"
                : "Compare this run (pick two)"}
              onClick={(e) => { e.stopPropagation(); toggleCompare(r.run_id); }}
            >
              ⇆
            </button>
            <div className="card-title" title={r.run_id}>
              {r.label ?? r.run_id} <span className={`badge ${r.status}`} title={runStatusInfo(r.status)}>{r.status}</span>
              <RunMascot status={r.status} small />
            </div>
            {r.label && <div className="card-id mono">{r.run_id}</div>}
            {editing === r.run_id && (
              <LabelEditor scope="run" refId={r.run_id}
                           label={r.label} project={r.project} projects={projects}
                           onSaved={refresh} onClose={() => setEditing(null)} />
            )}
            <div className="card-sub">
              <span>{fmtAgo(r.last_call_at)}</span>
              <span>{r.iterations ?? "?"} iterations</span>
              <span>{r.call_count} calls</span>
              <span>{fmtUsd(r.total_cost_usd)}</span>
              {r.models && (
                <span className="mono model-cell">
                  <ProviderMark model={r.models.split(",")[0]} />
                  {r.models}
                </span>
              )}
              {r.framework && <span className="badge fw">{r.framework}</span>}
              {r.project && (
                <span className="badge fw"
                      title={r.project_auto
                        ? "filed automatically: this run's app matches the project's binding"
                        : "project"}>
                  {r.project}{r.project_auto ? " ·auto" : ""}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="main">
        {compare.length === 2 ? (
          <CompareView
            a={compare[0]}
            b={compare[1]}
            onClose={() => setCompare([])}
            onOpenSession={onOpenSession}
          />
        ) : !detail ? (
          <div className="empty">
            {compare.length === 1
              ? <>Pick a second run with <span className="mono">⇆</span> to compare.</>
              : "Select a run to open the Loop Lens."}
          </div>
        ) : (
          <>
            <h2 className="page-title">
              {detail.label ?? detail.run_id}{" "}
              <span className={`badge ${detail.status}`} title={runStatusInfo(detail.status)}>{detail.status}</span>
              <RunMascot status={detail.status} />
              {detail.status === "stopped" ? (
                <button className="link-btn" style={{ marginLeft: 10 }}
                        title="Lifts the block: calls under this run id flow again. Restarts nothing; if your loop exited, start it yourself."
                        onClick={() => {
                          apiDel(`/api/runs/${encodeURIComponent(detail.run_id)}/stop`)
                            .then(() => get<Run>(`/api/runs/${encodeURIComponent(detail.run_id)}`))
                            .then((r) => setRunStatus(r.run_id, r.status))
                            .then(refresh);
                        }}>
                  allow calls again
                </button>
              ) : confirmStop ? (
                <span className="key-actions" style={{ marginLeft: 10 }}>
                  <button className="link-btn project-purge"
                          onClick={() => {
                            post(`/api/runs/${encodeURIComponent(detail.run_id)}/stop`, {})
                              .then(() => setRunStatus(detail.run_id, "stopped"))
                              .then(refresh);
                            setConfirmStop(false);
                          }}>
                    {detail.status === "running" || detail.status === "flagged"
                      ? "block this run's calls" : "block future calls under this id"}
                  </button>
                  <button className="link-btn" onClick={() => setConfirmStop(false)}>Cancel</button>
                </span>
              ) : detail.status === "running" || detail.status === "flagged" ? (
                <button className="link-btn" style={{ marginLeft: 10 }}
                        title="Refuses this run's next calls at the proxy, so they cost nothing. Does not kill your process: a loop that cannot call usually exits on its own. History stays."
                        onClick={() => setConfirmStop(true)}>
                  ⊘ block calls
                </button>
              ) : (
                <button className="link-btn" style={{ marginLeft: 10 }}
                        title="This run is not running now. If calls ever arrive under this run id again (an always-on agent, a restarted loop), they will be refused until you allow them. History stays."
                        onClick={() => setConfirmStop(true)}>
                  ⊘ block future calls
                </button>
              )}
            </h2>
            <div className="muted">
              <button
                className="session-id mono"
                title="click to copy the run id"
                onClick={() => {
                  navigator.clipboard?.writeText(detail.run_id).then(() => {
                    setCopied(true);
                    window.setTimeout(() => setCopied(false), 1200);
                  }).catch(() => {});
                }}
              >
                {detail.run_id} {copied ? "✓ copied" : "⧉"}
              </button>
              {" · "}started {fmtTime(detail.started_at)} · last call {fmtTime(detail.last_call_at)}
              {detail.models && <> · <span className="mono">{detail.models}</span></>}
            </div>

            <div className="stats-row">
              <div className="stat"><div className="v">{detail.iterations ?? "—"}</div><div className="l">iterations</div></div>
              <div className="stat"><div className="v">{fmtUsd(detail.total_cost_usd)}</div><div className="l">total cost</div></div>
              <div className="stat"><div className="v">{detail.call_count}</div><div className="l">llm calls</div></div>
              <div className="stat"><div className="v">{fmtNum(detail.total_tokens_in)}</div><div className="l">tokens in</div></div>
              <div className="stat"><div className="v">{fmtNum(detail.total_tokens_out)}</div><div className="l">tokens out</div></div>
              <div className="stat">
                <div className="v" style={{ color: detail.flagged_calls ? "var(--amber)" : undefined }}>
                  {detail.flagged_calls}
                </div>
                <div className="l">flagged calls</div>
              </div>
            </div>

            <WhatIf params={`run_id=${encodeURIComponent(detail.run_id)}`} />
            <BatchReplay scope="run" refId={detail.run_id} onOpenSession={onOpenSession} />

            {iterations.length > 0 && (
              <>
                <div className="section-title">Cost per iteration</div>
                <div className="ribbon">
                  {iterations.map((it) => (
                    <div
                      key={String(it.iteration)}
                      className={`bar ${it.error_calls ? "errored" : it.flagged_calls ? "flagged" : ""}`}
                      style={{ height: `${Math.max((100 * (it.cost_usd || 0)) / maxCost, 3)}%` }}
                      title={`iteration ${it.iteration}: ${fmtUsd(it.cost_usd)}, ${it.call_count} calls. Click to open its session`}
                      onClick={() => it.session_id && onOpenSession(it.session_id)}
                    />
                  ))}
                </div>
                <div className="ribbon-labels">
                  {iterations.map((it) => (
                    <div key={String(it.iteration)}>{it.iteration ?? "?"}</div>
                  ))}
                </div>

                {flags.length > 0 && (
                  <>
                    <div className="section-title">Flags: what happened and why</div>
                    {flags.map((f) => (
                      <FlagCard key={f.action_id} flag={f} onOpenSession={onOpenSession} />
                    ))}
                  </>
                )}

                <div className="section-title">Iterations</div>
                <table className="grid">
                  <thead>
                    <tr>
                      <th>#</th><th>calls</th><th>cost</th><th>tokens in/out</th>
                      <th>cache reads</th><th>flags</th><th>errors</th><th>started</th><th>session</th>
                    </tr>
                  </thead>
                  <tbody>
                    {iterations.map((it) => (
                      <tr
                        key={String(it.iteration)}
                        className={it.session_id ? "row-link" : ""}
                        title={it.session_id ? "Open this iteration's session" : undefined}
                        onClick={() => it.session_id && onOpenSession(it.session_id)}
                      >
                        <td>{it.iteration ?? "—"}</td>
                        <td>{it.call_count}</td>
                        <td>{fmtUsd(it.cost_usd)}</td>
                        <td>{fmtNum(it.tokens_in)} / {fmtNum(it.tokens_out)}</td>
                        <td>{fmtNum(it.cache_read_tokens)}</td>
                        <td>{it.flagged_calls ? <span className="badge flagged">{it.flagged_calls}</span> : "—"}</td>
                        <td>{it.error_calls ? <span className="badge error">{it.error_calls}</span> : "—"}</td>
                        <td>{fmtTime(it.started_at)}</td>
                        <td className="session-link">
                          {it.session_id
                            ? (it.session_id.length > 14 ? it.session_id.slice(0, 13) + "…" : it.session_id) + " →"
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
