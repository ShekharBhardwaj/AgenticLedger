// API client for the Agentic Ledger proxy. The SPA is served by the proxy
// itself, so all paths are same-origin. Auth: an api_key/token query param on
// first load is remembered and attached to every request (the legacy embedded
// dashboard never did this — its fetches 401'd under auth).

const params = new URLSearchParams(window.location.search);
const urlKey = params.get("api_key") || params.get("token");
if (urlKey) localStorage.setItem("agentledger.key", urlKey);

export const apiKey: string | null =
  urlKey || localStorage.getItem("agentledger.key");

function headers(): Record<string, string> {
  return apiKey ? { "x-agentledger-api-key": apiKey } : {};
}

export async function get<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: headers() });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
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
  thread_id: string | null;
  step_index: number | null;
  turn_index: number | null;
  run_id: string | null;
  iteration: number | null;
  loop_flags: string | null;
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
    ws.onmessage = () => {
      window.clearTimeout(debounce);
      debounce = window.setTimeout(onEvent, 400);
    };
    ws.onclose = () => {
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
