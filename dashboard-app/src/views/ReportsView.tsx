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
}

interface AgentRow {
  agent_name: string;
  call_count: number;
  cost_usd: number;
  session_count: number;
}

interface Report {
  days: number;
  totals: {
    total_cost_usd: number;
    call_count: number;
    error_calls: number;
    tokens_in: number;
    tokens_out: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    cache_savings_usd: number;
  };
  daily: DailyRow[];
  models: ModelRow[];
  agents: AgentRow[];
}

const WINDOWS = [7, 30, 90];

export default function ReportsView() {
  const [report, setReport] = useState<Report | null>(null);
  const [days, setDays] = useState(30);

  const refresh = useCallback(() => {
    get<Report>(`/api/reports?days=${days}`).then(setReport).catch(() => {});
  }, [days]);

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
          </div>
          <div className="l">calls</div>
        </div>
        <div className="stat">
          <div className="v">{fmtNum(t.tokens_in)} / {fmtNum(t.tokens_out)}</div>
          <div className="l">tokens in / out</div>
        </div>
        {cacheUsed && (
          <div className="stat">
            <div className="v" style={{ color: t.cache_savings_usd >= 0 ? "var(--green)" : "var(--red)" }}>
              {t.cache_savings_usd >= 0 ? "" : "−"}{fmtUsd(Math.abs(t.cache_savings_usd))}
            </div>
            <div className="l">{t.cache_savings_usd >= 0 ? "cache saved" : "cache cost extra"}</div>
          </div>
        )}
      </div>

      {report.daily.length > 0 && (
        <>
          <div className="section-title">Spend per day (UTC)</div>
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
            {report.daily.map((d) => (
              <div key={d.day}>{d.day.slice(8)}</div>
            ))}
          </div>
        </>
      )}

      <div className="section-title">By model</div>
      <table className="rtable">
        <thead>
          <tr>
            <th>model</th><th>calls</th><th>tokens in / out</th>
            <th>cache r / w</th><th>cache Δ</th><th>cost</th>
          </tr>
        </thead>
        <tbody>
          {report.models.map((m) => (
            <tr key={`${m.model_id}|${m.provider}`}>
              <td className="mono">{m.model_id}</td>
              <td>{fmtNum(m.call_count)}</td>
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

      <div className="section-title">By agent</div>
      <table className="rtable">
        <thead>
          <tr><th>agent</th><th>calls</th><th>sessions</th><th>cost</th></tr>
        </thead>
        <tbody>
          {report.agents.map((a) => (
            <tr key={a.agent_name}>
              <td className="mono">{a.agent_name}</td>
              <td>{fmtNum(a.call_count)}</td>
              <td>{fmtNum(a.session_count)}</td>
              <td>{fmtUsd(a.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
