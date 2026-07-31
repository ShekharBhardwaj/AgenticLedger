import { useState } from "react";
import { fmtUsd, get } from "../api";

interface WhatIfResult {
  target_model: string;
  calls: number;
  actual_cost_usd: number;
  estimated_cost_usd: number;
  delta_usd: number;
  note: string;
}

/** "This run on haiku: $0.31 instead of $4.20" — pure repricing of the
 *  captured token counts; spends nothing, needs no key. */
export default function WhatIf({ params }: { params: string }) {
  const [model, setModel] = useState("claude-haiku-4-5");
  const [result, setResult] = useState<WhatIfResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = () => {
    setBusy(true);
    setError(null);
    setResult(null);
    get<WhatIfResult>(`/api/whatif?${params}&model=${encodeURIComponent(model.trim())}`)
      .then(setResult)
      .catch((e) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const cheaper = result != null && result.delta_usd < 0;
  return (
    <div className="whatif">
      <span className="muted">what if this ran on</span>
      <input
        className="replay-model"
        value={model}
        onChange={(e) => setModel(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && run()}
      />
      <button className="link-btn" disabled={busy} onClick={run}>
        {busy ? "…" : "estimate"}
      </button>
      {error && <span className="replay-error">{error}</span>}
      {result && (
        <span className="whatif-result" title={result.note}>
          <strong>{fmtUsd(result.estimated_cost_usd)}</strong>
          <span className="muted"> instead of {fmtUsd(result.actual_cost_usd)} · </span>
          <span style={{ color: cheaper ? "var(--green)" : "var(--red)" }}>
            {cheaper ? "−" : "+"}{fmtUsd(Math.abs(result.delta_usd))}
          </span>
          <span className="muted"> · {result.calls} calls · estimate</span>
        </span>
      )}
    </div>
  );
}
