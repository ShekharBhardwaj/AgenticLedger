import { useState } from "react";
import RunsView from "./views/RunsView";
import SessionsView from "./views/SessionsView";

type Tab = "runs" | "sessions";

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

export default function App() {
  const [tab, setTab] = useState<Tab>("runs");
  const [focusSession, setFocusSession] = useState<string | null>(null);

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
          <a className="tab" href="/classic" title="The original single-file dashboard">
            Classic
          </a>
        </div>
        <span className="spacer" />
        <span className="live-dot" title="live via WebSocket" />
      </div>
      {tab === "runs" ? (
        <RunsView onOpenSession={openSession} />
      ) : (
        <SessionsView focusSession={focusSession} />
      )}
    </>
  );
}
