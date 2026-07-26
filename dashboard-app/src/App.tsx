import { useState } from "react";
import RunsView from "./views/RunsView";
import SessionsView from "./views/SessionsView";

type Tab = "runs" | "sessions";

export default function App() {
  const [tab, setTab] = useState<Tab>("runs");

  return (
    <>
      <div className="topbar">
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
          <a className="tab" href="/" title="Flow DAG + Trace timeline (classic dashboard)">
            Classic
          </a>
        </div>
        <span className="spacer" />
        <span className="live-dot" title="live via WebSocket" />
      </div>
      {tab === "runs" ? <RunsView /> : <SessionsView />}
    </>
  );
}
