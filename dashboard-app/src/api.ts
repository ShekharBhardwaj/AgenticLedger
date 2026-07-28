// API client for the Agentic Ledger proxy. The SPA is served by the proxy
// itself, so all paths are same-origin. Auth: an api_key/token query param on
// first load is remembered and attached to every request (the legacy embedded
// dashboard never did this — its fetches 401'd under auth).

const params = new URLSearchParams(window.location.search);
const urlKey = params.get("api_key") || params.get("token");
if (urlKey) localStorage.setItem("agenticledger.key", urlKey);

export const apiKey: string | null =
  urlKey || localStorage.getItem("agenticledger.key");

function headers(): Record<string, string> {
  return apiKey ? { "x-agenticledger-api-key": apiKey } : {};
}

export async function get<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: headers() });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

export async function del(path: string): Promise<void> {
  const resp = await fetch(path, { method: "DELETE", headers: headers() });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers() },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = (data as { error?: string; detail?: string }).error
      ?? (data as { detail?: string }).detail
      ?? `${resp.status} ${resp.statusText}`;
    throw new Error(msg);
  }
  return data as T;
}

export interface ReplaySide {
  action_id: string;
  model_id: string;
  content: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
}

export interface ReplayResult {
  original: ReplaySide;
  replay: ReplaySide & { tool_calls: unknown };
}

export interface Run {
  run_id: string;
  iterations: number | null;
  call_count: number;
  session_count: number;
  started_at: string;
  last_call_at: string;
  total_cost_usd: number;
  total_tokens_in: number;
  total_tokens_out: number;
  framework: string | null;
  flagged_calls: number;
  status: "running" | "flagged" | "complete";
}

export interface Iteration {
  iteration: number | null;
  call_count: number;
  started_at: string;
  last_call_at: string;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens: number;
  flagged_calls: number;
  error_calls: number;
  session_id: string | null;
}

export interface Session {
  session_id: string;
  call_count: number;
  started_at: string;
  total_latency_ms: number | null;
  total_tokens_in: number | null;
  total_tokens_out: number | null;
  total_cost_usd: number | null;
  model_id: string | null;
  agent_name: string | null;
  user_id: string | null;
  environment: string | null;
}

export interface Call {
  action_id: string;
  session_id: string | null;
  timestamp: string;
  model_id: string;
  provider: string;
  content: string | null;
  thinking: string | null;
  system_prompt: string | null;
  messages: unknown;
  tool_calls: { id?: string; name?: string; arguments?: unknown }[] | null;
  tool_results: unknown;
  stop_reason: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
  latency_ms: number | null;
  cost_usd: number | null;
  status_code: number | null;
  error_detail: string | null;
  agent_name: string | null;
  framework: string | null;
  handoff_from: string | null;
  handoff_to: string | null;
  parent_action_id: string | null;
  prev_action_id: string | null;
  thread_id: string | null;
  step_index: number | null;
  turn_index: number | null;
  run_id: string | null;
  iteration: number | null;
  loop_flags: string | null;
  tools: { name?: string; function?: { name?: string } }[] | null;
  temperature: number | null;
}

// Connection status shared by every live socket — the header dot listens
// here so it reflects reality (green only while at least one /ws socket is
// actually open, red while disconnected and retrying).
const statusListeners = new Set<(up: boolean) => void>();
let openSockets = 0;

function notifyStatus() {
  const up = openSockets > 0;
  statusListeners.forEach((l) => l(up));
}

/** Subscribe to live-connection status; fires immediately with the current
 *  state and returns an unsubscribe function. */
export function connectionStatus(listener: (up: boolean) => void): () => void {
  statusListeners.add(listener);
  listener(openSockets > 0);
  return () => {
    statusListeners.delete(listener);
  };
}

/** Subscribe to live call events; returns an unsubscribe function. */
export function liveUpdates(onEvent: () => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let debounce: number | undefined;

  const connect = () => {
    if (closed) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const key = apiKey ? `?token=${encodeURIComponent(apiKey)}` : "";
    ws = new WebSocket(`${proto}://${window.location.host}/ws${key}`);
    let counted = false;
    ws.onopen = () => {
      counted = true;
      openSockets += 1;
      notifyStatus();
    };
    ws.onmessage = () => {
      window.clearTimeout(debounce);
      debounce = window.setTimeout(onEvent, 400);
    };
    ws.onclose = () => {
      if (counted) {
        counted = false;
        openSockets -= 1;
        notifyStatus();
      }
      if (!closed) window.setTimeout(connect, 3000);
    };
  };
  connect();
  return () => {
    closed = true;
    ws?.close();
  };
}

export const fmtUsd = (v: number | null | undefined) =>
  v == null ? "—" : `$${v >= 1 ? v.toFixed(2) : v.toFixed(4)}`;

