# Agentic Ledger

[![CI](https://github.com/ShekharBhardwaj/AgenticLedger/actions/workflows/ci.yml/badge.svg)](https://github.com/ShekharBhardwaj/AgenticLedger/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ShekharBhardwaj/AgenticLedger/actions/workflows/codeql.yml/badge.svg)](https://github.com/ShekharBhardwaj/AgenticLedger/actions/workflows/codeql.yml)
[![PyPI](https://img.shields.io/pypi/v/agentic-ledger)](https://pypi.org/project/agentic-ledger/)
[![Python versions](https://img.shields.io/pypi/pyversions/agentic-ledger)](https://pypi.org/project/agentic-ledger/)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue)](https://ghcr.io/shekharbhardwaj/agentledger)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Runtime observability for AI agents — see exactly what your agent did, why it did it, and what it cost.

Works with **any agent framework**, **any LLM provider**, **any model gateway**. Zero code changes required. Point your agent at the proxy and everything is captured automatically.

---

## How it works

Agentic Ledger runs as a transparent proxy between your agent and the LLM provider. It intercepts every request and response, assigns it an `action_id`, stores it, and returns the upstream response unmodified. Your agent never knows the proxy is there.

```
Your Agent  →  Agentic Ledger Proxy  →  OpenAI / Anthropic / LiteLLM / any LLM
                      ↓
               SQLite or Postgres
                      ↓
               Live Dashboard + API
```

---

## Quick Start

**Step 1 — Start the proxy**

With Docker (recommended, no Python required):
```bash
docker run -p 8000:8000 \
  -e AGENTLEDGER_UPSTREAM_URL=https://api.openai.com \
  -v $(pwd)/data:/data \
  ghcr.io/shekharbhardwaj/agentledger:latest
```

Or with docker compose (SQLite by default — see `docker-compose.yml`):
```bash
AGENTLEDGER_UPSTREAM_URL=https://api.openai.com docker compose up
```

With `uv`:
```bash
uv add agentic-ledger
AGENTLEDGER_UPSTREAM_URL=https://api.openai.com uv run python -m agentledger.proxy
```

With `pip`:
```bash
python -m venv venv && source venv/bin/activate
pip install agentic-ledger
AGENTLEDGER_UPSTREAM_URL=https://api.openai.com ./venv/bin/python -m agentledger.proxy
```

> **Postgres?** Install the extra and set `AGENTLEDGER_DSN`:
> ```bash
> pip install "agentic-ledger[postgres]"
> AGENTLEDGER_DSN=postgresql://user:password@localhost/agentledger
> ```
> Note: the Docker image uses SQLite only. For Postgres with Docker, install via `pip` instead.

> **OpenTelemetry?** Install the extra and set `AGENTLEDGER_OTEL_ENDPOINT`:
> ```bash
> pip install "agentic-ledger[otel]"
> AGENTLEDGER_OTEL_ENDPOINT=http://localhost:4318
> ```

Proxy starts on `http://localhost:8000`. Traces are saved to `agentledger.db` in the current folder (or `/data/agentledger.db` in Docker).

---

**Step 2 — Point your agent at the proxy**

Two changes: set `base_url` to the proxy and add a session ID header to group calls into a run. Everything else — your API key, model, messages — stays exactly the same.

**OpenAI:**
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",  # ← proxy
    api_key="your-openai-key",
    default_headers={"x-agentledger-session-id": "run-1"},
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Research the top 3 AI trends in 2026"}],
)
```

**Anthropic:**
```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8000",  # ← proxy
    api_key="your-anthropic-key",
    default_headers={"x-agentledger-session-id": "run-1"},
)
```

**LiteLLM / OpenRouter / any gateway:**
```bash
# Point Agentic Ledger at your gateway
AGENTLEDGER_UPSTREAM_URL=http://localhost:4000 uv run python -m agentledger.proxy

# Then point your agent at Agentic Ledger
client = OpenAI(base_url="http://localhost:8000/v1", ...)
```

---

**Step 3 — Open the dashboard**

```
http://localhost:8000
```

The dashboard updates live via WebSocket as calls come in. No refresh needed.

- **Calls tab** — every LLM call with full prompt, system prompt, tool calls, tool results, output, tokens, cost, latency, and errors
- **Flow tab** — visual DAG of your multi-agent system. Each agent is a node with aggregated cost, latency, and call count. Edges represent handoffs. Click a node to highlight its calls.
- **Trace tab** — Gantt/waterfall timeline showing every call as a horizontal bar on a shared time axis. Parallel calls appear side-by-side at the same position — no instrumentation required. Works purely from timestamps. Click any bar to jump to the full call detail. Budget warnings show as an amber border on the bar without hiding the agent colour.
- **Search** — full-text search across all sessions by prompt, output, agent name, or user ID

---

## What gets captured

Every LLM call is stored with:

| Field | What it contains |
|---|---|
| `action_id` | UUID assigned at interception time |
| `session_id` | Run grouping (from header) |
| `timestamp` | When the call was made |
| `model_id` | Model used |
| `provider` | `openai` or `anthropic` |
| `messages` | Full message history sent to the model |
| `system_prompt` | Extracted system prompt |
| `tools` | Tool definitions available to the model |
| `tool_calls` | Tools the model decided to call |
| `tool_results` | What the tools returned (from next call's messages) |
| `content` | Model's text output |
| `stop_reason` | Why the model stopped |
| `tokens_in` / `tokens_out` | Token usage |
| `cost_usd` | Estimated cost based on model pricing |
| `latency_ms` | End-to-end response time |
| `status_code` | HTTP status from upstream — errors are captured too |
| `error_detail` | Upstream error message for non-200 responses |
| `agent_name` | From `x-agentledger-agent-name` header |
| `user_id` | From `x-agentledger-user-id` header |
| `app_id` | From `x-agentledger-app-id` header |
| `environment` | From `x-agentledger-environment` header |
| `parent_action_id` | Parent call in a nested agent graph |
| `handoff_from` / `handoff_to` | Agent handoff tracking for the Flow DAG |

---

## API reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness — `{"status":"ok","version":"..."}`. No auth, never touches the store. |
| `GET` | `/readyz` | Readiness — pings the store; `503` when unreachable. Also reports `capture_dropped`. |
| `GET` | `/metrics` | Prometheus metrics (captures persisted/dropped, queue depth). |
| `GET` | `/api/audit` | Audit trail of sensitive actions (admin). |
| `DELETE` | `/api/users/{user_id}` | Right-to-erasure: delete all of a user's captured calls (admin). |
| `GET` | `/` | Live dashboard |
| `WS` | `/ws` | WebSocket stream — powers live dashboard updates |
| `GET` | `/api/sessions` | List recent sessions with aggregated stats |
| `DELETE` | `/api/sessions/{session_id}` | Delete a session and all its calls |
| `GET` | `/api/search?q=...` | Full-text search across all captured calls |
| `GET` | `/session/{session_id}` | All calls in a session, ordered by time |
| `GET` | `/explain/{action_id}` | Single call by action ID |
| `GET` | `/export/{session_id}` | JSON compliance export with SHA-256 integrity hash |
| `GET` | `/export/{session_id}/report` | Printable HTML audit report |
| `POST` | `/mcp` | MCP tool server — `list_sessions`, `explain`, `get_session`, `search` |

**Examples:**
```bash
# All calls in a session
curl http://localhost:8000/session/run-1

# Search across all sessions
curl "http://localhost:8000/api/search?q=failed+to+connect"

# Download JSON audit trail (includes an integrity tag; keyed HMAC when configured)
curl http://localhost:8000/export/run-1 -o audit-run-1.json

# Printable HTML report — open in browser, print to PDF
open http://localhost:8000/export/run-1/report
```

---

## MCP server

Agentic Ledger exposes its captured data as an MCP (Model Context Protocol) tool server at `POST /mcp`. Point Claude Desktop, Cursor, or any MCP-compatible client at it to query traces directly from your AI assistant.

**Tools available:**

| Tool | Description |
|---|---|
| `list_sessions` | List recent sessions with cost, token, and call count summaries |
| `explain(action_id)` | Full trace for a single LLM call — prompt, tool calls, output, tokens, cost |
| `get_session(session_id)` | All calls in a session in chronological order |
| `search(query)` | Full-text search across all captured calls |

**Configure in `claude_desktop_config.json`:**
```json
{
  "mcpServers": {
    "agentledger": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

If `AGENTLEDGER_API_KEY` is set, pass it as a header:
```json
{
  "mcpServers": {
    "agentledger": {
      "url": "http://localhost:8000/mcp",
      "headers": { "x-agentledger-api-key": "your-key" }
    }
  }
}
```

Once connected, you can ask your assistant things like:
- *"What did the SearchAgent do in the last session?"*
- *"Show me all calls that mentioned rate limit errors"*
- *"What was the total cost of session run-abc123?"*

---

## Configuration

### Environment variables

**Core:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `AGENTLEDGER_UPSTREAM_URL` | **Yes** | `https://api.openai.com` | LLM endpoint to forward requests to. Accepts OpenAI, Anthropic, LiteLLM, OpenRouter, or any OpenAI-compatible URL. |
| `AGENTLEDGER_DSN` | No | `sqlite:///agentledger.db` (Docker: `sqlite:////data/agentledger.db`) | Database. SQLite for local dev, Postgres URL for production. |
| `AGENTLEDGER_HOST` | No | `0.0.0.0` | Host to bind to. Use `127.0.0.1` to restrict to localhost only. |
| `AGENTLEDGER_PORT` | No | `8000` | Port to run on. |
| `AGENTLEDGER_API_KEY` | No | _(none)_ | Master admin key. When set, the dashboard, read, and management endpoints require authentication; the key grants the `admin` role and bootstraps API tokens (below). Skip for local dev; set when the proxy is on a server — you choose the value. |
| `AGENTLEDGER_INGEST_KEY` | No | _(none)_ | When set, the proxy forwards a request only if it carries a matching `x-agentledger-ingest-key` header — closing the open relay. Off by default; a loud startup warning fires when unset. |
| `AGENTLEDGER_EXPORT_HMAC_KEY` | No | _(none)_ | When set, compliance exports carry a tamper-evident keyed `hmac-sha256` integrity tag instead of a plain `sha256` checksum. |
| `AGENTLEDGER_EXTRA_PATHS` | No | _(none)_ | Comma-separated additional request paths to capture, e.g. `v1/responses,v1/custom`. Built-in paths (`v1/chat/completions`, `v1/messages`, `v1/responses`) are always captured. |
| `AGENTLEDGER_ASYNC_CAPTURE` | No | `off` | Persist captures on a background worker so storage never adds latency to the agent's call. Trade-off: reads become **eventually consistent** (a just-captured call may not be queryable for a brief moment). Recommended for high throughput. |
| `AGENTLEDGER_CAPTURE_QUEUE_MAX` | No | `10000` | Max captures buffered in async mode before load is shed (drops are counted in `/metrics`). |
| `AGENTLEDGER_CAPTURE_LEVEL` | No | `full` | `full` stores everything; `metadata` stores only metrics/metadata (model, tokens, cost, latency, agent, status) and drops prompts, responses, and tools. |
| `AGENTLEDGER_REDACT` | No | _(off)_ | Redact PII/secrets in stored data: `all`, or a comma list of `email,ssn,credit_card,ip,api_key`. Replaces matches with `[REDACTED:<label>]`. Only the stored copy is affected — the agent's response is untouched. |
| `AGENTLEDGER_REDACT_PATTERNS` | No | _(none)_ | Extra redaction regexes as JSON: `{"label": "regex", ...}` or `["regex", ...]`. |
| `AGENTLEDGER_RETENTION_DAYS` | No | _(keep forever)_ | Delete captured calls older than N days via a background purge worker. |
| `AGENTLEDGER_AUDIT_LOG` | No | `on` | Record an audit trail of who viewed/exported/deleted what plus token/erasure actions. Set `0` to disable. |

**Cost budgets** — block calls that exceed a spend limit (returns HTTP 429):

| Variable | Default | Description |
|---|---|---|
| `AGENTLEDGER_BUDGET_SESSION` | _(none)_ | Max USD per `session_id` across its lifetime. |
| `AGENTLEDGER_BUDGET_AGENT` | _(none)_ | Max USD per `agent_name` per calendar day (UTC). |
| `AGENTLEDGER_BUDGET_DAILY` | _(none)_ | Max USD total across all calls per calendar day (UTC). |
| `AGENTLEDGER_BUDGET_ACTION` | `block` | What happens when a budget is exceeded: `block` returns HTTP 429 (call never reaches the LLM), `warn` lets the call through and fires a webhook alert, `both` blocks and fires the webhook. |

**Rate limits** — block calls that exceed request frequency (returns HTTP 429, sliding 60-second window):

| Variable | Default | Description |
|---|---|---|
| `AGENTLEDGER_RATE_LIMIT_RPM` | _(none)_ | Max requests per minute globally. |
| `AGENTLEDGER_RATE_LIMIT_SESSION_RPM` | _(none)_ | Max requests per minute per `session_id`. |
| `AGENTLEDGER_RATE_LIMIT_AGENT_RPM` | _(none)_ | Max requests per minute per `agent_name`. |
| `AGENTLEDGER_RATE_LIMIT_USER_RPM` | _(none)_ | Max requests per minute per `user_id`. |

**Alerts** — POST to your webhook when a threshold is breached (does not block calls — see [Alerts](#alerts)):

| Variable | Default | Description |
|---|---|---|
| `AGENTLEDGER_ALERT_WEBHOOK_URL` | _(none)_ | URL to POST alert payloads to. Required for any alerts to fire. |
| `AGENTLEDGER_ALERT_COST_PER_CALL` | _(none)_ | Alert when a single call costs more than `$X`. |
| `AGENTLEDGER_ALERT_LATENCY_MS` | _(none)_ | Alert when a single call takes longer than `Xms`. |
| `AGENTLEDGER_ALERT_ERROR_RATE` | _(none)_ | Alert when session error rate exceeds `X` (e.g. `0.5` = 50%). |
| `AGENTLEDGER_ALERT_DAILY_SPEND` | _(none)_ | Alert when daily spend crosses `$X`. Unlike budgets, this does not block calls. |

**OpenTelemetry** — emit spans to any OTLP-compatible collector (requires `pip install "agentic-ledger[otel]"` — see [OpenTelemetry export](#opentelemetry-export)):

| Variable | Default | Description |
|---|---|---|
| `AGENTLEDGER_OTEL_ENDPOINT` | _(none)_ | OTLP/HTTP base URL, e.g. `http://localhost:4318`. OTel export is disabled when not set. |
| `AGENTLEDGER_OTEL_SERVICE_NAME` | `agentledger` | Value of `service.name` reported to the collector. |
| `AGENTLEDGER_OTEL_HEADERS` | _(none)_ | Comma-separated `key=value` auth headers, e.g. `x-honeycomb-team=abc123`. |

**Pricing overrides** — override or extend the built-in per-token pricing table (merged at startup):

| Variable | Default | Description |
|---|---|---|
| `AGENTLEDGER_PRICING` | _(none)_ | Inline JSON map of model → `[input_per_million, output_per_million]` USD. E.g. `'{"gpt-4o": [2.50, 10.00], "my-model": [1.00, 2.00]}'`. |
| `AGENTLEDGER_PRICING_FILE` | _(none)_ | Path to a JSON file with the same format. Applied after `AGENTLEDGER_PRICING`. |

---

### Common startup examples

```bash
# Local dev — OpenAI (default)
AGENTLEDGER_UPSTREAM_URL=https://api.openai.com uv run python -m agentledger.proxy

# Local dev — Anthropic
AGENTLEDGER_UPSTREAM_URL=https://api.anthropic.com uv run python -m agentledger.proxy

# Local dev — LiteLLM gateway (any model)
AGENTLEDGER_UPSTREAM_URL=http://localhost:4000 uv run python -m agentledger.proxy

# Production — Postgres + auth + budgets + rate limits + alerts
AGENTLEDGER_UPSTREAM_URL=https://api.openai.com \
AGENTLEDGER_DSN=postgresql://user:password@localhost/agentledger \
AGENTLEDGER_API_KEY=my-secret \
AGENTLEDGER_BUDGET_DAILY=20.00 \
AGENTLEDGER_BUDGET_SESSION=2.00 \
AGENTLEDGER_RATE_LIMIT_SESSION_RPM=20 \
AGENTLEDGER_RATE_LIMIT_USER_RPM=60 \
AGENTLEDGER_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz \
AGENTLEDGER_ALERT_COST_PER_CALL=0.50 \
AGENTLEDGER_ALERT_DAILY_SPEND=15.00 \
uv run python -m agentledger.proxy
```

When `AGENTLEDGER_API_KEY` is set, pass it to access protected endpoints:
```bash
# Header
curl -H "x-agentledger-api-key: my-secret" http://localhost:8000/session/run-1

# Query param (browser)
http://localhost:8000?api_key=my-secret
```

#### Scoped API tokens (RBAC)

The master key is convenient but coarse. For team access, mint **scoped, revocable tokens** with roles instead of sharing the master secret. Tokens are random secrets shown once at creation; only their SHA-256 hash is stored.

Roles are hierarchical:

| Role | Can |
|---|---|
| `viewer` | read captured data — dashboard, API, export, MCP |
| `editor` | viewer + delete sessions |
| `admin` | editor + manage API tokens |

```bash
# Mint a viewer token (admin only — use the master key to bootstrap)
curl -X POST http://localhost:8000/api/tokens \
  -H "x-agentledger-api-key: my-secret" \
  -H "content-type: application/json" \
  -d '{"name": "grafana-readonly", "role": "viewer", "expires_in_days": 90}'
# → {"token_id": "...", "token": "agl_…", "role": "viewer", ...}  (token shown once)

# Use it (Bearer header, x-agentledger-token, or ?token=)
curl -H "Authorization: Bearer agl_…" http://localhost:8000/api/sessions

# List and revoke
curl -H "x-agentledger-api-key: my-secret" http://localhost:8000/api/tokens
curl -X DELETE -H "x-agentledger-api-key: my-secret" http://localhost:8000/api/tokens/<token_id>
```

> Auth is enforced only when `AGENTLEDGER_API_KEY` is set; the master key is the admin bootstrap for minting tokens. (Dashboard credential propagation over the live `/ws` feed is tracked as follow-up — see `ROADMAP.md`.)

---

### Request headers

Pass these from your agent on each LLM call. All optional. They enrich captured data, power the Flow tab, and enable per-dimension budgets and rate limits.

| Header | Default | Description |
|---|---|---|
| `x-agentledger-session-id` | _(none)_ | Groups all calls in a run. Use a consistent ID per agent execution (e.g. a UUID or `"run-1"`). Without this, calls are stored but not grouped in the dashboard. |
| `x-agentledger-user-id` | _(none)_ | End user who triggered this run. Enables per-user rate limiting and auditing. |
| `x-agentledger-agent-name` | _(none)_ | Name of the agent making this call (e.g. `"orchestrator"`, `"researcher"`). Powers the Flow tab DAG and agent-level budgets and rate limits. |
| `x-agentledger-app-id` | _(none)_ | Application name or ID. Useful when multiple apps share one proxy. |
| `x-agentledger-parent-action-id` | _(none)_ | The `action_id` of the call that spawned this one. When set, the Trace tab draws explicit parent→child connectors. Without it, the Trace tab infers relationships from timestamps automatically. |
| `x-agentledger-environment` | `development` | `production`, `staging`, or `development`. Shown in the dashboard. |
| `x-agentledger-handoff-from` | _(none)_ | Agent handing off control (e.g. `"orchestrator"`). Renders as a directed edge in the Flow DAG. |
| `x-agentledger-handoff-to` | _(none)_ | Agent receiving control (e.g. `"researcher"`). Renders as a directed edge in the Flow DAG. |

**Single agent — fully annotated:**
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-openai-key",
    default_headers={
        "x-agentledger-session-id":  "run-abc123",
        "x-agentledger-user-id":     "user-42",
        "x-agentledger-agent-name":  "researcher",
        "x-agentledger-app-id":      "my-app",
        "x-agentledger-environment": "production",
    },
)
```

**Multi-agent system — tracking handoffs:**
```python
from openai import OpenAI

# Orchestrator
orchestrator_client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-openai-key",
    default_headers={
        "x-agentledger-session-id":  "run-abc123",
        "x-agentledger-agent-name":  "orchestrator",
    },
)

# Researcher (receives handoff from orchestrator)
researcher_client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-openai-key",
    default_headers={
        "x-agentledger-session-id":   "run-abc123",
        "x-agentledger-agent-name":   "researcher",
        "x-agentledger-handoff-from": "orchestrator",
        "x-agentledger-handoff-to":   "researcher",
    },
)
```

The Flow tab renders `orchestrator → researcher` as a DAG with cost and latency on each node.

**OpenAI Agents SDK (`openai-agents`) — per-agent clients:**

The `openai-agents` SDK uses its own internal OpenAI client. To pass Agentic Ledger headers you need to create a client per agent using `OpenAIResponsesModel` and set it as the agent's `model`.

```python
import uuid
import os
from openai import AsyncOpenAI
from agents import Agent
from agents.models.openai_responses import OpenAIResponsesModel

SESSION_ID = f"run-{uuid.uuid4().hex[:8]}"  # one per execution
BASE_URL = os.getenv("OPENAI_BASE_URL")      # e.g. http://localhost:8000/v1

def al_model(agent_name: str, model: str = "gpt-4o-mini",
             handoff_from: str | None = None, handoff_to: str | None = None):
    """Create a model instance that sends Agentic Ledger metadata headers."""
    if not BASE_URL:
        return model  # proxy not configured — use default client
    headers = {
        "x-agentledger-session-id": SESSION_ID,
        "x-agentledger-agent-name": agent_name,
    }
    if handoff_from:
        headers["x-agentledger-handoff-from"] = handoff_from
    if handoff_to:
        headers["x-agentledger-handoff-to"] = handoff_to
    client = AsyncOpenAI(base_url=BASE_URL, api_key=os.getenv("OPENAI_API_KEY", ""),
                         default_headers=headers)
    return OpenAIResponsesModel(model=model, openai_client=client)

planner = Agent(name="PlannerAgent", model=al_model("PlannerAgent", handoff_to="SearchAgent"), ...)
searcher = Agent(name="SearchAgent",  model=al_model("SearchAgent",  handoff_from="PlannerAgent", handoff_to="WriterAgent"), ...)
writer   = Agent(name="WriterAgent",  model=al_model("WriterAgent",  handoff_from="SearchAgent",  handoff_to="EmailAgent"), ...)
emailer  = Agent(name="EmailAgent",   model=al_model("EmailAgent",   handoff_from="WriterAgent"), ...)
```

Each agent's calls are tagged with its name and pipeline position. The Flow tab renders the full `PlannerAgent → SearchAgent → WriterAgent → EmailAgent` DAG automatically.

> **Why per-agent clients?** `set_default_openai_client()` sets a single global client — fine for single-agent apps, but it can't carry different `agent_name` or `handoff_*` headers per agent in a multi-agent system. Per-agent `OpenAIResponsesModel` instances are the correct approach.

---

## Alerts

Agentic Ledger fires a `POST` to your webhook URL when a threshold is breached. You connect it to whatever you already use — Slack, PagerDuty, Discord, email, or a custom endpoint. Agentic Ledger sends the payload; the integration is on your side.

**Payload format:**
```json
{
  "type":       "high_cost",
  "message":    "Single call cost $0.1842 exceeded threshold $0.10",
  "value":      0.1842,
  "threshold":  0.10,
  "action_id":  "a1b2c3d4-...",
  "session_id": "run-1",
  "agent_name": "researcher",
  "timestamp":  "2026-04-03T12:00:00+00:00"
}
```

**Alert types:**

| Type | Triggered when |
|---|---|
| `high_cost` | A single call exceeds `AGENTLEDGER_ALERT_COST_PER_CALL` |
| `high_latency` | A single call takes longer than `AGENTLEDGER_ALERT_LATENCY_MS` |
| `high_error_rate` | Session error rate exceeds `AGENTLEDGER_ALERT_ERROR_RATE` |
| `daily_spend` | Daily total spend crosses `AGENTLEDGER_ALERT_DAILY_SPEND` |
| `budget_exceeded` | A budget limit is hit and `AGENTLEDGER_BUDGET_ACTION` is `warn` or `both` |

**Budgets vs alerts:**
- **Budgets** (`AGENTLEDGER_BUDGET_*`) — block the call before it reaches the LLM. Agent gets HTTP 429.
- **Alerts** (`AGENTLEDGER_ALERT_*`) — the call goes through, you get notified after.

**Slack** — create an [Incoming Webhook](https://api.slack.com/messaging/webhooks) and point `AGENTLEDGER_ALERT_WEBHOOK_URL` at it.

**PagerDuty** — use the [Events API v2](https://developer.pagerduty.com/docs/events-api-v2/) URL or a thin adapter that maps `type` → PagerDuty severity.

**Discord** — use a Discord channel webhook URL directly.

**Custom** — any HTTP endpoint that accepts a JSON `POST`.

---

## OpenTelemetry export

Agentic Ledger can emit every intercepted LLM call as an OTel span to any OTLP-compatible collector: Grafana Tempo, Jaeger, Honeycomb, Datadog, Dynatrace, or any vendor that supports OTLP/HTTP.

**Install the extra** (Docker image includes OTel — no extra step needed when using Docker):
```bash
pip install "agentic-ledger[otel]"
# or
uv add "agentic-ledger[otel]"
```

**Configure:**

| Variable | Default | Description |
|---|---|---|
| `AGENTLEDGER_OTEL_ENDPOINT` | _(none)_ | OTLP/HTTP base URL, e.g. `http://localhost:4318`. OTel export is disabled when not set. |
| `AGENTLEDGER_OTEL_SERVICE_NAME` | `agentledger` | Value of `service.name` in the emitted resource. |
| `AGENTLEDGER_OTEL_HEADERS` | _(none)_ | Comma-separated `key=value` pairs for auth headers, e.g. `x-honeycomb-team=abc123,x-honeycomb-dataset=llm`. |

**Example — Grafana Tempo:**
```bash
AGENTLEDGER_UPSTREAM_URL=https://api.openai.com \
AGENTLEDGER_OTEL_ENDPOINT=http://localhost:4318 \
AGENTLEDGER_OTEL_SERVICE_NAME=my-agent \
uv run python -m agentledger.proxy
```

**Example — Honeycomb:**
```bash
AGENTLEDGER_OTEL_ENDPOINT=https://api.honeycomb.io \
AGENTLEDGER_OTEL_HEADERS=x-honeycomb-team=YOUR_API_KEY,x-honeycomb-dataset=llm-traces \
uv run python -m agentledger.proxy
```

**Span attributes emitted (GenAI semantic conventions):**

| Attribute | Source |
|---|---|
| `gen_ai.system` | Provider (`openai` / `anthropic`) |
| `gen_ai.operation.name` | Always `chat` |
| `gen_ai.request.model` | Model ID |
| `gen_ai.request.temperature` | If set |
| `gen_ai.request.max_tokens` | If set |
| `gen_ai.usage.input_tokens` | Tokens in |
| `gen_ai.usage.output_tokens` | Tokens out |
| `gen_ai.response.finish_reasons` | Stop reason |
| `agentledger.action_id` | Unique call ID |
| `agentledger.session_id` | Run grouping |
| `agentledger.agent_name` | From header |
| `agentledger.user_id` | From header |
| `agentledger.cost_usd` | Estimated cost |
| `agentledger.latency_ms` | End-to-end latency |
| `agentledger.environment` | From header |
| `agentledger.handoff_from` / `agentledger.handoff_to` | Agent handoffs |
| `http.status_code` | HTTP status from upstream |

Spans are grouped into traces by `session_id` — all calls in a session appear as one trace in your backend. Parent-child relationships follow `x-agentledger-parent-action-id`. Error spans (`status_code != 200`) are marked with `StatusCode.ERROR`.

---

## Compliance export

Every session can be exported as an integrity-tagged audit trail — useful for regulated industries, internal audits, or passing traces to external tools.

```bash
# Machine-readable JSON with an integrity tag over the calls array
curl http://localhost:8000/export/run-1 -o audit-run-1.json

# Printable HTML — open in browser and print to PDF
open http://localhost:8000/export/run-1/report
```

The JSON export carries an integrity tag over the calls array. By default this is a `sha256` **checksum** — it catches accidental corruption but is not a signature (anyone who edits the calls can recompute it). Set `AGENTLEDGER_EXPORT_HMAC_KEY` to switch to a keyed **`hmac-sha256`** tag, which is tamper-evident: a recipient holding the key can detect any modification, and the tag cannot be forged without the key.

---

## Releasing

Tagging a version triggers the full release pipeline automatically:

```bash
git tag v0.2.0
git push origin v0.2.0
```

This runs three jobs:
1. **Docker** — builds and pushes `ghcr.io/shekharbhardwaj/agentledger:{version}` and `:latest` to GHCR
2. **PyPI** — builds and publishes `agentic-ledger=={version}` to PyPI using trusted publishing (no API token needed)
3. **GitHub Release** — creates a release with auto-generated changelog from commit messages

**First-time PyPI setup** (one time only):
1. Go to [pypi.org/manage/account/publishing](https://pypi.org/manage/account/publishing/)
2. Add a new pending publisher:
   ```
   PyPI project name:  agentic-ledger
   Owner:              ShekharBhardwaj
   Repository:         AgenticLedger
   Workflow name:      release.yml
   Environment name:   pypi
   ```
3. Create a `pypi` environment in GitHub: repo → Settings → Environments → New environment → name it `pypi`
4. That's it — no secrets needed

---

## License

MIT
