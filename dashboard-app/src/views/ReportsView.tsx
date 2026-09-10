import React from "react";
import { useCallback, useEffect, useState } from "react";
import { downloadReportsCsv, fmtNum, fmtUsd, get, listProjects, liveUpdates, Run } from "../api";
import { RUN_PREFIX } from "./LabelBits";
import ProviderMark from "./ProviderMark";

interface DailyRow {
  day: string;
  call_count: number;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  error_calls: number;
  blocked_calls: number;
}

interface ModelRow {
  model_id: string;
  provider: string | null;
  call_count: number;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cache_savings_usd: number;
  error_calls: number;
  blocked_calls: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  p99_latency_ms: number | null;
}

interface TeamRow {
  team: string;
  call_count: number;
  cost_usd: number;
  session_count: number;
  error_count: number;
  blocked_count: number;
  budget_daily?: number;
  spent_today?: number;
  over_budget?: boolean;
}

interface ProjectRow {
  project: string;
  call_count: number;
  session_count: number;
  cost_usd: number;
  error_count: number;
  blocked_count: number;
}

interface AgentRow {
  agent_name: string;
  call_count: number;
  cost_usd: number;
  session_count: number;
  error_calls: number;
  blocked_calls: number;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  p99_latency_ms: number | null;
}

const fmtMs = (v: number | null | undefined) =>
  v == null ? "—" : v >= 10_000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`;

function LatencyCell({ r }: { r: { p50_latency_ms: number | null; p95_latency_ms: number | null; p99_latency_ms: number | null } }) {
  return (
    <td title="latency p50 / p95 / p99">
      {fmtMs(r.p50_latency_ms)} / {fmtMs(r.p95_latency_ms)} / {fmtMs(r.p99_latency_ms)}
    </td>
  );
}

function ErrorCell({ n }: { n: number }) {
  return <td style={{ color: n ? "var(--red)" : undefined }}>{n || "—"}</td>;
}

/** The ledger's own refusals (budget walls) — amber, not red: enforcement
 *  working is not the agent failing. */
function BlockedCell({ n }: { n: number }) {
  return (
    <td style={{ color: n ? "var(--amber)" : undefined }}
        title="calls the ledger refused on purpose (over budget), not failures">
      {n || "—"}
    </td>
  );
}

interface Report {
  days: number;
  totals: {
    total_cost_usd: number;
    call_count: number;
    error_calls: number;
    blocked_calls: number;
    tokens_in: number;
    tokens_out: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    cache_savings_usd: number;
  };
  daily: DailyRow[];
  models: ModelRow[];
  agents: AgentRow[];
  teams: TeamRow[];
  projects: ProjectRow[];
}

function LatencyText({ r }: { r: { p50_latency_ms: number | null; p95_latency_ms: number | null; p99_latency_ms: number | null } }) {
  const f = (v: number | null) => (v == null ? "—" : `${Math.round(v)}ms`);
  return <span className="mono">{f(r.p50_latency_ms)} / {f(r.p95_latency_ms)} / {f(r.p99_latency_ms)}</span>;
}

const WINDOWS = [7, 30, 90];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Bar label: "Jul 29" when the month is new or ambiguous, bare day otherwise. */
function fmtDayLabel(day: string, prevDay: string): string {
  const month = MONTHS[parseInt(day.slice(5, 7), 10) - 1] ?? "";
  const dom = String(parseInt(day.slice(8), 10));
  return prevDay.slice(0, 7) === day.slice(0, 7) ? dom : `${month} ${dom}`;
}

export default function ReportsView() {
  const [report, setReport] = useState<Report | null>(null);
  const [expandedModel, setExpandedModel] = React.useState<string | null>(null);
  const [days, setDays] = useState(30);
  // #107 — scope every number to one project (or a run-default group),
  // same vocabulary as the Sessions and Loop Lens dropdowns.
  const [project, setProject] = useState("");
  const [projects, setProjects] = useState<string[]>([]);
  const [runGroups, setRunGroups] = useState<string[]>([]);

  // Bucket days in the viewer's local timezone (JS offset is minutes behind
  // UTC, the API wants minutes ahead — hence the negation).
  const tzOffset = -new Date().getTimezoneOffset();
  const refresh = useCallback(() => {
    get<Report>(`/api/reports?days=${days}&tz_offset_minutes=${tzOffset}`
                + (project ? `&project=${encodeURIComponent(project)}` : ""))
      .then(setReport).catch(() => {});
    listProjects().then((r) => setProjects(r.projects)).catch(() => {});
    get<Run[]>("/api/runs")
      .then((runs) => setRunGroups(runs.filter((r) => !r.project).map((r) => r.run_id)))
      .catch(() => {});
  }, [days, tzOffset, project]);

  useEffect(() => {
    refresh();
    return liveUpdates(refresh);
  }, [refresh]);

  if (!report) return <div className="main"><div className="empty">Loading…</div></div>;

  const t = report.totals;
  // Spec 9: the axis is continuous. Zero-fill missing dates only because a
  // successful response established the window; past 31 buckets, aggregate
  // to labeled calendar weeks whose totals equal the daily totals.
  const chartBuckets = (() => {
    if (report.daily.length === 0) return [] as { day: string; cost_usd: number; call_count: number; error_calls: number; span?: string }[];
    const byDay = new Map(report.daily.map((d) => [d.day, d]));
    const first = report.daily[0].day;
    const last = report.daily[report.daily.length - 1].day;
    const days: { day: string; cost_usd: number; call_count: number; error_calls: number; span?: string }[] = [];
    const cur = new Date(first + "T00:00:00Z");
    const stop = new Date(last + "T00:00:00Z");
    while (cur <= stop) {
      const key = cur.toISOString().slice(0, 10);
      const d = byDay.get(key);
      days.push(d ? { day: d.day, cost_usd: d.cost_usd || 0, call_count: d.call_count, error_calls: d.error_calls }
                  : { day: key, cost_usd: 0, call_count: 0, error_calls: 0 });
      cur.setUTCDate(cur.getUTCDate() + 1);
    }
    if (days.length <= 31) return days;
    const weeks: { day: string; cost_usd: number; call_count: number; error_calls: number; span?: string }[] = [];
    for (const d of days) {
      const dt = new Date(d.day + "T00:00:00Z");
      const monday = new Date(dt);
      monday.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay() + 6) % 7));
      const wk = monday.toISOString().slice(0, 10);
      const bucket = weeks.find((w) => w.day === wk);
      if (bucket) {
        bucket.cost_usd += d.cost_usd; bucket.call_count += d.call_count;
        bucket.error_calls += d.error_calls; bucket.span = `${bucket.span!.split(" – ")[0]} – ${d.day}`;
      } else {
        weeks.push({ ...d, day: wk, span: `${d.day} – ${d.day}` });
      }
    }
    return weeks;
  })();
  const weekly = report.daily.length > 31;
  const maxDay = Math.max(...chartBuckets.map((d) => d.cost_usd || 0), 0.000001);
  const cacheUsed = t.cache_read_tokens > 0 || t.cache_write_tokens > 0;

  return (
    <div className="main reports">
      <div className="seg reports-seg">
        {WINDOWS.map((w) => (
          <button key={w} className={days === w ? "active" : ""} onClick={() => setDays(w)}>
            {w}d
          </button>
        ))}
        {(projects.length > 0 || runGroups.length > 0) && (
          <select
            className="project-filter reports-project"
            value={project}
            onChange={(e) => setProject(e.target.value)}
            title="Scope every number on this page to one project or run"
          >
            <option value="">all projects</option>
            {projects.map((p) => <option key={p} value={p}>{p}</option>)}
            {runGroups.length > 0 && (
              <optgroup label="runs (unfiled work groups under its run)">
                {runGroups.map((r) => (
                  <option key={r} value={`${RUN_PREFIX}${r}`}>{r}</option>
                ))}
              </optgroup>
            )}
          </select>
        )}
      </div>

      <div className="stats-row">
        <div className="stat">
          <div className="v">{fmtUsd(t.total_cost_usd)}</div>
          <div className="l">spend · {report.days}d</div>
        </div>
        <div className="stat">
          <div className="v">
            {fmtNum(t.call_count)}
            {t.error_calls > 0 && (
              <span style={{ color: "var(--red)", fontSize: 13 }}> ({t.error_calls} err)</span>
            )}
            {t.blocked_calls > 0 && (
              <span style={{ color: "var(--amber)", fontSize: 13 }}
                    title="refused by the ledger's budget walls, not failures"> ({t.blocked_calls} blocked)</span>
            )}
          </div>
          <div className="l">calls</div>
        </div>
        <div className="stat">
          <div className="v">{fmtNum(t.tokens_in)} / {fmtNum(t.tokens_out)}</div>
          <div className="l">tokens in / out</div>
        </div>
        {cacheUsed && (
          <div className="stat" title="What your prompt-cache traffic would have cost at full input rates minus what it actually cost. Negative (red) means heavy cache writes were never read back: caching cost more than it saved.">
            <div className="v" style={{ color: t.cache_savings_usd >= 0 ? "var(--green)" : "var(--red)" }}>
              {t.cache_savings_usd >= 0 ? "" : "−"}{fmtUsd(Math.abs(t.cache_savings_usd))}
            </div>
            <div className="l">{t.cache_savings_usd >= 0 ? "cache saved" : "cache cost extra"}</div>
          </div>
        )}
      </div>

      {report.daily.length > 0 && (
        <>
          <div className="section-title"
               title="a continuous axis: days without spend show as zero; a red dot marks errors">
            {weekly ? "Spend per week" : "Spend per day"} ({tzOffset === 0 ? "UTC" : "your local time"})
          </div>
          <div className="ribbon-scale">tallest bar = {fmtUsd(maxDay)}{weekly ? "/week" : "/day"}</div>
          <div className="ribbon" role="img"
               aria-label={`${weekly ? "Weekly" : "Daily"} recorded spend, tallest ${fmtUsd(maxDay)}`}>
            {chartBuckets.map((d) => (
              <div
                key={d.day}
                className={`bar ${d.error_calls ? "errored" : ""}`}
                style={{ height: `${Math.max((100 * (d.cost_usd || 0)) / maxDay, d.call_count ? 3 : 1)}%` }}
                title={`${d.span ?? d.day}: ${fmtUsd(d.cost_usd)}, ${d.call_count} calls`
                       + (d.error_calls ? ` (${d.error_calls} errored)` : "")}
              />
            ))}
          </div>
          <div className="ribbon-labels">
            {chartBuckets.map((d, i) => (
              <div key={d.day}>{fmtDayLabel(d.day, i === 0 ? "" : chartBuckets[i - 1].day)}</div>
            ))}
          </div>
          <details className="chart-table">
            <summary>Values as text</summary>
            <table className="rtable"><tbody>
              {chartBuckets.filter((d) => d.call_count > 0).map((d) => (
                <tr key={d.day}><td className="mono">{d.span ?? d.day}</td>
                  <td className="num">{fmtUsd(d.cost_usd)}</td>
                  <td className="num">{fmtNum(d.call_count)} calls</td>
                  <td className="num">{d.error_calls ? `${d.error_calls} errored` : ""}</td></tr>
              ))}
            </tbody></table>
          </details>
        </>
      )}

      <div className="section-title" style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
        <span>By model</span>
        <button
          onClick={() => downloadReportsCsv(days, tzOffset, project).catch(() => {})}
          className="muted link-btn"
          style={{ fontSize: 12, marginTop: 0, padding: "2px 8px" }}
          title="Download model spend as CSV"
        >
          ↓ CSV
        </button>
      </div>
      {(() => {
        const denom = report.models.reduce((a, m) => a + (m.cost_usd || 0), 0);
        return (
          <table className="rtable money-first">
            <thead>
              <tr>
                <th>model</th>
                <th className="num">recorded spend</th>
                <th className="num" title="share of this report's recorded spend">share</th>
                <th className="num">failures</th>
                <th className="num" title="refused by the ledger on purpose (budget walls), not failures">refusals</th>
                <th aria-label="details" />
              </tr>
            </thead>
            <tbody>
              {report.models.map((m) => {
                const key = `${m.model_id}|${m.provider}`;
                const open = expandedModel === key;
                return (
                  <React.Fragment key={key}>
                    <tr className="model-row" onClick={() => setExpandedModel(open ? null : key)}>
                      <td className="mono model-cell">
                        <ProviderMark provider={m.provider} model={m.model_id} />
                        {m.model_id}
                      </td>
                      <td className="num">{fmtUsd(m.cost_usd)}</td>
                      <td className="num">{denom > 0 ? `${(100 * (m.cost_usd || 0) / denom).toFixed(1)}%` : "unavailable"}</td>
                      <ErrorCell n={m.error_calls} />
                      <BlockedCell n={m.blocked_calls} />
                      <td className="num expand-cell">{open ? "▴" : "▾"}</td>
                    </tr>
                    {open && (
                      <tr className="model-detail">
                        <td colSpan={6}>
                          <span className="mono">{fmtNum(m.call_count)} calls</span>
                          {" · "}latency p50/p95/p99: <LatencyText r={m} />
                          {" · "}tokens {fmtNum(m.tokens_in)} in / {fmtNum(m.tokens_out)} out
                          {" · "}cache {fmtNum(m.cache_read_tokens)} read / {fmtNum(m.cache_write_tokens)} written
                          {" · "}<span title="what cached traffic would have cost at full input rates minus what it actually cost, against that baseline; can validly exceed spend"
                                  style={{ color: m.cache_savings_usd > 0 ? "var(--green)" : m.cache_savings_usd < 0 ? "var(--red)" : undefined }}>
                            {m.cache_savings_usd > 0 ? `net cache savings ${fmtUsd(m.cache_savings_usd)}`
                              : m.cache_savings_usd < 0 ? `cache cost extra ${fmtUsd(Math.abs(m.cache_savings_usd))}`
                              : "no cache effect"}
                          </span>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        );
      })()}

      {report.teams.length > 0 && (
        <>
          <div className="section-title">By team</div>
          <table className="rtable">
            <thead>
              <tr>
                <th>team</th><th>calls</th><th>errors</th>
                <th title="refused by the ledger on purpose (budget walls), not failures">blocked</th>
                <th>sessions</th><th>cost</th>
                <th title="today's spend against the team card's daily allowance">allowance</th>
              </tr>
            </thead>
            <tbody>
              {report.teams.map((t2) => (
                <tr key={t2.team}>
                  <td className="mono">{t2.team}</td>
                  <td>{fmtNum(t2.call_count)}</td>
                  <ErrorCell n={t2.error_count} />
                  <BlockedCell n={t2.blocked_count} />
                  <td>{fmtNum(t2.session_count)}</td>
                  <td>{fmtUsd(t2.cost_usd)}</td>
                  <td>
                    {t2.budget_daily == null ? "—" : (
                      <span style={{ color: t2.over_budget ? "var(--amber)" : undefined }}>
                        {fmtUsd(t2.spent_today ?? 0)} / {fmtUsd(t2.budget_daily)} today
                        {t2.over_budget ? " · blocked" : ""}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {report.projects.length > 0 && (
        <>
          <div className="section-title">By project</div>
          <table className="rtable">
            <thead>
              <tr>
                <th>project</th><th>calls</th><th>errors</th>
                <th title="refused by the ledger on purpose (budget walls), not failures">blocked</th>
                <th>sessions</th><th>cost</th>
              </tr>
            </thead>
            <tbody>
              {report.projects.map((p) => (
                <tr key={p.project}>
                  <td>{p.project}</td>
                  <td>{fmtNum(p.call_count)}</td>
                  <ErrorCell n={p.error_count} />
                  <BlockedCell n={p.blocked_count} />
                  <td>{fmtNum(p.session_count)}</td>
                  <td>{fmtUsd(p.cost_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <div className="section-title">By agent</div>
      <table className="rtable">
        <thead>
          <tr>
            <th>agent</th><th>calls</th><th>errors</th>
            <th title="refused by the ledger on purpose (budget walls), not failures">blocked</th>
            <th>latency p50/p95/p99</th><th>sessions</th><th>cost</th>
          </tr>
        </thead>
        <tbody>
          {report.agents.map((a) => (
            <tr key={a.agent_name}>
              <td className="mono">{a.agent_name}</td>
              <td>{fmtNum(a.call_count)}</td>
              <ErrorCell n={a.error_calls} />
              <BlockedCell n={a.blocked_calls} />
              <LatencyCell r={a} />
              <td>{fmtNum(a.session_count)}</td>
              <td>{fmtUsd(a.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}