export const fmtNum = (v: number | null | undefined) =>
  v == null ? "—" : v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M`
  : v >= 1_000 ? `${(v / 1_000).toFixed(1)}k` : String(v);

export const fmtTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString() : "—";

export interface FlaggedCall {
  action_id: string;
  session_id: string | null;
  thread_id: string | null;
  iteration: number | null;
  step_index: number | null;
  loop_flags: string;
  tool_calls: { name?: string; arguments?: unknown }[] | null;
  model_id: string;
  timestamp: string;
}

/** Plain-English explanations for every loop flag the tracker can raise. */
export const FLAG_INFO: Record<string, { title: string; detail: string; kind: "warn" | "good" | "info" }> = {
  repeat_tool_call: {
    title: "Stuck loop suspected",
    detail:
      "The agent issued the same tool call with identical arguments several times in a row " +
      "(threshold: AGENTICLEDGER_LOOP_REPEAT_THRESHOLD, default 3). Repeating an identical " +
      "call rarely produces new information — this is the classic signature of a loop " +
      "burning tokens without making progress. With AGENTICLEDGER_LOOP_ACTION=block, the " +
      "session's next call is stopped with HTTP 429 before it reaches the provider.",
    kind: "warn",
  },
  step_budget_exceeded: {
    title: "Step budget exceeded",
    detail:
      "A single reasoning thread ran past the configured AGENTICLEDGER_LOOP_MAX_STEPS. Long " +
      "threads aren't always wrong, but past the budget each additional step re-sends the " +
      "whole context — cost grows quadratically while quality tends to degrade.",
    kind: "warn",
  },
  context_compaction: {
    title: "Context compacted",
    detail:
      "The conversation history was rewritten into a summary (e.g. Claude Code's " +
      "/compact or auto-compaction). The prefix chain broke, but the continuation " +
      "marker identified it, so the call was re-linked to its thread instead of " +
      "starting a phantom new one. Informational, not a problem flag.",
    kind: "info",
  },
  completion_promise: {
    title: "Completion promise",
    detail:
      "The response matched AGENTICLEDGER_COMPLETION_PROMISE — the agent declared the loop " +
      "done. This is the good flag: it flips the run's status to complete so loop runners " +
      "know to stop. It is not counted as a problem flag.",
    kind: "good",
  },
};

export function flagInfo(name: string) {
  return (
    FLAG_INFO[name] ?? {
      title: name,
      detail: "Flag raised by the loop tracker.",
      kind: "warn" as const,
    }
  );
}

/** Badge CSS class for a flag, by severity kind. */
export function flagBadgeClass(name: string): string {
  const kind = flagInfo(name).kind;
  return kind === "good" ? "complete" : kind === "info" ? "fw" : "flagged";
}

export interface InteractionTag {
  tag: "H2A" | "A2T" | "A2A" | "A2H";
  label: string;
}

/** Interaction-type indicators, derived from what the call itself shows:
 *  H2A human→agent (triggered by fresh human input), A2T agent→tool
 *  (response issues tool calls), A2A agent→agent (handoff metadata),
 *  A2H agent→human (plain reply ending the turn). Not mutually exclusive. */
export function interactionTags(call: Call): InteractionTag[] {
  const tags: InteractionTag[] = [];
  const msgs = Array.isArray(call.messages) ? (call.messages as any[]) : [];
  const last = msgs.length ? msgs[msgs.length - 1] : null;
  const lastIsToolCarrier =
    last &&
    (last.role === "tool" ||
      (last.role === "user" &&
        Array.isArray(last.content) &&
        last.content.length > 0 &&
        last.content.every((b: any) => b && b.type === "tool_result")));
  if (last && last.role === "user" && !lastIsToolCarrier)
    tags.push({ tag: "H2A", label: "Human → Agent: triggered by fresh user input" });
  if (call.tool_calls && call.tool_calls.length)
    tags.push({ tag: "A2T", label: "Agent → Tool: this response issues tool calls" });
  if (call.handoff_from || call.handoff_to)
    tags.push({
      tag: "A2A",
      label: `Agent → Agent: handoff ${call.handoff_from ?? "?"} → ${call.handoff_to ?? "?"}`,
    });
  if (
    (!call.tool_calls || call.tool_calls.length === 0) &&
    (call.stop_reason === "end_turn" || call.stop_reason === "stop")
  )
    tags.push({ tag: "A2H", label: "Agent → Human: plain reply ending the turn" });
  return tags;
}

/** Unique tool names issued by this call, for the collapsed card. */
export function toolNames(call: Call): string[] {
  const names = (call.tool_calls ?? [])
    .map((tc) => tc.name)
    .filter((n): n is string => Boolean(n));
  return [...new Set(names)];
}
