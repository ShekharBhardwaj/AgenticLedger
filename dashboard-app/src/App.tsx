import { useEffect, useState } from "react";
import { connectionStatus, health, whoami, WhoAmI } from "./api";
import ReportsView from "./views/ReportsView";
import RunsView from "./views/RunsView";
import SessionsView from "./views/SessionsView";
import SettingsView from "./views/SettingsView";

type Tab = "runs" | "sessions" | "reports" | "settings";

/** Abstract raccoon mark: geometric head, triangle ears, and the signature
 *  mask band in accent blue with punched-out eyes. Flat, two-tone — a nod to
 *  the classic mascot without the photo. */
function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <polygon points="6.5,11 9.2,3 15,6.8" fill="var(--text)" />
      <polygon points="25.5,11 22.8,3 17,6.8" fill="var(--text)" />
      <circle cx="16" cy="17" r="11" stroke="var(--text)" strokeWidth="2.2" />
      <ellipse cx="11.6" cy="15.6" rx="5.4" ry="3.2" fill="var(--accent)"
               transform="rotate(-14 11.6 15.6)" />
      <ellipse cx="20.4" cy="15.6" rx="5.4" ry="3.2" fill="var(--accent)"
               transform="rotate(14 20.4 15.6)" />
      <circle cx="11.2" cy="15.4" r="1.5" fill="var(--bg)" />
      <circle cx="20.8" cy="15.4" r="1.5" fill="var(--bg)" />
      <path d="M14.4 21.6 L17.6 21.6 L16 23.6 Z" fill="var(--text)" opacity="0.7" />
    </svg>
  );
}

/** Plain-words answer to "what is this key?" for the ⚿ panel. */
function describeKey(w: WhoAmI): { text: string; tone: "ok" | "warn" } {
  if (!w.auth) {
    return { text: "This server has no access key set — everything is open, no key needed.", tone: "ok" };
  }
  if (w.team) {
    return {
      text: `This is team “${w.team}”’s card — it lets agents through the relay, but it can’t open the dashboard. Paste a viewer or admin key here instead.`,
      tone: "warn",
    };
  }
  if (!w.dashboard) return { text: `This key’s role (${w.role}) can’t open the dashboard.`, tone: "warn" };
  if (w.source === "master") return { text: "Master key · full admin access", tone: "ok" };
  return { text: `${w.name ?? "unnamed key"} · ${w.role}`, tone: "ok" };
}

/** ⚿ — dashboard access key. A small panel (not a browser popup): paste a
 *  key, the server says what it is, then save. */
