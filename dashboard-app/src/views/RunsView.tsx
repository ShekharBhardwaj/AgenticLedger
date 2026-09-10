import { useCallback, useEffect, useRef, useState } from "react";
import { plural,
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

/** Spec 7.2: visible labels state what was OBSERVED, never more. */
const STATUS_LABEL: Record<string, string> = {
  running: "Running",
  flagged: "Flagged",
  complete: "Completion declared",
  ended: "Ended",
  stopped: "Calls blocked",
};

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

export default function RunsView({ onOpenSession, focusRun, onSelectedChange }: {
  onOpenSession: (s: string) => void;
  focusRun?: string | null;
  onSelectedChange?: (id: string | null) => void;
}) {
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
  // Premium spec 7.1: Overview | Activity | Cache, with What-if/Replay as
  // secondary tools rather than permanently expanded panels.
  const [subview, setSubview] = useState<"overview" | "activity" | "cache">("overview");
  const [showTools, setShowTools] = useState(false);
  useEffect(() => { setSubview("overview"); setShowTools(false); }, [selected]);
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
  // The wall's UI must be as honest as the wall: a save is "saving", then
  // the confirmed value or a visible error - never a silent failure that
  // leaves the user believing in a ceiling that does not exist.
  const [ceilingState, setCeilingState] = useState<"saving" | string | null>(null);
  const selectedRef = useRef<string | null>(null);
  useEffect(() => { selectedRef.current = selected; setFeed([]); }, [selected]);

  useEffect(() => { setConfirmStop(false); setCopied(false); }, [selected]);
  // The URL owns the selection: a run id in the hash lands here, and
  // Back to a bare #/runs clears the detail (premium spec, section 5).
  // The URL owns selection. A prop-driven change (deep link, tab return)
  // must NOT echo back to the router, or the initial null on remount
  // clobbers the restored run out of the URL before the prop syncs in.
  const propDriven = useRef(true);
  useEffect(() => { propDriven.current = true; setSelected(focusRun ?? null); }, [focusRun]);
  useEffect(() => {
    if (propDriven.current) { propDriven.current = false; return; }
    onSelectedChange?.(selected);
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [selected]);
  interface CacheAudit {
    verdict: string; reason: string; fix: string | null;
    received_usd: number;
    eligible: { estimated_usd: number; estimated_tokens: number;
                occurrences: number; prompt_chars: number; method: string } | null;
  }
  const [audit, setAudit] = useState<CacheAudit | null>(null);

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
    get<CacheAudit>(`/api/runs/${encodeURIComponent(selected)}/cache-audit`)
      .then(setAudit).catch(() => setAudit(null));
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
            <div className="card-title" title={r.run_id}>
              <span className="card-name">{r.label ?? r.run_id}</span>
            </div>
            <div className="card-meta-row">
              <span className={`badge ${r.status}`} title={runStatusInfo(r.status)}>{STATUS_LABEL[r.status] ?? r.status}</span>
              <RunMascot status={r.status} small />
              <span className="card-cost">{fmtUsd(r.total_cost_usd)}</span>
            </div>
            {r.label && r.label !== r.run_id && <div className="card-id mono">{r.run_id}</div>}
            {editing === r.run_id && (
              <LabelEditor scope="run" refId={r.run_id}
                           label={r.label} project={r.project} projects={projects}
                           onSaved={refresh} onClose={() => setEditing(null)} />
            )}
            <div className="card-sub">
              <span>{fmtAgo(r.last_call_at)}</span>
              <span>{plural(r.iterations, "iteration")}</span>
              <span>{plural(r.call_count, "call")}</span>
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
        ) : !detail && compare.length === 1 ? (
          <div className="empty">
            Pick a second run with <span className="mono">⇆</span> to compare.
          </div>
        ) : !detail && error ? (
          <div className="landing">
            <div className="section-title">Could not load runs</div>
            <div className="landing-quiet">{error}</div>
            <button className="link-btn" onClick={refresh}>Retry</button>
          </div>
        ) : !detail && runs.length > 0 ? (
          <div className="landing">
            {(() => {
              const concerns = runs.filter((r) => r.status === "flagged" || r.status === "stopped");
              const active = runs.filter((r) => r.status === "running");
              const recent = runs.filter((r) => !concerns.includes(r) && !active.includes(r)).slice(0, 6);
              const spend = runs.reduce((a, r) => a + (r.total_cost_usd || 0), 0);
              const calls = runs.reduce((a, r) => a + (r.call_count || 0), 0);
              const attentionRow = (r: Run, what: string) => (
                <div key={r.run_id} className="landing-row" role="button" tabIndex={0}
                     onClick={() => setSelected(r.run_id)}
                     onKeyDown={(e) => { if (e.key === "Enter") setSelected(r.run_id); }}>
                  <span className={`badge ${r.status}`}>{STATUS_LABEL[r.status] ?? r.status}</span>
                  <span className="landing-what">{what}</span>
                  <span className="card-name landing-run">{r.label ?? r.run_id}</span>
                  <span className="dim landing-when">{fmtAgo(r.last_call_at)}</span>
                  <span className="landing-inspect">Inspect →</span>
                </div>
              );
              return (
                <>
                  <div className="landing-summary">
                    <div className="ls-metric">
                      <div className="ls-value mono">{fmtUsd(spend)}</div>
                      <div className="ls-label">recorded spend</div>
                    </div>
                    <div className="ls-metric">
                      <div className="ls-value mono">{fmtNum(calls)}</div>
                      <div className="ls-label">recorded calls</div>
                    </div>
                    <div className="ls-metric">
                      <div className="ls-value mono" style={{ color: concerns.length ? "var(--amber)" : undefined }}>
                        {concerns.length}
                      </div>
                      <div className="ls-label">need attention</div>
                    </div>
                  </div>

                  <div className="section-title">Needs attention</div>
                  {concerns.length === 0
                    ? <div className="landing-quiet">No recorded concerns in the loaded runs.</div>
                    : concerns.map((r) => attentionRow(r, r.status === "flagged"
                        ? plural(r.flagged_calls, "flagged call") : "calls blocked"))}

                  {active.length > 0 && (
                    <>
                      <div className="section-title">Active now</div>
                      <table className="landing-table">
                        <tbody>
                          {active.map((r) => (
                            <tr key={r.run_id} className="landing-trow" onClick={() => setSelected(r.run_id)}>
                              <td className="lt-name">{r.label ?? r.run_id}</td>
                              <td><span className={`badge ${r.status}`}>{STATUS_LABEL[r.status] ?? r.status}</span></td>
                              <td className="num mono">{fmtUsd(r.total_cost_usd)}</td>
                              <td className="num mono">{fmtNum(r.call_count)}</td>
                              <td className="lt-when dim">{fmtAgo(r.last_call_at)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  )}

                  {recent.length > 0 && (
                    <>
                      <div className="section-title">Recent runs</div>
                      <table className="landing-table">
                        <thead>
                          <tr><th>run</th><th>observed state</th><th className="num">recorded spend</th>
                              <th className="num">calls</th><th>last activity</th></tr>
                        </thead>
                        <tbody>
                          {recent.map((r) => (
                            <tr key={r.run_id} className="landing-trow" onClick={() => setSelected(r.run_id)}>
                              <td className="lt-name">{r.label ?? r.run_id}</td>
                              <td><span className={`badge ${r.status}`}>{STATUS_LABEL[r.status] ?? r.status}</span></td>
                              <td className="num mono">{fmtUsd(r.total_cost_usd)}</td>
                              <td className="num mono">{fmtNum(r.call_count)}</td>
                              <td className="lt-when dim">{fmtAgo(r.last_call_at)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  )}

                  <div className="landing-scope">
                    Across the {plural(runs.length, "most recent run")} this view loaded.
                    For spend by day, model, and project, open <b>Reports</b>.
                  </div>
                </>
              );
            })()}
          </div>
        ) : !detail ? (
          <div className="landing landing-first">
            <div className="section-title">Waiting for the first call</div>
            <div className="landing-quiet">
              Point an agent at this ledger and its calls appear here as they happen:
            </div>
            <pre>export ANTHROPIC_BASE_URL=http://localhost:8000{"\n"}# or OPENAI_BASE_URL=http://localhost:8000/v1</pre>
            <div className="landing-quiet">
              Or wire a framework in one command: <code>agenticledger connect claude-code</code>
            </div>
          </div>
        ) : (
          <>
            <h2 className="page-title">
              {detail.label ?? detail.run_id}{" "}
              <span className={`badge ${detail.status}`} title={runStatusInfo(detail.status)}>{STATUS_LABEL[detail.status] ?? detail.status}</span>
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

            {(() => {
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
                setCeilingState("saving");
                setCeilingEdit(null);
                setLabel("run", detail.run_id, { budget_usd: v })
                  .then(() => { setCeilingState(null); refresh(); })
                  .catch((e) => setCeilingState(
                    `ceiling NOT saved: ${e?.message || "request failed"} - the wall is unchanged`));
              };
              const submitCeiling = () => {
                const v = parseFloat(ceilingEdit ?? "");
                if (Number.isNaN(v) || !Number.isFinite(v) || v <= 0 || v > 1_000_000) {
                  setCeilingState("a ceiling is a dollar amount above zero, at most 1,000,000");
                  return;
                }
                saveCeiling(v);
              };
              return (
                <div className="metric-strip">
                  <div className="metric">
                    <div className="metric-label">Recorded spend</div>
                    <div className="metric-primary mono">{fmtUsd(spent)}</div>
                    {liveNow && burn > 0 && (
                      <div className="metric-sub"
                           title="the last hour's recorded spend projected forward unchanged; not a predicted invoice">
                        At the recent pace: {fmtUsd(projected)} by{" "}
                        {morning.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
                      </div>
                    )}
                  </div>
                  <div className="metric">
                    <div className="metric-label">Run ceiling</div>
                    {ceiling ? (
                      <>
                        <div className="metric-secondary mono">{fmtUsd(ceiling)}</div>
                        <span className={`meter-track ${frac >= 1 ? "at" : frac >= 0.8 ? "near" : ""}`}
                              title={`recorded spend over the configured ceiling; an accounting display, not proof of remaining admission`}>
                          <span className="meter-fill" style={{ width: `${frac * 100}%` }} />
                        </span>
                        <div className="metric-sub">
                          <button className="link-btn" onClick={() => setCeilingEdit(String(ceiling))}>Edit</button>
                          <button className="link-btn" title="Remove run ceiling: this run's calls stop being refused by it. Other agent/team/daily limits still apply."
                                  onClick={() => saveCeiling(0)}>Remove</button>
                        </div>
                      </>
                    ) : ceilingEdit === null ? (
                      <>
                        <div className="metric-secondary muted">No run ceiling</div>
                        <div className="metric-sub">
                          <button className="link-btn"
                                  title="refuse this run's calls at the proxy once its spend reaches a dollar amount; survives restarts"
                                  onClick={() => setCeilingEdit("")}>Set ceiling</button>
                        </div>
                      </>
                    ) : null}
                    {ceilingEdit !== null && (
                      <span className="key-actions">
                        <label className="sr-only" htmlFor="ceiling-usd">Run ceiling in US dollars</label>
                        <input id="ceiling-usd" autoFocus className="ceiling-input"
                               inputMode="decimal" placeholder="USD"
                               value={ceilingEdit}
                               disabled={ceilingState === "saving"}
                               onChange={(e) => setCeilingEdit(e.target.value)}
                               onKeyDown={(e) => {
                                 if (e.key === "Enter") submitCeiling();
                                 if (e.key === "Escape") setCeilingEdit(null);
                               }} />
                        <button className="link-btn" disabled={ceilingState === "saving"}
                                onClick={submitCeiling}>Save</button>
                        <button className="link-btn" onClick={() => setCeilingEdit(null)}>Cancel</button>
                      </span>
                    )}
                    {ceilingState === "saving" && <div className="metric-sub">saving…</div>}
                    {ceilingState && ceilingState !== "saving" && (
                      <div className="ceiling-error">{ceilingState}</div>
                    )}
                    {ceiling !== null && ceiling < spent && (
                      <div className="metric-sub" style={{ color: "var(--amber)" }}>
                        below recorded spend: future requests may be refused
                      </div>
                    )}
                  </div>
                  <div className="metric">
                    <div className="metric-label">Model calls</div>
                    <div className="metric-secondary mono">{detail.call_count}</div>
                    <div className="metric-sub">
                      {plural(detail.iterations, "iteration")}
                      {detail.flagged_calls ? (
                        <span style={{ color: "var(--amber)" }}> · {detail.flagged_calls} flagged</span>
                      ) : null}
                    </div>
                  </div>
                </div>
              );
            })()}
            <div className="metric-tokens muted mono">
              {fmtNum(detail.total_tokens_in)} tokens in · {fmtNum(detail.total_tokens_out)} tokens out
            </div>

            <div className="subview-row">
              <div role="tablist" aria-label="Run views" className="subtabs">
                {(["overview", "activity", "cache"] as const).map((v) => (
                  <button key={v} role="tab" aria-selected={subview === v}
                          className={`subtab ${subview === v ? "active" : ""}`}
                          onClick={() => setSubview(v)}>
                    {v === "overview" ? "Overview" : v === "activity" ? "Activity" : "Cache"}
                  </button>
                ))}
              </div>
              <span className="spacer" />
              <button className="link-btn" aria-expanded={showTools}
                      onClick={() => setShowTools(!showTools)}>
                What-if / Replay {showTools ? "▴" : "▾"}
              </button>
            </div>

            {subview === "overview" && flags.length > 0 && (
              <div className="concern-band" role="note">
                <div className="concern-text">
                  <div className="concern-title">
                    Recorded concern: {(() => {
                      try { return (JSON.parse(flags[0].loop_flags) as string[]).join(", "); }
                      catch { return "loop flag"; }
                    })()}
                  </div>
                  <div className="concern-sub">
                    {plural(flags.length, "flagged call")} on record
                    {flags[0].iteration != null ? ` · latest in iteration ${flags[0].iteration}` : ""}
                  </div>
                </div>
                {flags[0].session_id && (
                  <button className="inspect-btn"
                          onClick={() => onOpenSession(flags[0].session_id!)}>
                    Inspect
                  </button>
                )}
              </div>
            )}

            {subview === "cache" && (
              !audit ? (
                <div className="empty">The cache audit did not load. <button className="link-btn" onClick={refresh}>Retry</button></div>
              ) : (
              <div className={`cache-panel ${audit.verdict}`}>
                <div className="cache-headline">
                  {audit.verdict === "never_requested" && audit.eligible ? (
                    <>~{fmtUsd(audit.eligible.estimated_usd)} of repeat-discount missed
                      <span className="est-mark" title={audit.eligible.method}> Estimate</span></>
                  ) : audit.verdict === "partially_cached" ? (
                    <>Partially cached{audit.eligible ? <> · ~{fmtUsd(audit.eligible.estimated_usd)} still missed
                      <span className="est-mark" title={audit.eligible.method}> Estimate</span></> : null}</>
                  ) : audit.verdict === "unstable_opening" ? (
                    <>Cache discount missed: the opening changes between calls</>
                  ) : audit.verdict === "well_cached" ? (
                    <>Nothing missed</>
                  ) : audit.verdict === "too_short" ? (
                    <>Nothing to fix</>
                  ) : (
                    <>Cache audit unavailable</>
                  )}
                </div>
                <div className="cache-line">{audit.reason}.</div>
                {audit.fix && <div className="cache-line">Fix: {audit.fix}.</div>}
                {audit.received_usd > 0 && (
                  <div className="cache-line muted"
                       title="exact, from provider-reported cache reads; the discount received versus full input price. Not net savings after cache-write charges.">
                    Cache-read discount received: {fmtUsd(audit.received_usd)}
                  </div>
                )}
                {audit.eligible && (
                  <details className="cache-method">
                    <summary>How the estimate is computed</summary>
                    <div className="muted">{audit.eligible.method}. An identical
                      {" "}{audit.eligible.prompt_chars.toLocaleString()}-character prompt,
                      {" "}{audit.eligible.occurrences} occurrences,
                      ~{audit.eligible.estimated_tokens.toLocaleString()} tokens.</div>
                  </details>
                )}
              </div>
              )
            )}

            {subview === "activity" && (
              <>
                <div className="section-title"
                     title="the live event buffer: every capture the proxy announced while this view was open. Not the complete persisted history.">
                  Activity · arrivals since this view opened
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

            {showTools && (
              <>
                <WhatIf params={`run_id=${encodeURIComponent(detail.run_id)}`} />
                <BatchReplay scope="run" refId={detail.run_id} onOpenSession={onOpenSession} />
              </>
            )}

            {subview === "overview" && iterations.length > 0 && (
              <>
                <div className="section-title">Cost per iteration</div>
                <div className="iter-scale">tallest bar = {fmtUsd(maxCost)}/iteration</div>
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
                              {plural(it.session_count, "session")}
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
