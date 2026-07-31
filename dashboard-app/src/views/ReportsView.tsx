import { useCallback, useEffect, useState } from "react";
import { fmtNum, fmtUsd, get, liveUpdates } from "../api";

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
        title="calls the ledger refused on purpose (over budget) — not failures">
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
  const [days, setDays] = useState(30);

  // Bucket days in the viewer's local timezone (JS offset is minutes behind
  // UTC, the API wants minutes ahead — hence the negation).
  const tzOffset = -new Date().getTimezoneOffset();
  const refresh = useCallback(() => {
    get<Report>(`/api/reports?days=${days}&tz_offset_minutes=${tzOffset}`)
      .then(setReport).catch(() => {});
  }, [days, tzOffset]);

  useEffect(() => {
    refresh();
    return liveUpdates(refresh);
  }, [refresh]);

  if (!report) return <div className="main"><div className="empty">Loading…</div></div>;

  const t = report.totals;
  const maxDay = Math.max(...report.daily.map((d) => d.cost_usd || 0), 0.000001);
  const cacheUsed = t.cache_read_tokens > 0 || t.cache_write_tokens > 0;

  return (
    <div className="main reports">
      <div className="seg">
        {WINDOWS.map((w) => (
          <button key={w} className={days === w ? "active" : ""} onClick={() => setDays(w)}>
            {w}d
          </button>
        ))}
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
                    title="refused by the ledger's budget walls — not failures"> ({t.blocked_calls} blocked)</span>
            )}
          </div>
          <div className="l">calls</div>
        </div>
        <div className="stat">
          <div className="v">{fmtNum(t.tokens_in)} / {fmtNum(t.tokens_out)}</div>
          <div className="l">tokens in / out</div>
        </div>
        {cacheUsed && (
          <div className="stat" title="What your prompt-cache traffic would have cost at full input rates minus what it actually cost. Negative (red) means heavy cache writes were never read back — caching cost more than it saved.">
            <div className="v" style={{ color: t.cache_savings_usd >= 0 ? "var(--green)" : "var(--red)" }}>
              {t.cache_savings_usd >= 0 ? "" : "−"}{fmtUsd(Math.abs(t.cache_savings_usd))}
            </div>
            <div className="l">{t.cache_savings_usd >= 0 ? "cache saved" : "cache cost extra"}</div>
          </div>
        )}
      </div>

      {report.daily.length > 0 && (
        <>
          <div className="section-title">
            Spend per day ({tzOffset === 0 ? "UTC" : "your local time"})
          </div>
          <div className="ribbon">
            {report.daily.map((d) => (
              <div
                key={d.day}
                className={`bar ${d.error_calls ? "errored" : ""}`}
                style={{ height: `${Math.max((100 * (d.cost_usd || 0)) / maxDay, 3)}%` }}
                title={`${d.day}: ${fmtUsd(d.cost_usd)}, ${d.call_count} calls`}
              />
            ))}
          </div>
          <div className="ribbon-labels">
            {report.daily.map((d, i) => (
              <div key={d.day}>{fmtDayLabel(d.day, i === 0 ? "" : report.daily[i - 1].day)}</div>
            ))}
          </div>
        </>
      )}

      <div className="section-title">By model</div>
      <table className="rtable">
        <thead>
          <tr>
            <th>model</th><th>calls</th><th>errors</th>
            <th title="refused by the ledger on purpose (budget walls) — not failures">blocked</th>
            <th>latency p50/p95/p99</th>
            <th>tokens in / out</th>
            <th title="prompt-cache tokens: reads are billed at a fraction of the input rate, writes at a premium">cache r / w</th>
            <th title="effect of caching on your bill: minus = money saved vs paying full input rates, plus = caching cost extra">cache Δ</th>
            <th>cost</th>
          </tr>
        </thead>
        <tbody>
          {report.models.map((m) => (
            <tr key={`${m.model_id}|${m.provider}`}>
              <td className="mono">{m.model_id}</td>
              <td>{fmtNum(m.call_count)}</td>
              <ErrorCell n={m.error_calls} />
              <BlockedCell n={m.blocked_calls} />
              <LatencyCell r={m} />
              <td>{fmtNum(m.tokens_in)} / {fmtNum(m.tokens_out)}</td>
              <td>{fmtNum(m.cache_read_tokens)} / {fmtNum(m.cache_write_tokens)}</td>
              <td style={{ color: m.cache_savings_usd > 0 ? "var(--green)" : m.cache_savings_usd < 0 ? "var(--red)" : undefined }}>
                {m.cache_savings_usd ? `${m.cache_savings_usd > 0 ? "−" : "+"}${fmtUsd(Math.abs(m.cache_savings_usd))}` : "—"}
              </td>
              <td>{fmtUsd(m.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="muted" style={{ fontSize: 12, margin: "-12px 0 20px" }}>
        Cache Δ = what cached traffic would have cost at full input rates minus
        what it actually cost. Minus is savings; plus means cache writes
        outweighed the reads.
      </div>

      {report.teams.length > 0 && (
        <>
          <div className="section-title">By team</div>
          <table className="rtable">
            <thead>
              <tr>
                <th>team</th><th>calls</th><th>errors</th>
                <th title="refused by the ledger on purpose (budget walls) — not failures">blocked</th>
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

      <div className="section-title">By agent</div>
      <table className="rtable">
        <thead>
          <tr>
            <th>agent</th><th>calls</th><th>errors</th>
            <th title="refused by the ledger on purpose (budget walls) — not failures">blocked</th>
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