function KeyPanel() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [status, setStatus] = useState<{ text: string; tone: "ok" | "warn" } | null>(null);
  const stored = localStorage.getItem("agenticledger.key");

  // A fresh browser on a keyed server would otherwise just see empty views —
  // open the panel unprompted when the server wants a key we don't have
  // (or the one we have has gone stale).
  useEffect(() => {
    whoami(stored).catch(() => setOpen(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!open) return;
    setValue(stored ?? "");
    setStatus(null);
    // Identify whatever is in effect right now (stored key, or open server).
    whoami(stored)
      .then((w) => setStatus(describeKey(w)))
      .catch((e) => setStatus(stored
        ? { text: `Saved key: ${e.message}`, tone: "warn" }
        : { text: "This server needs a key — paste one to unlock the dashboard.", tone: "warn" }));
  }, [open, stored]);

  const save = () => {
    const key = value.trim();
    if (!key) {
      localStorage.removeItem("agenticledger.key");
      location.reload();
      return;
    }
    whoami(key)
      .then((w) => {
        // Open server: it ignores keys entirely, so there's nothing to save.
        if (!w.auth) { setStatus(describeKey(w)); return; }
        const d = describeKey(w);
        if (d.tone === "warn") { setStatus(d); return; } // e.g. a team card — don't save it
        localStorage.setItem("agenticledger.key", key);
        setStatus(d); // let the identity be seen ("Master key · …") before the app unlocks
        setTimeout(() => location.reload(), 900);
      })
      .catch((e) => setStatus({ text: e.message, tone: "warn" }));
  };

  return (
    <div className="key-wrap">
      <button
        className={`key-btn ${stored ? "set" : ""}`}
        title={stored ? "Access key is set — click to inspect, change, or clear"
          : "Set the dashboard access key (needed when AGENTICLEDGER_API_KEY is configured)"}
        onClick={() => setOpen(!open)}
      >
        ⚿
      </button>
      {open && (
        <div className="key-pop">
          <div className="key-pop-title">Dashboard access key</div>
          <input
            type="password"
            placeholder="paste key…"
            value={value}
            autoFocus
            onChange={(e) => { setValue(e.target.value); setStatus(null); }}
            onKeyDown={(e) => { if (e.key === "Enter") save(); }}
          />
          {status && <div className={`key-status ${status.tone}`}>{status.text}</div>}
          <div className="key-actions">
            <button className="link-btn" onClick={save}>Save</button>
            {stored && (
              <button
                className="link-btn"
                onClick={() => { localStorage.removeItem("agenticledger.key"); location.reload(); }}
              >
                Clear
              </button>
            )}
            <button className="link-btn" onClick={() => setOpen(false)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("runs");
  const [focusSession, setFocusSession] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => connectionStatus(setLive), []);
  useEffect(() => { health().then((h) => setVersion(h.version)).catch(() => {}); }, []);

  const openSession = (sessionId: string) => {
    setFocusSession(sessionId);
    setTab("sessions");
  };

  return (
    <>
      <div className="topbar">
        <Logo />
        <h1>
          Agentic <span>Ledger</span>
        </h1>
        <div className="tabs">
          <button className={`tab ${tab === "runs" ? "active" : ""}`} onClick={() => setTab("runs")}>
            Loop Lens
          </button>
          <button className={`tab ${tab === "sessions" ? "active" : ""}`} onClick={() => setTab("sessions")}>
            Sessions
          </button>
          <button className={`tab ${tab === "reports" ? "active" : ""}`} onClick={() => setTab("reports")}>
            Reports
          </button>
        </div>
        <span className="spacer" />
        {version && (
          <button
            className="version-chip"
            title={`Agentic Ledger ${version}${version.includes(".dev")
              ? " — a dev build; its version is stamped at install time"
              : ""} · click for settings`}
            onClick={() => setTab("settings")}
          >
            {/* local build metadata (+g<sha>) is noise in the header */}
            v{version.split("+")[0]}
            {version.includes(".dev") && <span className="version-dev">dev</span>}
          </button>
        )}
        <button
          className={`key-btn ${tab === "settings" ? "set" : ""}`}
          title="Settings — what the proxy is running with (read-only)"
          onClick={() => setTab(tab === "settings" ? "runs" : "settings")}
        >
          ⚙
        </button>
        <KeyPanel />
        <span
          className={`live-dot ${live ? "" : "down"}`}
          title={live ? "live via WebSocket" : "disconnected — proxy unreachable, retrying"}
        />
      </div>
      {tab === "runs" ? (
        <RunsView onOpenSession={openSession} />
      ) : tab === "reports" ? (
        <ReportsView />
      ) : tab === "settings" ? (
        <SettingsView />
      ) : (
        <SessionsView focusSession={focusSession} />
      )}
      <footer className="console-footer">
        <span>Agentic Ledger — the flight recorder for AI agents. Local-first: everything on this page stays on this machine.</span>
        <span className="spacer" />
        <a href="https://agentic-ledger.dev" target="_blank" rel="noreferrer">agentic-ledger.dev</a>
        <a href="https://github.com/ShekharBhardwaj/AgenticLedger" target="_blank" rel="noreferrer">GitHub</a>
        <a href="https://github.com/ShekharBhardwaj/AgenticLedger/issues" target="_blank" rel="noreferrer">Report an issue</a>
      </footer>
    </>
  );
}
