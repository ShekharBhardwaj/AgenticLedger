import { useEffect, useState } from "react";
import { get } from "../api";

interface SettingRow { section: string; label: string; value: string; source: string; }

/** #50 — the oven window: what the proxy is actually running with.
 *  Read-only; secrets arrive pre-masked from the server. */
export default function SettingsView() {
  const [rows, setRows] = useState<SettingRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    get<{ rows: SettingRow[] }>("/api/settings")
      .then((r) => setRows(r.rows))
      .catch((e) => setError(e.message));
  }, []);

  if (error) {
    return (
      <div className="reports">
        <div className="empty">
          Settings need an admin key — set one in the ⚿ panel. ({error})
        </div>
      </div>
    );
  }
  if (!rows) return <div className="reports"><div className="empty">Loading…</div></div>;

  const sections = [...new Set(rows.map((r) => r.section))];
  return (
    <div className="reports">
      <h2 className="page-title">Settings</h2>
      <div className="muted" style={{ marginBottom: 8 }}>
        What the proxy is running with — read-only, secrets hidden.
        Source: <b>file</b> = agenticledger.toml · <b>env</b> = typed or
        exported (always wins) · <b>default</b> = built-in.
      </div>
      {sections.map((sec) => (
        <div key={sec}>
          <div className="section-title">{sec}</div>
          <table className="rtable">
            <thead><tr><th>setting</th><th>value</th><th>source</th></tr></thead>
            <tbody>
              {rows.filter((r) => r.section === sec).map((r) => (
                <tr key={r.label}>
                  <td>{r.label}</td>
                  <td className="mono">{r.value}</td>
                  <td><span className={`badge src-${r.source}`}>{r.source}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
