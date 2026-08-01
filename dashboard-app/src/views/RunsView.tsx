import { useCallback, useEffect, useState } from "react";
import {
  FlaggedCall, flagBadgeClass, flagInfo, fmtAgo, fmtNum, fmtTime, fmtUsd, get,
  Iteration, liveUpdates, Run, runStatusInfo,
 listProjects,
} from "../api";
import CompareView from "./CompareView";
import { LabelEditor, matchesFilter, PinButton, pinnedFirst, ProjectFilter } from "./LabelBits";
import ProviderMark from "./ProviderMark";
import BatchReplay from "./BatchReplay";
import WhatIf from "./WhatIf";

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
  const [projects, setProjects] = useState<string[]>([]);
  const [projectFilter, setProjectFilter] = useState("");
  const [editing, setEditing] = useState<string | null>(null);

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
            </div>
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
              {detail.run_id}{" "}
              <span className={`badge ${detail.status}`} title={runStatusInfo(detail.status)}>{detail.status}</span>
            </h2>
            <div className="muted">
              started {fmtTime(detail.started_at)} · last call {fmtTime(detail.last_call_at)}
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
