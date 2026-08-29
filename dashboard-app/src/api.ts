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

/** "1 iteration", "8 iterations" — a flight recorder should not ship "1 iterations". */
export function plural(n: number | null | undefined, word: string): string {
  if (n === null || n === undefined) return `? ${word}s`;
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export interface WhoAmI {
  auth: boolean;          // false = server has no key configured, all open
  role: string;
  source: string;         // "open" | "master" | "token"
  name: string | null;
  team: string | null;    // set when the key is a team card (agents only)
  dashboard: boolean;     // can this key open the dashboard?
}

export interface ReplayTarget {
  provider: string;
  host: string;
  local: boolean;
}

/** Where replays can go — feeds the replay panel's destination dropdown. */
export function replayTargets(): Promise<{ targets: ReplayTarget[]; same_provider: boolean }> {
  return get("/api/replay/targets");
}

/** Models a replay target actually serves (e.g. what's loaded in LM Studio). */
export function replayModels(provider: string): Promise<{ models: string[] }> {
  return get(`/api/replay/models?provider=${encodeURIComponent(provider)}`);
}

export interface BatchStep {
  original_action_id: string;
  original_model: string | null;
  original_content: string | null;
  original_cost_usd: number | null;
  original_latency_ms: number | null;
  status: "ok" | "failed" | "skipped";
  reason?: string;
  replay_action_id?: string;
  replay_content?: string | null;
  replay_cost_usd?: number | null;
  replay_latency_ms?: number | null;
  score?: { answered: boolean; tool_verdict: string; match: boolean;
            orig_tools: string[]; replay_tools: string[] };
}

export interface BatchJob {
  job_id: string;
  scope: string;
  ref_id: string;
  model: string;
  provider: string;
  replay_session_id: string;
  total: number;
  done: number;
  status: "running" | "done";
  steps: BatchStep[];
  report?: { replayed: number; matched: number; fumbles: string[];
             skipped: number; failed: number;
             original_cost_usd: number; replay_cost_usd: number };
}

/** Replay a whole run/session on another model — returns a job to poll. */
export function startBatchReplay(body: {
  run_id?: string; session_id?: string; model: string; provider?: string;
}): Promise<{ job_id: string; total: number; replay_session_id: string }> {
  return post("/api/replay/batch", body);
}

export interface JobSummary {
  job_id: string; status: string; scope: string; ref_id: string;
  model: string; replay_session_id: string;
}

export function listReplayJobs(params: {
  scope?: string; refId?: string; replaySessionId?: string;
}): Promise<{ jobs: JobSummary[] }> {
  const q = new URLSearchParams();
  if (params.scope) q.set("scope", params.scope);
  if (params.refId) q.set("ref_id", params.refId);
  if (params.replaySessionId) q.set("replay_session_id", params.replaySessionId);
  return get(`/api/replay/jobs?${q.toString()}`);
}

export function getReplayJob(jobId: string): Promise<BatchJob> {
  return get(`/api/replay/jobs/${encodeURIComponent(jobId)}`);
}

/** One call by id — used to follow a replay back to its original session. */
export function getCall(actionId: string): Promise<Call> {
  return get(`/api/calls/${encodeURIComponent(actionId)}`);
}

/** Version of the running proxy. /health needs no key, so this works even
 *  before one is pasted. */
export function health(): Promise<{ status: string; version: string }> {
  return fetch("/health").then((r) => r.json());
}

/** Ask the server what a key is — used by the ⚿ panel before saving. */
export async function whoami(key: string | null): Promise<WhoAmI> {
  const h: Record<string, string> = key ? { "x-agenticledger-api-key": key } : {};
  const resp = await fetch("/api/whoami", { headers: h });
  if (resp.status === 401) throw new Error("not a valid key");
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

export interface ShareInfo {
  keyed: boolean;
  wifi_url: string | null;
  tunnel_url: string | null;
}

export function shareInfo(): Promise<ShareInfo> {
  return get<ShareInfo>("/api/share");
}

/** The pairing QR as SVG markup (fetched with auth headers — an <img>
 *  can't carry them). */
export async function shareQr(which: "wifi" | "tunnel"): Promise<string> {
  const resp = await fetch(`/api/share/qr.svg?which=${which}`, { headers: headers() });
  if (!resp.ok) throw new Error(`qr: ${resp.status}`);
  return resp.text();
}

export async function get<T>(path: string): Promise<T> {
  const resp = await fetch(path, { headers: headers() });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    const msg = (data as { error?: string; detail?: string }).error
      ?? (data as { detail?: string }).detail
      ?? `${resp.status} ${resp.statusText}`;
    throw new Error(msg);
  }
  return resp.json();
}

export async function del(path: string): Promise<void> {
  const resp = await fetch(path, { method: "DELETE", headers: headers() });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
}

export async function put<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "PUT",
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

/** Download the model report CSV via blob to keep credentials in headers instead of URLs. */
export function downloadReportsCsv(days: number, tzOffset: number, project = ""): Promise<void> {
  return fetch(`/api/reports.csv?days=${days}&tz_offset_minutes=${tzOffset}`
               + (project ? `&project=${encodeURIComponent(project)}` : ""), {
    headers: headers(),
  })
    .then((resp) => {
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
      return resp.blob();
    })
    .then((blob) => {
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `agenticledger-models-${days}d.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    });
}

/** Name, pin, or file a session/run under a project (#47). */
export function setLabel(
  scope: "session" | "run", refId: string,
  fields: { name?: string; pinned?: boolean; project?: string; budget_usd?: number },
): Promise<unknown> {
  return put(`/api/labels/${scope}/${encodeURIComponent(refId)}`, fields);
}

export function listProjects(): Promise<{
  projects: string[]; bindings: Record<string, string>;
}> {
  return get("/api/projects");
}

/** Declare a project — optionally bound to an app id so matching sessions
 *  and runs file themselves, past and future. */
export function createProject(name: string, appId?: string): Promise<unknown> {
  return post("/api/projects", { name, ...(appId ? { app_id: appId } : {}) });
}

export function renameProject(oldName: string, newName: string): Promise<unknown> {
  return put(`/api/projects/${encodeURIComponent(oldName)}`, { name: newName });
}

/** purge=false: the project vanishes, its sessions survive unfiled.
 *  purge=true: everything under the project is deleted, calls and all. */
export function deleteProject(name: string, purge: boolean): Promise<{
  sessions_deleted: number; calls_deleted: number;
}> {
  return delWithBody(`/api/projects/${encodeURIComponent(name)}?purge=${purge}`);
}

async function delWithBody<T>(path: string): Promise<T> {
  const resp = await fetch(path, { method: "DELETE", headers: headers() });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = (data as { error?: string; detail?: string }).error
      ?? (data as { detail?: string }).detail ?? `${resp.status}`;
    throw new Error(msg);
  }
  return data as T;
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers() },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = data as { error?: string; detail?: string; upstream_status?: number; hint?: string };
    const parts = [
      err.error ?? `${resp.status} ${resp.statusText}`,
      err.upstream_status != null ? `(upstream ${err.upstream_status})` : "",
      err.detail ?? "",
      err.hint ?? "",
    ].filter(Boolean);
    throw new Error(parts.join("\n"));
  }
  return data as T;
}

/** Relative age for list tiles: "just now", "8m ago", "2h ago", "3d ago". */
export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "";
  const min = ms / 60000;
  if (min < 1) return "just now";
  if (min < 60) return `${Math.floor(min)}m ago`;
  if (min < 1440) return `${Math.floor(min / 60)}h ago`;
  return `${Math.floor(min / 1440)}d ago`;
}

export interface ReplaySide {
  action_id: string;
  model_id: string;
  content: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cache_read_tokens: number | null;
  cache_write_tokens: number | null;
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
  models: string | null;   // comma-separated distinct models in the run
  flagged_calls: number;
  status: "running" | "flagged" | "complete" | "ended" | "stopped";
  label: string | null;
  pinned: boolean;
  project: string | null;
  project_auto: boolean;
  app_id: string | null;
  budget_usd?: number | null;        // per-run cost ceiling (0.11 spend meter)
  burn_last_hour_usd?: number;       // spend in the last hour (detail only)
}

/** Plain-words tooltip for a run's status badge. */
export function runStatusInfo(status: string): string {
  switch (status) {
    case "running": return "calls are still arriving";
    case "complete": return "the run declared victory — its completion promise (its own \"I'm done\" signal) was seen";
    case "ended": return "the run stopped (runner exited or calls went quiet) — no claim about success either way";
    case "flagged": return "loop-pathology flags were raised — open the run to see which calls";
    case "stopped": return "calls blocked by you: this run's calls are refused at the proxy, costing nothing, until you allow them again";
    default: return status;
  }
}

export interface Iteration {
  iteration: number | null;
  call_count: number;
  session_count: number;
  started_at: string;
  last_call_at: string;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens: number;
  flagged_calls: number;
  error_calls: number;
  blocked_calls: number;
  session_id: string | null;
}

export interface Session {
  session_id: string;
  call_count: number;
  started_at: string;
  last_call_at: string;
  run_id: string | null;
  total_latency_ms: number | null;
  total_tokens_in: number | null;
  total_tokens_out: number | null;
  total_cost_usd: number | null;
  model_id: string | null;
  agent_name: string | null;
  user_id: string | null;
  environment: string | null;
  team: string | null;
  error_count: number | null;
  blocked_count: number | null;
  label: string | null;
  pinned: boolean;
  project: string | null;
  project_auto: boolean;
  app_id: string | null;
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
  team: string | null;
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

/** One captured call, as the proxy announces it on /ws the moment it
 *  lands — the substance behind the Live Loop stage (#96). */
export interface LiveCall {
  type: string;
  action_id: string;
  session_id: string | null;
  status_code: number;
  budget_warning: boolean;
  run_id: string | null;
  iteration: number | null;
  model_id: string | null;
  provider: string | null;
  cost_usd: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  latency_ms: number;
  blocked: boolean;
  error: boolean;
  flags: string[];
}

/** Subscribe to live call events; returns an unsubscribe function.
 *  onEvent fires debounced (refetch trigger); onCall fires immediately
 *  per event with the parsed payload, for surfaces that stage calls as
 *  they happen. */
export function liveUpdates(onEvent: () => void, onCall?: (ev: LiveCall) => void): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let debounce: number | undefined;
  // Rejections back off (3s doubling to 60s). A dashboard holding a stale
  // key was hammering a rejecting server several times a second, forever —
  // seen live after a key rotation. A socket that OPENS resets the delay.
  let retryDelay = 3000;

  const connect = () => {
    if (closed) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const key = apiKey ? `?token=${encodeURIComponent(apiKey)}` : "";
    ws = new WebSocket(`${proto}://${window.location.host}/ws${key}`);
    let counted = false;
    ws.onopen = () => {
      counted = true;
      retryDelay = 3000;
      openSockets += 1;
      notifyStatus();
    };
    ws.onmessage = (msg) => {
      if (onCall) {
        try {
          const ev = JSON.parse(msg.data) as LiveCall;
          if (ev && ev.type === "call") onCall(ev);
        } catch { /* a malformed frame still triggers the refetch below */ }
      }
      window.clearTimeout(debounce);
      debounce = window.setTimeout(onEvent, 400);
    };
    ws.onclose = () => {
      if (counted) {
        counted = false;
        openSockets -= 1;
        notifyStatus();
      }
      if (!closed) {
        window.setTimeout(connect, retryDelay);
        if (!counted) retryDelay = Math.min(retryDelay * 2, 60000);
      }
    };
  };
  connect();
  // #82 — silence must not freeze the page. Without traffic there are no
  // websocket events, so "ago" strings and inactivity-derived statuses
  // (running → ended) kept whatever the last render said. A slow steady
  // tick re-fetches even when nothing is happening.
  const tick = window.setInterval(onEvent, 60_000);
  return () => {
    closed = true;
    window.clearInterval(tick);
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