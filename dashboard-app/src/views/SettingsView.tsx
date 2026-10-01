import { useCallback, useEffect, useState } from "react";
import { fmtAgo, get, health, listNotifications, NotificationList, post, restartLedger, sendTestNotification, setConfigValue } from "../api";

interface SettingRow {
  section: string; label: string; value: string; source: string;
  means: string; set_with: string;
  config_key: string | null; env: string | null; choices: string[] | null; secret: boolean;
}

interface SettingsBody {
  rows: SettingRow[]; config_path: string; config_loaded: boolean; restart_required: boolean;
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
  const [body, setBody] = useState<SettingsBody | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Edits are written to the config file and wait for one restart; the
  // banner carries that state for every row at once.
  const [pending, setPending] = useState<Record<string, string>>({});
  const [restart, setRestart] = useState<"idle" | "working" | "waiting" | "failed">("idle");

  useEffect(() => {
    get<SettingsBody>("/api/settings")
      .then((r) => setBody(r))
      .catch((e) => setError(e.message));
  }, []);
  const rows = body?.rows ?? null;
  const restartNeeded = Boolean(body?.restart_required) || Object.keys(pending).length > 0;

  const doRestart = () => {
    setRestart("working");
    restartLedger()
      .then(() => {
        setRestart("waiting");
        // The process re-executes; poll until it answers again, then reload.
        const started = Date.now();
        const poll = () => {
          health().then(() => location.reload()).catch(() => {
            if (Date.now() - started > 60_000) setRestart("failed");
            else window.setTimeout(poll, 1000);
          });
        };
        window.setTimeout(poll, 1500);
      })
      .catch(() => setRestart("failed"));
  };

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
        What the proxy is running with, secrets hidden. Each row says where
        its value came from: <b>file</b> = the config file ·{" "}
        <b>env</b> = typed or exported, which always wins · <b>default</b> =
        built-in. Change a value with its <b>Change</b> link: it is written to{" "}
        <span className="mono">{body?.config_path}</span> and takes effect
        when you restart, which the banner offers.
      </div>
      {restartNeeded && (
        <div className="restart-banner" role="status">
          <span>
            {restart === "waiting" ? "Restarting, the page reloads when the ledger is back."
              : restart === "failed" ? "The ledger did not come back within a minute. Check `agenticledger status` in a terminal."
              : "Saved to the config file. Restart the ledger to apply the change."}
          </span>
          {restart !== "waiting" && (
            <button className="link-btn" disabled={restart === "working"} onClick={doRestart}>
              {restart === "working" ? "restarting…" : "Restart now"}
            </button>
          )}
        </div>
      )}
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
                  <td className="mono">
                    <ValueCell row={r} pending={r.config_key ? pending[r.config_key] : undefined}
                               onSaved={(key, shown) => setPending((p) => ({ ...p, [key]: shown }))} />
                  </td>
                  <td><span className={`badge src-${r.source}`}>{r.source}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <Notifications />
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

/** The value column: the running value, and for rows the config file can
 *  set, a Change link that opens an editor (a choice list, a password box
 *  for secrets, a text field otherwise). Saved values show beside the
 *  running one until the restart applies them. */
function ValueCell({ row, pending, onSaved }: {
  row: SettingRow; pending?: string; onSaved: (key: string, shown: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!row.config_key) return <>{row.value}</>;
  const key = row.config_key;
  const save = (value: string | null) => {
    setBusy(true); setNote(null);
    setConfigValue(key, value)
      .then((res) => {
        onSaved(key, value === null ? "cleared" : (res.value ?? value));
        setEditing(false);
        if (res.env_wins) {
          setNote(`Saved, but ${res.env} is set in the environment the ledger runs in, and the environment wins. Change it where the ledger is started.`);
        }
      })
      .catch((e) => setNote(`Not saved: ${e.message}`))
      .finally(() => setBusy(false));
  };
  return (
    <div className="setting-value">
      <span>{row.value}</span>
      {pending && <span className="badge src-file setting-pending" title="written to the config file; applies on restart">{pending} on restart</span>}
      {!editing ? (
        <button className="link-btn setting-change" onClick={() => { setDraft(""); setEditing(true); }}
                aria-label={`Change ${row.label}`}>Change</button>
      ) : (
        <form className="setting-editor" onSubmit={(e) => { e.preventDefault(); save(draft); }}>
          {row.choices ? (
            <select aria-label={`New value for ${row.label}`} value={draft}
                    onChange={(e) => setDraft(e.target.value)} autoFocus>
              <option value="">pick a value</option>
              {row.choices.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          ) : (
            <input aria-label={`New value for ${row.label}`} type={row.secret ? "password" : "text"}
                   value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus
                   placeholder={row.secret ? "paste the key" : "new value"} />
          )}
          <button className="link-btn" type="submit" disabled={busy || !draft}>Save</button>
          <button className="link-btn" type="button" disabled={busy} onClick={() => save(null)}
                  title="Remove this key from the config file (the default or the environment applies again)">Clear</button>
          <button className="link-btn" type="button" onClick={() => setEditing(false)}>Cancel</button>
        </form>
      )}
      {note && <div className="muted setting-note">{note}</div>}
    </div>
  );
}

/** What the ledger sent and whether it landed, plus the one-click test
 *  that turns "did I wire Slack right?" into an answer. */
function Notifications() {
  const [list, setList] = useState<NotificationList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const refresh = useCallback(() => {
    listNotifications().then((l) => { setList(l); setError(null); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const test = () => {
    setBusy(true); setResult(null);
    sendTestNotification()
      .then((r) => {
        setResult(r.sent
          ? `Delivered as ${r.format} after ${r.row?.attempts ?? 1} attempt${(r.row?.attempts ?? 1) === 1 ? "" : "s"}.`
          : r.reason ?? `Not delivered: ${r.row?.error ?? "unknown error"} (${r.row?.attempts ?? 0} attempts).`);
        refresh();
      })
      .catch((e) => setResult(`Failed: ${e.message}`))
      .finally(() => setBusy(false));
  };
  return (
    <section className="settings-section" aria-label="Notifications">
      <div className="section-title">Notifications</div>
      <div className="muted" style={{ maxWidth: 760, marginBottom: 8 }}>
        Every alert, loop flag, run summary and digest the ledger sends, and
        whether it landed. Each is tried three times with backoff, said once
        per window, and shaped for Slack, Discord or PagerDuty when the
        webhook is one of those.
        {list && (list.enabled
          ? <> Webhook configured; payload shape: <span className="mono">{list.format}</span>.</>
          : <> No webhook configured: set <span className="mono">AGENTICLEDGER_ALERT_WEBHOOK_URL</span> and restart.</>)}
      </div>
      {error ? (
        <div className="empty">Notifications need a viewer key. ({error})</div>
      ) : (
        <>
          <div className="key-actions">
            <button className="link-btn" disabled={busy || !list?.enabled} onClick={test}>
              {busy ? "sending…" : "Send a test notification"}
            </button>
            {result && <span className="muted" role="status">{result}</span>}
          </div>
          {!list ? (
            <div className="empty">Loading…</div>
          ) : list.rows.length === 0 ? (
            <div className="empty">Nothing sent yet.</div>
          ) : (
            <table className="rtable">
              <thead>
                <tr><th>when</th><th>type</th><th>about</th><th>status</th><th>tries</th><th>detail</th></tr>
              </thead>
              <tbody>
                {list.rows.map((r) => (
                  <tr key={r.id}>
                    <td className="mono" title={r.timestamp}>{fmtAgo(r.timestamp)}</td>
                    <td className="mono">{r.type}</td>
                    <td className="mono">{r.target_id ?? "-"}</td>
                    <td>
                      <span className={`badge ${r.status === "delivered" ? "complete" : r.status === "failed" ? "error" : "fw"}`}>
                        {r.status}
                      </span>
                    </td>
                    <td className="mono">{r.attempts}</td>
                    <td className="muted">{r.error ?? r.summary ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
