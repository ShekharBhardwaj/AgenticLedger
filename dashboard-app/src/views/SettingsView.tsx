import { useEffect, useState } from "react";
import { get, post } from "../api";

interface SettingRow {
  section: string; label: string; value: string; source: string;
  means: string; set_with: string;
}

type ThemePref = "dark" | "light" | "system";

/** Appearance: a browser-local preference, clearly separate from the
 *  read-only proxy configuration below (premium-dashboard spec, 5/10).
 *  Applies immediately; resolved before paint on the next load by the
 *  inline script in index.html. */
function AppearancePicker() {
  const [pref, setPref] = useState<ThemePref>(() => {
    try { return (localStorage.getItem("agenticledger.ui.theme") as ThemePref) || "dark"; }
    catch { return "dark"; }
  });
  const apply = (next: ThemePref) => {
    setPref(next);
    try { localStorage.setItem("agenticledger.ui.theme", next); } catch { /* private mode */ }
    const dark = next === "dark" ||
      (next === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  };
  return (
    <div className="appearance-row">
      <span className="appearance-label">Appearance</span>
      <div role="radiogroup" aria-label="Appearance" className="appearance-options">
        {(["dark", "light", "system"] as ThemePref[]).map((opt) => (
          <button key={opt} role="radio" aria-checked={pref === opt}
                  className={`seg-btn ${pref === opt ? "active" : ""}`}
                  onClick={() => apply(opt)}>
            {opt === "dark" ? "Dark" : opt === "light" ? "Light" : "System"}
          </button>
        ))}
      </div>
      <span className="muted appearance-note">
        Stored in this browser only; it does not change proxy configuration.
      </span>
    </div>
  );
}

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
          Settings need an admin key. Set one in the ⚿ panel. ({error})
        </div>
      </div>
    );
  }
  if (!rows) return <div className="reports"><div className="empty">Loading…</div></div>;

  const sections = [...new Set(rows.map((r) => r.section))];
  return (
    <div className="reports">
      <h2 className="page-title">Settings</h2>
      <AppearancePicker />
      <div className="muted" style={{ marginBottom: 8, maxWidth: 760 }}>
        What the proxy is running with: read-only, secrets hidden. Each row
        says where its value came from: <b>file</b> = your agenticledger.toml ·{" "}
        <b>env</b> = typed or exported, which always wins · <b>default</b> =
        built-in. To change something, edit the config file and restart
        (<span className="mono">agenticledger stop &amp;&amp; agenticledger start</span>).
      </div>
      {sections.map((sec) => (
        <div key={sec}>
          <div className="section-title">{sec}</div>
          <table className="rtable">
            <thead><tr><th>setting</th><th>value</th><th>source</th></tr></thead>
            <tbody>
              {rows.filter((r) => r.section === sec).map((r) => (
                <tr key={r.label}>
                  <td className="setting-cell">
                    <div className="setting-name">{r.label}</div>
                    {r.means && <div className="setting-means">{r.means}</div>}
                    {r.set_with && (
                      <div className="setting-key" title="set it here">
                        set with: {r.set_with}
                      </div>
                    )}
                  </td>
                  <td className="mono">{r.value}</td>
                  <td><span className={`badge src-${r.source}`}>{r.source}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <Maintenance />
      <AuditTrail />
    </div>
  );
}

interface AuditRow {
  id: string; timestamp: string; actor_role: string | null; actor_source: string | null;
  actor: string | null; action: string; target: string | null; details: string | null;
  client: string | null; seq: number | null; prev_hash: string | null; row_hash: string | null;
}

interface ChainCheck {
  ok: boolean; checked: number; pre_chain: number; keyed: boolean;
  first_break: { seq: number | null; id: string; action?: string } | null;
}

type AuditFilters = { action: string; actor: string; target: string };
const AUDIT_PAGE = 50;

function fetchAuditPage(f: AuditFilters, beforeSeq?: number): Promise<AuditRow[]> {
  const q = new URLSearchParams({ limit: String(AUDIT_PAGE) });
  for (const [k, v] of Object.entries(f)) if (v.trim()) q.set(k, v.trim());
  if (beforeSeq != null) q.set("before_seq", String(beforeSeq));
  return get<AuditRow[]>(`/api/audit?${q.toString()}`);
}

/** The audit trail, read from the chained log: who did what, filtered and
 *  paged, with the chain check one click away. Admin only; a viewer sees a
 *  plain note instead of a broken table. */
function AuditTrail() {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<AuditFilters>({ action: "", actor: "", target: "" });
  const [applied, setApplied] = useState<AuditFilters>(filters);
  const [more, setMore] = useState(false);
  const [check, setCheck] = useState<ChainCheck | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    let alive = true;
    fetchAuditPage(applied)
      .then((page) => {
        if (!alive) return;
        setRows(page); setMore(page.length === AUDIT_PAGE); setError(null);
      })
      .catch((e) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [applied]);

  const loadOlder = () => {
    const seqs = (rows ?? []).map((r) => r.seq).filter((s): s is number => s != null);
    if (!seqs.length) return;
    fetchAuditPage(applied, Math.min(...seqs))
      .then((page) => {
        setRows((prev) => [...(prev ?? []), ...page]);
        setMore(page.length === AUDIT_PAGE);
      })
      .catch((e) => setError(e.message));
  };

  const verify = () => {
    setChecking(true); setCheck(null);
    get<ChainCheck>("/api/audit/verify")
      .then(setCheck)
      .catch((e) => setError(e.message))
      .finally(() => setChecking(false));
  };

  return (
    <section className="settings-section" aria-label="Audit trail">
      <div className="section-title">Audit trail</div>
      <div className="muted" style={{ maxWidth: 760, marginBottom: 8 }}>
        Who viewed, exported, deleted or changed what, plus failed logins. Rows
        are hash-chained; verifying walks the chain and names the first break.
        A keyed chain (AGENTICLEDGER_AUDIT_HMAC_KEY) resists a database writer
        without the key; a plain sha256 chain catches edits, not a determined
        writer with database access.
      </div>
      {error ? (
        <div className="empty">The audit trail needs an admin key. ({error})</div>
      ) : (
        <>
          <form className="key-actions"
                onSubmit={(e) => { e.preventDefault(); setApplied({ ...filters }); }}>
            <input className="search" aria-label="Filter by action"
                   placeholder="action, e.g. view_session" value={filters.action}
                   onChange={(e) => setFilters({ ...filters, action: e.target.value })} />
            <input className="search" aria-label="Filter by actor" placeholder="actor"
                   value={filters.actor}
                   onChange={(e) => setFilters({ ...filters, actor: e.target.value })} />
            <input className="search" aria-label="Filter by target" placeholder="target contains"
                   value={filters.target}
                   onChange={(e) => setFilters({ ...filters, target: e.target.value })} />
            <button className="link-btn" type="submit" style={{ whiteSpace: "nowrap" }}>
              Apply filters
            </button>
            <button className="link-btn" type="button" disabled={checking} onClick={verify}
                    style={{ whiteSpace: "nowrap" }}>
              {checking ? "verifying…" : "Verify chain"}
            </button>
          </form>
          {check && (
            <div role="status" className="muted" style={{ margin: "6px 0" }}>
              {check.ok
                ? `Chain intact: ${check.checked} rows verified` +
                  (check.pre_chain ? `, ${check.pre_chain} pre-chain` : "") +
                  ` (${check.keyed ? "keyed HMAC" : "plain sha256"}).`
                : `Chain broken at seq ${check.first_break?.seq ?? "?"}` +
                  ` (${check.first_break?.action ?? "row"} ${check.first_break?.id ?? ""});` +
                  ` ${check.checked} rows verified before it.`}
            </div>
          )}
          {!rows ? (
            <div className="empty">Loading…</div>
          ) : rows.length === 0 ? (
            <div className="empty">No audit rows match.</div>
          ) : (
            <table className="rtable">
              <thead>
                <tr><th>when</th><th>actor</th><th>action</th><th>target</th>
                    <th>details</th><th>client</th><th>seq</th></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className="mono">{new Date(r.timestamp).toLocaleString()}</td>
                    <td>
                      {r.actor || r.actor_source || "-"}
                      {r.actor_role && <span className="muted"> ({r.actor_role})</span>}
                    </td>
                    <td className="mono">{r.action}</td>
                    <td className="mono">{r.target || "-"}</td>
                    <td>{r.details || ""}</td>
                    <td className="mono">{r.client || "-"}</td>
                    <td className="mono" title={r.row_hash || ""}>{r.seq ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {more && rows && (
            <button className="link-btn" type="button" onClick={loadOlder}>Load older</button>
          )}
        </>
      )}
    </section>
  );
}

/** Actions, not settings: product-shipped data maintenance. */
function Maintenance() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  return (
    <div className="settings-section">
      <div className="section-title">Maintenance</div>
      <div className="muted" style={{ maxWidth: 640, marginBottom: 8 }}>
        When the ledger learns to recognize a new framework, calls captured
        before that knowledge show as unattributed. Re-running detection
        names them in place; attribution set at capture time is never
        changed.
      </div>
      <div className="key-actions">
        <button className="link-btn" disabled={busy}
                onClick={() => {
                  setBusy(true); setResult(null);
                  post<{
                    examined: number;
                    updated: number;
                    by_framework: Record<string, number>;
                  }>("/api/redetect", {})
                    .then((r) => {
                      const breakdown = Object.entries(r.by_framework)
                        .map(([framework, count]) => `${framework}: ${count}`)
                        .join(", ");
                      setResult(
                        r.updated
                          ? `${r.updated} of ${r.examined} unattributed calls newly named` +
                            `${breakdown ? ` (${breakdown})` : ""}.`
                          : `Nothing to do: ${r.examined} unattributed calls, none newly recognizable.`,
                      );
                    })
                    .catch((e) => setResult(`Failed: ${e.message}`))
                    .finally(() => setBusy(false));
                }}>
          {busy ? "re-running detection…" : "re-run detection over history"}
        </button>
        {result && <span className="muted">{result}</span>}
      </div>
    </div>
  );
}
