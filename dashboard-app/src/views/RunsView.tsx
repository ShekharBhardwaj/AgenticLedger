import { useCallback, useEffect, useRef, useState } from "react";
import {
  del as apiDel,
  FlaggedCall, flagBadgeClass, flagInfo, fmtAgo, fmtNum, fmtTime, fmtUsd, get,
  Iteration, LiveCall, liveUpdates, post, Run, runStatusInfo,
 listProjects,
} from "../api";
import CompareView from "./CompareView";
import { LabelEditor, matchesFilter, PinButton, pinnedFirst, ProjectFilter, TimeSortToggle, timeSorted } from "./LabelBits";
import { setLabel } from "../api";
import ProviderMark from "./ProviderMark";
import BatchReplay from "./BatchReplay";
import WhatIf from "./WhatIf";
import { RaccoonHead } from "../Raccoon";

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
  return (
    <span className={`rac rac-${status} ${small ? "rac-sm" : ""}`}
          aria-hidden="true" title={mood[status]}>
      <svg viewBox="0 0 24 22" width="100%" height="100%">
        {status === "running" && (
          <g className="rac-speed" stroke="var(--text-dim)" strokeWidth="1.1"
             strokeLinecap="round">
            <line x1="0.2" y1="8.6" x2="4.2" y2="8.6" />
            <line x1="-0.8" y1="12.6" x2="3.6" y2="12.6" />
            <line x1="0.6" y1="16.6" x2="4.4" y2="16.6" />
          </g>
        )}
        {status === "flagged" && (
          <g className="rac-flag">
            <line x1="22" y1="16" x2="22" y2="3.4" stroke="var(--text-dim)"
                  strokeWidth="0.9" strokeLinecap="round" />
            <path className="rac-flag-cloth" d="M22.4,3.2 L27.2,4.8 L22.4,6.6 Z"
                  fill="var(--amber)" />
          </g>
        )}
        <RaccoonHead mood={status} />
        {status === "ended" && (
          /* tiny z's drifting up from the sleeping head, staggered */
          <g className="rac-zzz" fill="var(--text-dim)" fontFamily="sans-serif" fontWeight="700">
            <text x="19.2" y="6.2" fontSize="3.2">z</text>
            <text x="21.4" y="4.2" fontSize="4">z</text>
            <text x="23.8" y="2.2" fontSize="4.8">z</text>
          </g>
        )}
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
  const [oldestFirst, setOldestFirst] = useState(false);
  // Live Loop (#96): calls staged the moment the proxy announces them,
  // scoped to the open run. Cleared on every run switch.
  const [feed, setFeed] = useState<(LiveCall & { at: number })[]>([]);
  const [ceilingEdit, setCeilingEdit] = useState<string | null>(null);
  const selectedRef = useRef<string | null>(null);
  useEffect(() => { selectedRef.current = selected; setFeed([]); }, [selected]);

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
    return liveUpdates(refresh, (ev) => {
      if (ev.run_id && ev.run_id === selectedRef.current) {
        setFeed((cur) => [{ ...ev, at: Date.now() }, ...cur].slice(0, 40));
      }
    });
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

  // The list in its rendered order — the phone's prev/next arrows walk
  // exactly what the eye saw, pins and sort direction included.
  const ordered = pinnedFirst(timeSorted(runs.filter((r) => matchesFilter(r, projectFilter)), oldestFirst));

  return (
    <div className={`layout ${selected ? "has-detail" : ""}`}>
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
                       runGroups={[...new Set(runs.filter((x) => !x.project).map((x) => x.run_id))]}
                       hasPinned={runs.some((x) => x.pinned)}
                       knownApps={[...new Set(runs.map((x) => x.app_id).filter(Boolean))] as string[]}
                       onCreated={refresh}
                       sessionCount={runs.filter((x) => matchesFilter(x, projectFilter)).length} />
        <TimeSortToggle oldestFirst={oldestFirst} onChange={setOldestFirst} />
        {ordered.map((r) => (
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
            <div className="card-title card-title-row" title={r.run_id}>
              <span className="card-name">{r.label ?? r.run_id}</span>
              <span className={`badge ${r.status}`} title={runStatusInfo(r.status)}>{r.status}</span>
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
                <span className="mono model-cell" title={r.models.split(",").join("\n")}>
                  <ProviderMark model={r.models.split(",")[0]} />
                  {r.models.split(",")[0]}
                  {r.models.includes(",") && (
                    <span className="model-more">+{r.models.split(",").length - 1} more</span>
                  )}
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
        {selected && (() => {
          const idx = ordered.findIndex((r) => r.run_id === selected);
          return (
            <div className="mobile-nav">
              <button className="mobile-back" onClick={() => setSelected(null)}>
                ← all runs
              </button>
              <span className="mnav-spacer" />
              {idx >= 0 && <span className="mobile-pos">{idx + 1} / {ordered.length}</span>}
              <button className="mobile-step" disabled={idx <= 0}
                      onClick={() => setSelected(ordered[idx - 1].run_id)}>‹</button>
              <button className="mobile-step" disabled={idx < 0 || idx >= ordered.length - 1}
                      onClick={() => setSelected(ordered[idx + 1].run_id)}>›</button>
            </div>
          );
        })()}
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
              {detail.models && (
                <> · <span className="mono" title={detail.models.split(",").join("\n")}>
                  {detail.models.split(",")[0]}
                  {detail.models.includes(",") &&
                    ` +${detail.models.split(",").length - 1} more`}
                </span></>
              )}
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

            {(() => {
              // The bill, before the bill (#0.11): burn, projection, and
              // the run's own ceiling — editable while it runs.
              const burn = detail.burn_last_hour_usd ?? 0;
              const ceiling = detail.budget_usd ?? null;
              const spent = detail.total_cost_usd || 0;
              const liveNow = detail.status === "running" || detail.status === "flagged";
              const morning = new Date();
              morning.setHours(8, 0, 0, 0);
              if (morning.getTime() <= Date.now()) morning.setDate(morning.getDate() + 1);
              const hoursToMorning = (morning.getTime() - Date.now()) / 3_600_000;
              const projected = spent + burn * hoursToMorning;
              const frac = ceiling ? Math.min(spent / ceiling, 1) : 0;
              const saveCeiling = (v: number) => {
                setLabel("run", detail.run_id, { budget_usd: v })
                  .then(refresh).catch(() => {});
                setCeilingEdit(null);
              };
              return (
                <div className="spend-meter">
                  <span className="mono">{fmtUsd(spent)} spent</span>
                  {liveNow && burn > 0 && (
                    <span className="muted"
                          title="the last hour's spend, projected forward unchanged">
                      · burning {fmtUsd(burn)}/h · at this pace {fmtUsd(projected)} by{" "}
                      {morning.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                    </span>
                  )}
                  {ceiling ? (
                    <>
                      <span className={`meter-track ${frac >= 1 ? "at" : frac >= 0.8 ? "near" : ""}`}
                            title={`ceiling: calls are refused once spend reaches ${fmtUsd(ceiling)}`}>
                        <span className="meter-fill" style={{ width: `${frac * 100}%` }} />
                      </span>
                      <span className="mono">{fmtUsd(ceiling)} ceiling</span>
                      <button className="link-btn" onClick={() => setCeilingEdit(String(ceiling))}>edit</button>
                      <button className="link-btn" title="remove the ceiling; calls flow again"
                              onClick={() => saveCeiling(0)}>clear</button>
                    </>
                  ) : ceilingEdit === null ? (
                    <button className="link-btn"
                            title="refuse this run's calls at the proxy once its spend reaches a dollar amount; survives restarts"
                            onClick={() => setCeilingEdit("")}>+ cost ceiling</button>
                  ) : null}
                  {ceilingEdit !== null && (
                    <span className="key-actions">
                      <input autoFocus className="ceiling-input" placeholder="$"
                             value={ceilingEdit}
                             onChange={(e) => setCeilingEdit(e.target.value)}
                             onKeyDown={(e) => {
                               if (e.key === "Enter") {
                                 const v = parseFloat(ceilingEdit);
                                 if (!Number.isNaN(v) && v >= 0) saveCeiling(v);
                               }
                               if (e.key === "Escape") setCeilingEdit(null);
                             }} />
                      <button className="link-btn" onClick={() => {
                        const v = parseFloat(ceilingEdit);
                        if (!Number.isNaN(v) && v >= 0) saveCeiling(v);
                      }}>Save</button>
                      <button className="link-btn" onClick={() => setCeilingEdit(null)}>Cancel</button>
                    </span>
                  )}
                </div>
              );
            })()}

            {(
              <>
                <div className="section-title"
                     title="a live feed: the proxy announces every capture the moment it happens">
                  Calls, as they happen
                </div>
                {feed.length === 0 ? (
                  <div className="muted live-empty">
                    {detail.status === "stopped"
                      ? "Watching the wall. A refused knock under this run lands here the moment it happens."
                      : detail.status === "ended"
                        ? "Quiet. If calls ever arrive under this run again, they appear here the moment they happen."
                        : "Watching. The next call under this run appears here the moment it happens."}
                  </div>
                ) : (
                  <div className="live-feed">
                    {feed.map((ev) => (
                      <div
                        key={`${ev.action_id}-${ev.at}`}
                        className={`live-row ${ev.blocked ? "blocked" : ev.error ? "errored" : ""}`}
                        title={ev.session_id
                          ? `session ${ev.session_id}: click to open`
                          : undefined}
                        onClick={() => ev.session_id && onOpenSession(ev.session_id)}
                      >
                        <span className="mono live-when">{new Date(ev.at).toLocaleTimeString()}</span>
                        <span className="live-iter">{ev.iteration != null ? `#${ev.iteration}` : ""}</span>
                        <span className="mono model-cell live-model">
                          <ProviderMark provider={ev.provider} model={ev.model_id} />
                          {ev.model_id}
                        </span>
                        <span className="mono live-num">{fmtNum(ev.tokens_in)} / {fmtNum(ev.tokens_out)} tok</span>
                        <span className="mono live-num">{ev.latency_ms ? `${Math.round(ev.latency_ms)} ms` : ""}</span>
                        <span className="mono live-num">{fmtUsd(ev.cost_usd)}</span>
                        <span className="live-verdict">
                          {ev.blocked ? <span className="badge blocked">blocked</span>
                            : ev.error ? <span className="badge error">error</span>
                            : ev.flags.length > 0 ? <span className="badge flagged">{ev.flags.join(", ")}</span>
                            : ev.budget_warning ? <span className="badge flagged">budget warning</span>
                            : ev.status_code !== 200 ? (
                              <span className="live-ok"
                                    title="counted apart: a probe or transient failure the ledger does not count as an agent error">
                                {ev.status_code}
                              </span>
                            ) : <span className="live-ok">ok</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}

            <WhatIf params={`run_id=${encodeURIComponent(detail.run_id)}`} />
            <BatchReplay scope="run" refId={detail.run_id} onOpenSession={onOpenSession} />

            {iterations.length > 0 && (
              <>
                <div className="section-title">Cost per iteration</div>
                <div className="ribbon">
                  {iterations.map((it) => (
                    <div
                      key={String(it.iteration)}
                      className={`bar ${it.error_calls ? "errored" : it.blocked_calls ? "blocked" : it.flagged_calls ? "flagged" : ""}`}
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
                        className={it.session_id && it.session_count <= 1 ? "row-link" : ""}
                        title={it.session_id && it.session_count <= 1 ? "Open this iteration's session" : undefined}
                        onClick={() => it.session_id && it.session_count <= 1 && onOpenSession(it.session_id)}
                      >
                        <td>{it.iteration ?? (it.blocked_calls
                          ? <span title="the wall: these calls were refused before any iteration ran">⊘</span>
                          : "—")}</td>
                        <td>{it.call_count}</td>
                        <td>{fmtUsd(it.cost_usd)}</td>
                        <td>{fmtNum(it.tokens_in)} / {fmtNum(it.tokens_out)}</td>
                        <td>{fmtNum(it.cache_read_tokens)}</td>
                        <td>{it.flagged_calls ? <span className="badge flagged">{it.flagged_calls}</span> : "—"}</td>
                        <td>
                          {it.error_calls ? <span className="badge error">{it.error_calls}</span> : null}
                          {it.blocked_calls ? (
                            <span className="badge blocked"
                                  title="refused at the wall (kill switch or budget): the block working, not the agent failing. These cost nothing.">
                              {it.blocked_calls} blocked
                            </span>
                          ) : null}
                          {!it.error_calls && !it.blocked_calls && "—"}
                        </td>
                        <td>{fmtTime(it.started_at)}</td>
                        <td className="session-link">
                          {it.session_count > 1 ? (
                            <span className="muted"
                                  title="This iteration number holds calls from several sessions (a reused run id, or merged identical loops). Find them all in the Sessions tab.">
                              {it.session_count} sessions
                            </span>
                          ) : it.session_id
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
