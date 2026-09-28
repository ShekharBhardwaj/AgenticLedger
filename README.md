<p align="left"><img src="https://raw.githubusercontent.com/ShekharBhardwaj/AgenticLedger/main/docs/raccoon.svg" alt="" width="66" height="66"></p>

# Agentic Ledger

[![CI](https://github.com/ShekharBhardwaj/AgenticLedger/actions/workflows/ci.yml/badge.svg)](https://github.com/ShekharBhardwaj/AgenticLedger/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ShekharBhardwaj/AgenticLedger/actions/workflows/codeql.yml/badge.svg)](https://github.com/ShekharBhardwaj/AgenticLedger/actions/workflows/codeql.yml)
[![PyPI](https://img.shields.io/pypi/v/agentic-ledger)](https://pypi.org/project/agentic-ledger/)
[![Python versions](https://img.shields.io/pypi/pyversions/agentic-ledger)](https://pypi.org/project/agentic-ledger/)
[![Docker](https://img.shields.io/badge/docker-ghcr.io-blue)](https://ghcr.io/shekharbhardwaj/agentic-ledger)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/LICENSE)
[![MCP server on Glama](https://glama.ai/mcp/servers/ShekharBhardwaj/AgenticLedger/badges/score.svg)](https://glama.ai/mcp/servers/ShekharBhardwaj/AgenticLedger)
[![BYOAIK status](https://qhfc1deef2.execute-api.us-east-1.amazonaws.com/tools/agentic-ledger/badge.svg)](https://www.byoaik.com/tools/agentic-ledger/)

Runtime observability for AI agents - see exactly what your agent did, why it did it, and what it cost.

**Website:** [agentic-ledger.dev](https://agentic-ledger.dev)

> The numbers are meant to match your provider bill. If they don't, [that's a bug we want](https://github.com/ShekharBhardwaj/AgenticLedger/issues/new/choose).

Works with **any agent framework**, **any LLM provider**, **any model gateway**. Zero code changes required. Point your agent at the proxy and everything is captured automatically.

---

## How it works

Agentic Ledger runs as a transparent proxy between your agent and the LLM provider. It intercepts every request and response, assigns it an `action_id`, stores it, and returns the upstream response unmodified. Your agent never knows the proxy is there. The full picture, with
diagrams and a module map for contributors, lives in
[ARCHITECTURE.md](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/ARCHITECTURE.md).

```
Your Agent  →  Agentic Ledger Proxy  →  OpenAI / Anthropic / LiteLLM / any LLM
                      ↓
               SQLite or Postgres
                      ↓
               Live Dashboard + API
```

---

## Quick Start

**Step 1 - Start the proxy**

Coming from Helicone or LangSmith? The [migration page](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/migrating.md) does the translation in two lines. Running a context compressor like Headroom? [They chain](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/chaining.md).

Two commands, zero config, no terminal held hostage:

```bash
uv tool install agentic-ledger    # or: pipx install agentic-ledger, or pip install -U agentic-ledger
agenticledger start     # runs in the background; terminal freed
```

A tool-managed install (`uv tool` / `pipx`) gets its own isolated
environment and one unambiguous shim on PATH, so shadowing by another
Python's copy becomes rare and doctor-detectable, and `agenticledger
upgrade` always means exactly one thing. Plain pip works too; if a machine ever grows
competing installs, `agenticledger doctor --fix` untangles them.

`agenticledger start` prints the dashboard URL and gives your terminal
back - closing the window doesn't stop it. `agenticledger status` tells
you it's up and healthy, `agenticledger logs` shows what it's doing,
`agenticledger stop` shuts it down. Want a config file anyway?
`agenticledger init` writes a commented one; see
[Configuration](#configuration) for what goes in it.

Or with Docker (no Python required):
```bash
docker run -p 8000:8000 \
  -e AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com \  # optional: omit to route by call format
  -v $(pwd)/data:/data \
  ghcr.io/shekharbhardwaj/agentic-ledger:latest
```

> The image is multi-arch (amd64/arm64), runs as a non-root user, and every
> release is signed with Sigstore and ships an SBOM. Hardening a shared
> deployment (TLS, auth keys, redaction, verification)? See the
> [deployment guide](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/deployment.md).

> **Using Anthropic / Claude?** Nothing to configure: with no upstream
> set, the proxy routes each call by its wire format, so Anthropic-style
> calls go to Anthropic and OpenAI-style calls go to OpenAI, side by side
> through one proxy. Setting an explicit `upstream_url` (a gateway like
> LiteLLM or OpenRouter, LM Studio, or a pinned provider) switches to the
> classic one-proxy-one-provider behavior, mismatch hints included.

Or with docker compose (SQLite by default - see `docker-compose.yml`):
```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com docker compose up
```
For Postgres, layer the override; it swaps the DSN and adds the database service, and the image ships the driver:
```bash
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up
```

With `uv`:
```bash
uv add agentic-ledger
AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com uv run python -m agenticledger.proxy
```

With `pip`:
```bash
python -m venv venv && source venv/bin/activate
pip install -U agentic-ledger
AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com ./venv/bin/python -m agenticledger.proxy
```

> **Postgres?** Install the extra and set `AGENTICLEDGER_DSN`:
> ```bash
> pip install "agentic-ledger[postgres]"
> AGENTICLEDGER_DSN=postgresql://user:password@localhost/agenticledger
> ```
> Note: the Docker image uses SQLite only. For Postgres with Docker, install via `pip` instead.

> **OpenTelemetry?** Install the extra and set `AGENTICLEDGER_OTEL_ENDPOINT`:
> ```bash
> pip install "agentic-ledger[otel]"
> AGENTICLEDGER_OTEL_ENDPOINT=http://localhost:4318
> ```

Proxy starts on `http://localhost:8000`. Traces are saved to `~/.agenticledger/agenticledger.db` when started with `agenticledger start` (one home for the background service, wherever you launched it from), to `agenticledger.db` in the current folder when run in the foreground (`agenticledger serve` / `python -m agenticledger.proxy`), or to `/data/agenticledger.db` in Docker.

---

**Step 2 - Point your agent at the proxy**

For Claude Code, BMAD, or OpenClaw, one command writes the config for you
(backed up, merged, Docker-aware):

```bash
agenticledger connect claude-code    # or: bmad, openclaw
```

For everything else, two changes: set `base_url` to the proxy and add a session ID header to group calls into a run. Everything else - your API key, model, messages - stays exactly the same.

**OpenAI:**
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",  # ← proxy
    api_key="your-openai-key",
    default_headers={"x-agenticledger-session-id": "run-1"},
)

response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Research the top 3 AI trends in 2026"}],
)
```

**Anthropic** (no upstream config needed: `/v1/messages` calls route to Anthropic automatically):
```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8000",  # ← proxy
    api_key="your-anthropic-key",
    default_headers={"x-agenticledger-session-id": "run-1"},
)
```

**Azure OpenAI:** point `AzureOpenAI(azure_endpoint="http://localhost:8000")` at the ledger with your resource set as the upstream; deployments are priced from the model the response names. See the [Azure guide](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/integrations/azure-openai.md).

**AWS Bedrock:** install `agentic-ledger[bedrock]`, give the ledger AWS credentials through the standard chain, and point `boto3` (`endpoint_url`) or Claude Code (`ANTHROPIC_BEDROCK_BASE_URL`) at it; the ledger re-signs each call itself. Both wires are covered: InvokeModel and the modern Converse/ConverseStream APIs. See the [Bedrock guide](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/integrations/bedrock.md).

**LiteLLM / OpenRouter / any gateway:**
```bash
# Point Agentic Ledger at your gateway
AGENTICLEDGER_UPSTREAM_URL=http://localhost:4000 uv run python -m agenticledger.proxy

# Then point your agent at Agentic Ledger
client = OpenAI(base_url="http://localhost:8000/v1", ...)
```

---

**Step 3 - Open the dashboard**

```
http://localhost:8000
```

The web app updates live via WebSocket as calls come in. No refresh needed.

- **Website-matched design** - the dashboard shares the charcoal surfaces, ice-blue accents, geometric raccoon mark, and typography of agentic-ledger.dev. Session Overview adds recorded cost, calls, token totals, and a per-call cost chart. Loop Lens pairs iteration costs with a "While you were away" timeline. Unknown call prices remain explicit; all charts use captured ledger data. Calls, Flow, Trace, replay, budget controls, and light/dark appearance remain available.
- **Loop Lens** - every loop run with its observed status (Running / Flagged / Completion declared / Ended / Calls blocked), one open metric strip (recorded spend, the run ceiling with an honest accounting track, model calls), Overview / Activity / Cache views, a recorded-event timeline that jumps straight to the evidence, a **Block calls** action that refuses a running loop's further calls at the wall (and Allow calls again to lift it; the agent being blocked cannot), per-iteration breakdowns, and plain-English explanations of every flag. Pick any two runs with **⇆** to diff them side by side - cost, iterations, calls, flags, duration with signed deltas, plus a **prompt drift** diff showing exactly what changed in the system prompt and opening instruction between the runs.
- **Sessions** - flat, scannable rows and three views: call rows (time, model, one status, latency, cost) that expand into a four-tab inspector (Response, Tools, Prompt, Raw), a **Flow** DAG of agent handoffs, and a **Trace** waterfall with real parent links from the loop engine. Rows say whose they are at a glance: team badge, red for real failures, amber for deliberate refusals, purple for replays, and a run chip linking each session to its loop.
- **Replay the whole run** - the question that decides a model switch isn't "how did it handle one call?" but "would my loop have survived?" Pick a run or session, pick a destination (a local model is free), and every step re-runs with its original inputs. You get a report card, not homework: **"34 / 40 moments matched"**, the fumbles named ("dropped the tools"), and the cost both ways. Each step is a real captured moment replayed honestly - after step one a different model would have steered a different conversation, so the ledger compares moments, not fairy tales.
- **In your pocket** - `agenticledger share` opens an https tunnel you own (via cloudflared, no account), prints the pairing link, and draws a QR in the terminal: point your phone's camera and the dashboard is in your hand, kill switch, stop all calls, and ceilings included. `--wifi` for a same-network link, `--rotate` to un-pair every device, or press **Pair a device** in the dashboard's ⚿ panel. Local machines never need a key; everyone else meets the auto-generated pairing key. The dashboard fits a phone: one pane at a time, a back button, prev/next arrows to flip between runs.
- **The wall, fleet-wide** - **Stop all calls** is the emergency stop for every agent at once: one button in Loop Lens (with a confirm), a banner on every page while it is on, and it survives a restart until someone lifts it. Allow and deny lists for models and providers (`AGENTICLEDGER_ALLOW_MODELS`, `AGENTICLEDGER_DENY_PROVIDERS`, and friends, globs welcome) turn the wrong model away with the rule named; a team card can carry its own lists and can only narrow the fleet's. Every refusal is on the record: rate limits, loop guards, budgets, ceilings, the kill switch and the stop all land as amber `blocked:` rows with the reason, counted in Reports and `/metrics`. A loop block is lifted from the session in the dashboard, no restart. The ledger only ever refuses or records; it never rewrites, reroutes, or substitutes a call.
- **The cache audit** - every run answers "was I paying full price for repeated text?" Received discount is exact from the provider's own cache reports; the missed amount is a labeled estimate with its method shown; every verdict carries the reason and a one-line fix, including "nothing missed, you're fine". Also at `GET /api/runs/{id}/cache-audit`.
- **Yours to keep** - dark, light, or system appearance (a browser-local choice), and URLs that hold the investigation: deep links to runs, sessions and single calls (`#/sessions/<id>/calls/<action_id>` opens the session with that call expanded), working Back/Forward, no credentials ever in ordinary links. Lists load the newest 50 and keep loading older with one press, filters reach the whole history, and the count on each sidebar is the real total.
- **Named instances** - `agenticledger start --name demo --port 8003` runs a second ledger beside your everyday one: own state, own database, its dashboard wears an amber name chip so it can never pass for the real thing. `stop`, `status`, `logs`, `share`, and `run` all take `--name`.
- **The spend meter** - a run's detail reads its money live: spent so far, burning $/h, "at this pace $Y by 8:00 AM". Give any run a **cost ceiling** and the proxy refuses further calls the moment spend reaches it (amber, costing nothing) until you raise or clear it; the ceiling survives restarts and guards auto-detected loops too. A webhook alert fires at 80%.
- **Names, pins, projects, icons** - call it "the overnight auth fix" instead of `cc-73a26366`, ★ pin what matters to the top, file work under a project and the Sessions view reads as sections: a heading per project, its sessions beneath, the unfiled pile last. A run filed under a project files its sessions with it. Give a loop or a session an icon and a color from the ✎ editor's picker and it stands out at a glance.
- **Settings** - the ⚙ shows what the proxy is actually running with: config file in effect, upstream, budgets, replay targets, each row labeled file / env / default. Read-only, secrets hidden.
- **Replay & what-if** - open any call and **↻ Replay** it: pick a destination (the panel lists what your local server actually has loaded), and the exact captured prompt re-executes there - same provider, the other one, or **a free local model via LM Studio**; tool calls, schemas, and system prompts are translated between the Anthropic and OpenAI wire formats automatically. Works even on calls your own budget blocked - the wall can say no and you can still see what would have happened, for $0. Replays tie back to their original with **↩ Open original**. The **what-if** box answers the cheaper question first: reprice any run or session on another model with pure math, no API calls. (Configure `AGENTICLEDGER_REPLAY_API_KEY` and/or the per-provider `AGENTICLEDGER_REPLAY_*_KEY` targets.)
- **Reports** - where the money goes: spend per day, model mix with **latency p50/p95/p99**, per-agent totals, a **by-team table** with each team's spend against its card's daily allowance ("who ran dry?" in one glance), and **cache savings** - what your prompt-cache traffic would have cost at full input rates versus what it actually cost. Errors and blocks are counted apart everywhere: **red = something broke, amber = the ledger refused on purpose** - a healthy wall never makes a healthy agent look sick
- **Search** - full-text search across all sessions by prompt, output, agent name, or user ID

---

## Configuration

`agenticledger init` writes `agenticledger.toml` with every option
commented. Uncomment what you need - a working setup looks like this:

```toml
[proxy]
port = 8000
upstream_url = "https://api.anthropic.com"
db = "sqlite:///agenticledger.db"

[keys]
# Prefer *_file: the file's contents are the key, so no secret lives in
# this file or your shell history (chmod 600 the key file).
api_key_file = "~/.agenticledger/api.key"       # dashboard/admin access
ingest_key_file = "~/.agenticledger/ingest.key" # closes the open relay

[budgets]
daily = 25.0          # whole-ledger daily ceiling, USD
session = 5.0         # per-session ceiling

[replay]
# Free local replay via LM Studio (any key works there):
openai_url = "http://localhost:1234"
openai_key = "lm-studio"
```

Three rules:

1. **The file is found in this order:** `AGENTICLEDGER_CONFIG`, then
   `./agenticledger.toml` (the folder you start from), then
   `~/.agenticledger/config.toml`. First match wins; the startup banner
   names the file in effect.
2. **Anything typed in the command beats the file.** Env vars override
   per-setting (`AGENTICLEDGER_PORT=9000 agenticledger start` uses 9000
   for that run without touching the file) - which is also why Docker and
   CI setups configured by env vars are unaffected.
3. **Changes apply on restart** (`agenticledger stop` then `start`).

Every setting in the [environment-variable reference](#configuration-reference)
below has a config-file home; an `[env]` section passes any other
`AGENTICLEDGER_*` variable through verbatim.

---

## Providers, step by step

Every provider below rides the same proxy; the only thing that changes is
which base URL you point at it. Each recipe assumes the proxy is up
(`agenticledger start`) and ends with the same check: run one call, open
http://localhost:8000, and see it in Sessions.

**OpenAI (and any OpenAI-compatible API)**

1. Point the client at the proxy:
   ```bash
   export OPENAI_BASE_URL=http://localhost:8000/v1
   ```
2. Keep your `OPENAI_API_KEY` exactly as it was - the proxy passes your
   auth header through untouched.
3. Make a call; it appears in Sessions with an O mark.

**Anthropic**

1. Point the client at the proxy:
   ```bash
   export ANTHROPIC_BASE_URL=http://localhost:8000
   ```
2. Keep your `ANTHROPIC_API_KEY` as it was.
3. Make a call; it appears with an A mark. No upstream config needed - 
   the proxy routes Anthropic-shaped calls to Anthropic by wire format.

**AWS Bedrock (direct capture)**

1. Give the ledger AWS credentials of its own through the standard chain
   (env vars, `~/.aws` profile, or an instance role) scoped to
   `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`,
   then install the extra and restart:
   ```bash
   pip install "agentic-ledger[bedrock]"
   agenticledger stop && agenticledger start
   ```
2. Check the ⚙ Settings panel: the Bedrock row should read "signing as
   the ledger in <your region>".
3. Point the client at the proxy - Claude Code:
   ```bash
   export CLAUDE_CODE_USE_BEDROCK=1
   export ANTHROPIC_BEDROCK_BASE_URL=http://localhost:8000
   ```
   boto3: `boto3.client("bedrock-runtime", endpoint_url="http://localhost:8000")`.
4. Make a call; it appears with an orange B mark. The ledger strips the
   caller's identity and re-signs with its own credentials. Full guide:
   [docs/integrations/bedrock.md](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/integrations/bedrock.md).

**Azure OpenAI**

1. Set the upstream to your resource:
   ```bash
   agenticledger config set proxy.upstream_url https://<resource>.openai.azure.com
   agenticledger stop && agenticledger start
   ```
2. Point the client's Azure endpoint at `http://localhost:8000`; keep
   your `api-key` header as it was.
3. Calls are tagged `azure-openai` and priced by the model the RESPONSE
   names, so deployment aliases can't hide the real model. Full guide:
   [docs/integrations/azure-openai.md](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/integrations/azure-openai.md).

**Local models (LM Studio, Ollama with the OpenAI API)**

1. Set the upstream to the local server:
   ```bash
   agenticledger config set proxy.upstream_url http://localhost:1234
   agenticledger stop && agenticledger start
   ```
2. `export OPENAI_BASE_URL=http://localhost:8000/v1` in the agent.
3. Calls appear with a purple mark and $0 cost. Full guide:
   [docs/integrations/lm-studio.md](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/integrations/lm-studio.md).

**Gateways (OpenRouter, LiteLLM)**

1. Set the upstream to the gateway:
   ```bash
   agenticledger config set proxy.upstream_url https://openrouter.ai/api
   agenticledger stop && agenticledger start
   ```
2. `export OPENAI_BASE_URL=http://localhost:8000/v1`; keep the gateway
   key as it was.
3. Gateway-prefixed model ids ("anthropic/claude-...") price correctly
   via substring matching. Guides: [openrouter.md](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/integrations/openrouter.md),
   [litellm.md](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/integrations/litellm.md).

Framework-specific recipes (CrewAI, LangGraph, AutoGen, Vercel AI SDK,
pydantic-ai, and more) live in [docs/integrations/](https://github.com/ShekharBhardwaj/AgenticLedger/tree/main/docs/integrations/).

---

## Coding agents - Claude Code, Ralph loops & friends

Claude Code (and most coding agents) can be pointed at the proxy with a single
environment variable - no headers, no code changes:

```bash
agenticledger start
```

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
claude
```

No upstream config needed: calls route to the provider matching their
wire format.

Agentic Ledger fingerprints Claude Code traffic automatically: every call is
tagged `framework=claude-code`, and instead of one undifferentiated bucket,
each Claude Code session appears under its **real session UUID** (the same id
`claude --resume` shows), with prompt-cache reads/writes captured and priced
correctly - cache traffic is where most of a coding agent's real spend lives.

Want a loop filed under a name you chose? Put one word in front of the
command you already run:

```bash
agenticledger run nightly-digest -- python agent.py
```

Your command runs exactly as before; its LLM calls land on the run tile
named `nightly-digest`, and each launch counts as the next iteration, so
tomorrow's run joins the same tile. Nothing in your agent's code changes.
Add `--project acme` to file the run under a dashboard project as it starts.

Running an overnight loop (Ralph-style `while :; do cat PROMPT.md | claude -p; done`)?
The same command with loop flags re-executes your command each iteration,
attributes every call to the run (via the base URL, no headers needed), and
stops on a completion promise, a budget ceiling, or the iteration cap:

```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.anthropic.com \
AGENTICLEDGER_COMPLETION_PROMISE="ALL TASKS COMPLETE" \
uv run python -m agenticledger.proxy
```

```bash
agenticledger run overnight --max-iterations 50 --budget 25 -- \
  claude -p "$(cat PROMPT.md)" --dangerously-skip-permissions
```

Each iteration shows up as *iteration N of the run* in `/api/runs`; when the
agent prints the completion promise in a response, run status flips to
`complete` and the loop exits with a cost/token summary. The word after
`run` is the run's name; without one the run is named after the folder and
the minute (`myproject-0819-1936`). Rerunning the same name continues its
iteration count instead of restarting at 1. Any existing loop
script works too - poll `GET /api/runs/{run_id}` yourself, or let the proxy's
budgets (`AGENTICLEDGER_BUDGET_DAILY=25.00`) hard-stop a runaway loop.

Iterating on the prompt? Rerun and use **⇆ compare** in the Loop Lens to diff
the two runs - cost, iterations, calls, and flags side by side - so "did the
new prompt actually help" gets a number instead of a feeling.

The same recipe works for any client with a base-URL override (Codex CLI,
opencode, OpenClaw, LiteLLM-based stacks) - set the OpenAI/Anthropic base URL
to the proxy and traffic is captured; add `x-agenticledger-*` headers when you
want explicit attribution.

**OTel-native tools** (Gemini CLI, Codex `[otel]`, AutoGen/AG2, Pydantic AI,
Vercel AI SDK) don't need the proxy at all - point their OTLP exporter at the
ledger and GenAI spans are ingested directly:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000
```

Both OTLP/HTTP encodings are accepted: JSON always, protobuf when the
`[otel]` extra is installed (the Docker image includes it). gRPC exporters
should switch to HTTP: `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`.

**Framework guides** - one per integration in
[docs/integrations](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/integrations/README.md): Claude Code, Codex CLI,
opencode, OpenClaw, BMAD-METHOD, LangGraph/LangChain, CrewAI, OpenAI Agents
SDK, Gemini CLI, AutoGen/AG2, Pydantic AI, Vercel AI SDK, LiteLLM,
OpenRouter, and LM Studio (fully offline: local model, local ledger).

**Production deployment** - TLS termination, auth keys, redaction, image
signature/SBOM verification, enterprise mirrors, and scaling guidance in
[docs/deployment.md](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/deployment.md).

---

## The numbers

Measured, not promised. Reproduce them with
`python scripts/loadtest.py --calls 2000 --seed 1000000` (Apple M-series
MacBook, SQLite backend; re-measured on 0.10 with the provider adapter
architecture in place - same numbers, 2,580 to 2,800 calls/s on both):

| What | Result |
|---|---|
| Sustained capture throughput | 2,886 proxied calls/sec |
| Added latency per call | 10ms p50 · 12ms p95 |
| Direct store writes | ~38,000 saves/sec |
| One million calls on disk | 271 MB |
| Open one session at 1M calls | 2 ms |
| Session list at 1M calls | 335 ms |
| 30-day report at 1M calls | 719 ms |

The honest caveats: the session list aggregates every session on every
load, so it grows with total history; the report window uses a timestamp
index, so it grows with the window's traffic, not the table. Your agent's
provider latency (hundreds of ms per call) dwarfs the proxy's overhead by
an order of magnitude. Postgres numbers vary with your server; the same
script measures them with `--dsn`. Cost math has its own guardrails and
a five-minute parity check against your provider console: see
[docs/accuracy.md](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/accuracy.md).

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
| `cache_read_tokens` / `cache_write_tokens` | Prompt-cache usage - reads and writes are priced correctly per provider |
| `thinking` | Extended-thinking output (Anthropic), captured separately from `content` |
| `cost_usd` | Estimated cost based on model pricing |
| `latency_ms` | End-to-end response time |
| `status_code` | HTTP status from upstream - errors are captured too |
| `error_detail` | Upstream error message for non-200 responses |
| `agent_name` | From `x-agenticledger-agent-name` header, or auto-detected (e.g. `claude-code`) |
| `framework` | From `x-agenticledger-framework` header, or fingerprint-detected (e.g. `claude-code`, `gemini-cli`, `litellm`) |
| `user_id` | From `x-agenticledger-user-id` header |
| `app_id` | From `x-agenticledger-app-id` header |
| `environment` | From `x-agenticledger-environment` header |
| `parent_action_id` | Parent call in a nested agent graph |
| `handoff_from` / `handoff_to` | Agent handoff tracking for the Flow DAG |

---

## API reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness - `{"status":"ok","version":"..."}`. No auth, never touches the store. |
| `GET` | `/readyz` | Readiness - pings the store; `503` when unreachable. Also reports `capture_dropped`. |
| `GET` | `/metrics` | Prometheus metrics (captures persisted/dropped, queue depth). |
| `GET` | `/api/audit` | Audit trail of sensitive actions, newest first, hash-chained; filter by `action`, `actor`, `target`, `since`, `until`, page with `before_seq` (admin). |
| `GET` | `/api/audit/verify` | Walk the audit hash chain and name the first break, if any (admin). |
| `DELETE` | `/api/users/{user_id}` | Right-to-erasure: delete all of a user's captured calls (admin). |
| `GET` | `/` | Live dashboard |
| `WS` | `/ws` | WebSocket stream - powers live dashboard updates |
| `GET` | `/api/sessions` | Sessions with aggregated stats, newest first. Page with `limit` (1 to 500, default 50) and `offset`; filter with `project` (a name, `__starred__`, or `run:<id>`), `run_id`, `model`, `since`, `until` (ISO, on the last call) and `q` (id, label or agent). `X-Total-Count` and `X-Next-Offset` headers say how much more there is. |
| `GET` | `/api/runs` | Loop runs (explicit or auto-inferred) with iterations, cost, status, and flagged-call counts, newest first. Same paging and headers; filters `project`, `status` (`running`, `flagged`, `complete`, `ended`, `stopped`), `model`, `since`, `until`, `q` (id or label). |
| `GET` | `/api/runs/{run_id}` | One run's status (`running` / `flagged` / `complete` / `ended` / `stopped`) - poll this from loop scripts |
| `POST` | `/api/stop` | Stop all calls: the fleet-wide emergency stop. Every LLM call, replays included, is refused at the wall and recorded until lifted; survives a restart (editor). `DELETE` lifts it, `GET` reports who engaged it and since when. |
| `GET` | `/api/sessions/{session_id}/tools` | Derived tool executions - each tool call paired with its result, latency, and error status |
| `GET` | `/api/sessions/{session_id}/loop-block` | Whether the loop circuit breaker is holding a session, and why. `DELETE` lifts it without a restart and re-arms the guards from now (editor). |
| `DELETE` | `/api/sessions/{session_id}` | Delete a session and all its calls |
| `GET` | `/api/reports?days=30` | Spend insights: daily trend, model mix with signed cache savings, latency percentiles, per-agent and per-team totals |
| `GET` | `/api/whatif?model=...&run_id=...` | Reprice a run/session/call's captured tokens on another model - pure math, zero API calls |
| `POST` | `/api/tokens` | Mint scoped API tokens - including `role: ingest` team cards with `budget_daily` and their own `allow_models` / `deny_models` / `allow_providers` / `deny_providers` lists |
| `GET` | `/api/calls/{action_id}` | One call by id - follow a replay's parent back to its original |
| `GET` | `/api/replay/targets` | Configured replay destinations (feeds the dashboard's dropdown) |
| `GET` | `/api/replay/models` | Models a replay target actually serves (`?provider=`) |
| `GET` | `/auth/status` | Whether sign-in is configured here, the provider's name, and where it starts. No auth. |
| `GET` | `/auth/login` | Start the identity-provider sign-in (`?next=/app#/runs` to return somewhere specific) |
| `POST` | `/auth/logout` | End this sign-in and clear the cookie |
| `GET` | `/api/people` | Everyone who has signed in, with the role their groups grant and live sign-in count (admin) |
| `POST` | `/api/people/{id}/signout` | End every sign-in of one person, now (admin) |
| `POST` | `/api/ws/ticket` | A one-minute, single-use ticket for the live `/ws` socket, minted with a key sent in a header (viewer) |
| `GET` | `/api/whoami` | What is the key I'm holding? Name, role, and team (for team cards) - the dashboard's ⚿ panel uses this |
| `POST` | `/api/replay/batch` | Replay a whole run or session on another model - returns a job id |
| `GET` | `/api/replay/jobs/{job_id}` | Batch progress and the report card |
| `PUT` | `/api/labels/{scope}/{ref_id}` | Name, pin, or file a session/run under a project; mark it with an `icon` and a `color` from the picker's fixed lists (`""` clears either) |
| `GET` | `/api/projects` | Project names in use |
| `GET` | `/api/settings` | What the proxy is running with (admin; secrets masked) |
| `GET` | `/api/runs/{run_id}/cache-audit` | The repeat-discount this run was eligible for and received: verdict, reason, fix, exact discount, labeled estimate |
| `POST` | `/api/redetect` | Re-run framework detection over unattributed history; returns examined, updated, and per-framework counts |
| `POST` | `/api/replay` | Re-execute a captured call - same provider or translated to the other one (`model` + optional `provider`); result stored linked to the original |
| `GET` | `/api/search?q=...` | Full-text search across all captured calls |
| `GET` | `/session/{session_id}` | All calls in a session, ordered by time |
| `GET` | `/explain/{action_id}` | Single call by action ID |
| `GET` | `/export/{session_id}` | JSON compliance export with SHA-256 integrity hash |
| `GET` | `/export/{session_id}/report` | Printable HTML audit report |
| `POST` | `/mcp` | MCP tool server - `list_sessions`, `explain`, `get_session`, `search`, `list_runs`, `get_run_status` |
| `POST` | `/v1/traces` | OTLP/HTTP JSON ingest - GenAI spans from OTel-native tools become ledger calls (`/v1/logs` also ingests Claude Code tool events into tool timings; `/v1/metrics` acked) |

**Examples:**
```bash
# All calls in a session
curl http://localhost:8000/session/run-1

# Search across all sessions
curl "http://localhost:8000/api/search?q=failed+to+connect"

# Download JSON audit trail (includes an integrity tag; keyed HMAC when configured)
curl http://localhost:8000/export/run-1 -o audit-run-1.json

# Printable HTML report - open in browser, print to PDF
open http://localhost:8000/export/run-1/report
```

---

## MCP server

Agentic Ledger exposes its captured data as an MCP (Model Context Protocol) tool server at `POST /mcp`. Point Claude Desktop, Cursor, or any MCP-compatible client at it to query traces directly from your AI assistant.

**Tools available:**

| Tool | Description |
|---|---|
| `list_sessions` | List recent sessions with cost, token, and call count summaries |
| `explain(action_id)` | Full trace for a single LLM call - prompt, tool calls, output, tokens, cost |
| `get_session(session_id)` | All calls in a session in chronological order |
| `search(query)` | Full-text search across all captured calls |
| `list_runs` | Loop runs with iterations, cost, and status |
| `get_run_status(run_id)` | One run's status - lets an agent inspect its own loop and decide whether to continue |

**Configure in `claude_desktop_config.json`** (HTTP, against a running proxy):
```json
{
  "mcpServers": {
    "agenticledger": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

**Or as a stdio subprocess** - for clients that launch servers as commands
(no running proxy required; reads the same database):
```json
{
  "mcpServers": {
    "agenticledger": {
      "command": "agenticledger",
      "args": ["mcp"],
      "env": { "AGENTICLEDGER_DSN": "sqlite:////absolute/path/to/agenticledger.db" }
    }
  }
}
```

If `AGENTICLEDGER_API_KEY` is set, pass it as a header:
```json
{
  "mcpServers": {
    "agenticledger": {
      "url": "http://localhost:8000/mcp",
      "headers": { "x-agenticledger-api-key": "your-key" }
    }
  }
}
```

Once connected, you can ask your assistant things like:
- *"What did the SearchAgent do in the last session?"*
- *"Show me all calls that mentioned rate limit errors"*
- *"What was the total cost of session run-abc123?"*

---

## Configuration reference

Every variable below can also live in `agenticledger.toml` - see
[Configuration](#configuration) for the file, the search order, and the
env-always-wins rule.

### Environment variables

**Core:**

| Variable | Required | Default | Description |
|---|---|---|---|
| `AGENTICLEDGER_UPSTREAM_URL` | No | _(unset: route by call format)_ | LLM endpoint to forward requests to. Accepts OpenAI, Anthropic, LiteLLM, OpenRouter, or any OpenAI-compatible URL. Omit it and the proxy routes each call by its wire format: Anthropic-shaped calls to Anthropic, Bedrock paths to Bedrock, everything else to OpenAI. |
| `AGENTICLEDGER_TLS` | No | _(off)_ | `1` adds a dashboard-only https listener with a self-generated certificate (the phone warns once). The agent port stays plain http. |
| `AGENTICLEDGER_TLS_PORT` | No | `8443` | Port for the https dashboard listener. |
| `AGENTICLEDGER_DSN` | No | `sqlite:///agenticledger.db` (Docker: `sqlite:////data/agenticledger.db`) | Database. SQLite for local dev, Postgres URL for production. |
| `AGENTICLEDGER_HOST` | No | `0.0.0.0` | Host to bind to. Use `127.0.0.1` to restrict to localhost only. |
| `AGENTICLEDGER_PORT` | No | `8000` | Port to run on. |
| `AGENTICLEDGER_API_KEY` | No | _(none)_ | Master admin key. When set, the dashboard, read, and management endpoints require authentication; the key grants the `admin` role and bootstraps API tokens (below). Skip for local dev; set when the proxy is on a server - you choose the value. |
| `AGENTICLEDGER_OIDC_ISSUER` / `_CLIENT_ID` / `_CLIENT_SECRET` | No | _(none)_ | Sign in with an identity provider (OpenID Connect, code flow with PKCE). `_ROLE_MAP` (`group=role,...`) decides roles; unmapped people are refused. `_GROUPS_CLAIM` (`groups`), `_SCOPES`, `_PROVIDER_NAME` optional. The redirect uses `AGENTICLEDGER_PUBLIC_URL`. |
| `AGENTICLEDGER_OIDC_SCOPE_MAP` | No | _(none)_ | `group=project,...` (a group may repeat): people in a mapped group see only those projects, everywhere the ledger reads; unfiled work is invisible to them. People in no mapped group are unscoped. |
| `AGENTICLEDGER_SESSION_IDLE_HOURS` / `_MAX_HOURS` | No | `12` / `168` | How long a sign-in lives: idle limit, and the absolute limit. |
| `AGENTICLEDGER_INGEST_KEY` | No | _(none)_ | When set, the proxy forwards a request only if it carries a matching `x-agenticledger-ingest-key` header - closing the open relay. Off by default; a loud startup warning fires when unset. |
| `AGENTICLEDGER_REPLAY_API_KEY` | No | _(none)_ | Key for same-provider replay through the proxy's own upstream - the proxy never stores agent credentials, so re-execution needs its own. |
| `AGENTICLEDGER_REPLAY_OPENAI_KEY` / `_URL` | No | _(none)_ / provider API | Cross-provider replay target: replay **any** capture on OpenAI-format models. Point `_URL` at LM Studio (`http://localhost:1234`, any key) and replaying your captured Claude calls on a local model is **free**. |
| `AGENTICLEDGER_REPLAY_ANTHROPIC_KEY` / `_URL` | No | _(none)_ / provider API | Cross-provider replay target for Claude models. |
| `AGENTICLEDGER_*_KEY_FILE` | No | _(none)_ | Every key above also reads from a file named by its `_FILE` variant - the Docker-secrets pattern; keeps keys out of shell history. |
| `AGENTICLEDGER_EXPORT_HMAC_KEY` | No | _(none)_ | When set, compliance exports carry a tamper-evident keyed `hmac-sha256` integrity tag instead of a plain `sha256` checksum. |
| `AGENTICLEDGER_EXTRA_PATHS` | No | _(none)_ | Comma-separated additional request paths to capture, e.g. `v1/responses,v1/custom`. Built-in paths (`v1/chat/completions`, `v1/messages`, `v1/responses`, plus `v1/messages/count_tokens` recorded as a free call) are always captured. |
| `AGENTICLEDGER_ASYNC_CAPTURE` | No | `off` | Persist captures on a background worker so storage never adds latency to the agent's call. Trade-off: reads become **eventually consistent** (a just-captured call may not be queryable for a brief moment). Recommended for high throughput. |
| `AGENTICLEDGER_CAPTURE_QUEUE_MAX` | No | `10000` | Max captures buffered in async mode before load is shed (drops are counted in `/metrics`). |
| `AGENTICLEDGER_CAPTURE_LEVEL` | No | `full` | `full` stores everything; `metadata` stores only metrics/metadata (model, tokens, cost, latency, agent, status) and drops prompts, responses, and tools. |
| `AGENTICLEDGER_REDACT` | No | _(off)_ | Redact PII/secrets in stored data: `all`, or a comma list of `email,ssn,credit_card,ip,api_key`. Replaces matches with `[REDACTED:<label>]`. Only the stored copy is affected - the agent's response is untouched. |
| `AGENTICLEDGER_REDACT_PATTERNS` | No | _(none)_ | Extra redaction regexes as JSON: `{"label": "regex", ...}` or `["regex", ...]`. |
| `AGENTICLEDGER_RETENTION_DAYS` | No | _(keep forever)_ | Delete captured calls older than N days via a background purge worker. |
| `AGENTICLEDGER_AUDIT_LOG` | No | `on` | Record an audit trail of who viewed/exported/deleted what plus token/erasure actions, failed logins, rejected ingest credentials and MCP reads. Set `0` to disable. |
| `AGENTICLEDGER_AUDIT_STRICT` | No | `off` | Refuse (HTTP 503) any audited action the log cannot record. Off, the failed write is counted (`agenticledger_audit_dropped_total`) and logged, and the action proceeds. |
| `AGENTICLEDGER_AUDIT_HMAC_KEY` | No | _(none)_ | Key the audit hash chain with HMAC-SHA256. Without it the chain is plain SHA-256: it catches edits, but a writer with database access can re-chain. `_FILE` variant accepted. |
| `AGENTICLEDGER_AUDIT_STDOUT` | No | `off` | Also print each audit row as one JSON line on stdout, for log scrapers and SIEM agents. With OTel export configured, rows are also sent as OTLP log records. |

**Cost budgets** - block calls that exceed a spend limit (returns HTTP 429):

| Variable | Default | Description |
|---|---|---|
| `AGENTICLEDGER_BUDGET_SESSION` | _(none)_ | Max USD per `session_id` across its lifetime. |
| `AGENTICLEDGER_BUDGET_AGENT` | _(none)_ | Max USD per `agent_name` per calendar day (UTC). |
| `AGENTICLEDGER_BUDGET_DAILY` | _(none)_ | Max USD total across all calls per calendar day (UTC). |
| `AGENTICLEDGER_BUDGET_USER` | _(none)_ | Max USD per `user_id` per calendar day (UTC) - follows the user across sessions. |
| `AGENTICLEDGER_BUDGET_STATUS` | `429` | HTTP status for budget blocks. `429` ships with an honest `Retry-After` (seconds until the UTC-midnight window reset); set `402` if your clients retry 429s aggressively - nothing retries Payment Required. |
| `AGENTICLEDGER_BUDGET_ACTION` | `block` | What happens when a budget is exceeded: `block` returns HTTP 429 (call never reaches the LLM), `warn` lets the call through and fires a webhook alert, `both` blocks and fires the webhook. |
| `AGENTICLEDGER_BUDGET_UNPRICED` | `allow` | A model with no price in the packs cannot be counted. `allow` records it with cost unknown (never $0), lets it through uncounted, and names it in the log; `refuse` turns it away while any budget applies. |

Budgets and run ceilings hold under concurrency. Each admitted call reserves an estimate (its text at four chars per token plus its `max_tokens`, priced like any call) until its real cost is recorded, so a burst of parallel calls cannot each pass the same remaining room. The single call that crosses the line still goes through, as one caller always did, so overshoot is bounded to one call's cost. A reservation is released the moment the call is recorded, fails, is dropped, or is refused.

**Allow and deny lists** - refuse a model or provider before any quota is spent (returns HTTP 403 with the rule named, so agents stop rather than retry). Patterns are shell globs, matched case-insensitively. Deny wins over allow; an allow list that exists admits only what it names. Team cards can carry the same four lists (see [Team cards](#team-cards---one-proxy-many-teams)); the fleet lists always apply and a card can only narrow them.

| Variable | Default | Description |
|---|---|---|
| `AGENTICLEDGER_ALLOW_MODELS` | _(none)_ | Comma-separated model patterns, e.g. `claude-*,gpt-4o`. When set, only matching models pass. |
| `AGENTICLEDGER_DENY_MODELS` | _(none)_ | Model patterns refused outright, e.g. `*-preview`. |
| `AGENTICLEDGER_ALLOW_PROVIDERS` | _(none)_ | Provider names or patterns (`openai`, `anthropic`, `bedrock`, `azure-openai`). When set, only these pass. |
| `AGENTICLEDGER_DENY_PROVIDERS` | _(none)_ | Providers refused outright. |

**Rate limits** - block calls that exceed request frequency (returns HTTP 429, sliding 60-second window). Every refusal is recorded as an amber `blocked:` row with the reason, counted per session and team in Reports and in `/metrics` (`agenticledger_refusals_total{reason=...}`), so a retry storm is visible instead of vanishing:

| Variable | Default | Description |
|---|---|---|
| `AGENTICLEDGER_RATE_LIMIT_RPM` | _(none)_ | Max requests per minute globally. |
| `AGENTICLEDGER_RATE_LIMIT_SESSION_RPM` | _(none)_ | Max requests per minute per `session_id`. |
| `AGENTICLEDGER_RATE_LIMIT_AGENT_RPM` | _(none)_ | Max requests per minute per `agent_name`. |
| `AGENTICLEDGER_RATE_LIMIT_USER_RPM` | _(none)_ | Max requests per minute per `user_id`. |

**Loop engine** - every call is stitched into ReAct threads (`thread_id`, `step_index`, `prev_action_id`) and fresh-context loop iterations are grouped into runs, with stuck-loop detection:

| Variable | Default | Description |
|---|---|---|
| `AGENTICLEDGER_LOOP_ACTION` | `warn` | `warn` records `loop_flags` and fires a `loop_flag` webhook alert; `block` additionally returns HTTP 429 (`loop_detected`) for a session that tripped a guard, records each refusal, and can be lifted without a restart from the session in the dashboard (`DELETE /api/sessions/{id}/loop-block`; the guards re-arm from that point); `off` disables inference. |
| `AGENTICLEDGER_LOOP_REPEAT_THRESHOLD` | `3` | Consecutive identical tool calls (same tool, same arguments) before a thread is flagged stuck. |
| `AGENTICLEDGER_LOOP_MAX_STEPS` | _(none)_ | Flag (and in block mode, stop) threads that exceed this many ReAct steps. |
| `AGENTICLEDGER_LOOP_RUN_GAP_SECONDS` | `900` | Max gap between fresh-context spawns (same system prompt) that still count as iterations of one run. |
| `AGENTICLEDGER_COMPLETION_PROMISE` | _(none)_ | Regex matched against response text. On match the call is flagged `completion_promise` and the run's status becomes `complete` - loop runners poll `GET /api/runs/{run_id}` and stop. |

**Alerts** - POST to your webhook when a threshold is breached (does not block calls - see [Alerts](#alerts)):

| Variable | Default | Description |
|---|---|---|
| `AGENTICLEDGER_ALERT_WEBHOOK_URL` | _(none)_ | URL to POST alert payloads to. Required for any alerts to fire. Slack, Discord and PagerDuty URLs get their native shape. |
| `AGENTICLEDGER_ALERT_FORMAT` | `auto` | Payload shape: `auto` reads the webhook host; `generic`, `slack`, `discord` or `pagerduty` forces one. |
| `AGENTICLEDGER_ALERT_PAGERDUTY_KEY` | _(none)_ | PagerDuty Events v2 integration key; needed when the webhook is `events.pagerduty.com`. `_FILE` variant accepted. |
| `AGENTICLEDGER_PUBLIC_URL` | _(none)_ | Where the dashboard is reachable (`https://ledger.example.com`), so notifications about a run or session link to it. |
| `AGENTICLEDGER_DIGEST_HOUR` | _(off)_ | UTC hour (0-23) to POST a daily spend digest - last 24h totals, cache savings, top models/agents - to the alert webhook. Slack-incoming-webhook friendly (`text`). |
| `AGENTICLEDGER_ALERT_COST_PER_CALL` | _(none)_ | Alert when a single call costs more than `$X`. |
| `AGENTICLEDGER_ALERT_LATENCY_MS` | _(none)_ | Alert when a single call takes longer than `Xms`. |
| `AGENTICLEDGER_ALERT_ERROR_RATE` | _(none)_ | Alert when session error rate exceeds `X` (e.g. `0.5` = 50%). |
| `AGENTICLEDGER_ALERT_DAILY_SPEND` | _(none)_ | Alert when daily spend crosses `$X`. Unlike budgets, this does not block calls. |

**OpenTelemetry** - emit spans to any OTLP-compatible collector (requires `pip install "agentic-ledger[otel]"` - see [OpenTelemetry export](#opentelemetry-export)):

| Variable | Default | Description |
|---|---|---|
| `AGENTICLEDGER_OTEL_ENDPOINT` | _(none)_ | OTLP/HTTP base URL, e.g. `http://localhost:4318`. OTel export is disabled when not set. |
| `AGENTICLEDGER_OTEL_SERVICE_NAME` | `agenticledger` | Value of `service.name` reported to the collector. |
| `AGENTICLEDGER_OTEL_HEADERS` | _(none)_ | Comma-separated `key=value` auth headers, e.g. `x-honeycomb-team=abc123`. |

**Pricing overrides** - override or extend the built-in per-token pricing table (merged at startup):

| Variable | Default | Description |
|---|---|---|
| `agenticledger pricing update` | | Fetch the current price packs from the repository into `~/.agenticledger/pricing/` (overrides built-ins on next start). Network is touched only when you run it (the same is true of `agenticledger upgrade`); nothing phones home on its own. |
| `AGENTICLEDGER_PRICING` | _(none)_ | Inline JSON map of model → `[input_per_million, output_per_million]` USD. E.g. `'{"gpt-4o": [2.50, 10.00], "my-model": [1.00, 2.00]}'`. |
| `AGENTICLEDGER_PRICING_FILE` | _(none)_ | Path to a JSON file with the same format. Applied after `AGENTICLEDGER_PRICING`. |

---

### Common startup examples

```bash
# Local dev - OpenAI (default)
AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com uv run python -m agenticledger.proxy

# Local dev - Anthropic
AGENTICLEDGER_UPSTREAM_URL=https://api.anthropic.com uv run python -m agenticledger.proxy

# Local dev - LiteLLM gateway (any model)
AGENTICLEDGER_UPSTREAM_URL=http://localhost:4000 uv run python -m agenticledger.proxy

# Production - Postgres + auth + budgets + rate limits + alerts
AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com \
AGENTICLEDGER_DSN=postgresql://user:password@localhost/agenticledger \
AGENTICLEDGER_API_KEY=my-secret \
AGENTICLEDGER_BUDGET_DAILY=20.00 \
AGENTICLEDGER_BUDGET_SESSION=2.00 \
AGENTICLEDGER_RATE_LIMIT_SESSION_RPM=20 \
AGENTICLEDGER_RATE_LIMIT_USER_RPM=60 \
AGENTICLEDGER_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz \
AGENTICLEDGER_ALERT_COST_PER_CALL=0.50 \
AGENTICLEDGER_ALERT_DAILY_SPEND=15.00 \
uv run python -m agenticledger.proxy
```

When `AGENTICLEDGER_API_KEY` is set, pass it in a header to access protected endpoints:
```bash
curl -H "x-agenticledger-api-key: my-secret" http://localhost:8000/session/run-1
```

Keys travel in headers only. A key in a query string (`?api_key=`, `?token=`) is refused with a 401 that says why: URLs end up in access logs, proxy logs, browser history and Referer headers. In a browser, paste the key into the dashboard's ⚿ panel, or open the pairing link from `agenticledger share`, which carries the key after the `#` (the URL fragment, which a browser never sends to any server).

#### Sign in with your identity provider (OpenID Connect)

For people, not scripts: point the ledger at your identity provider and the ⚿ panel gains a **Sign in with Okta** button (or whatever you name it). The code flow with PKCE, ID tokens verified against the provider's keys (RS256), and your groups decide the role: a person whose groups map to nothing is refused, told why, and recorded. Keys keep working beside it for agents and scripts.

```bash
AGENTICLEDGER_OIDC_ISSUER=https://your-org.okta.com
AGENTICLEDGER_OIDC_CLIENT_ID=0oa...
AGENTICLEDGER_OIDC_CLIENT_SECRET_FILE=/run/secrets/oidc   # omit for a public client
AGENTICLEDGER_OIDC_ROLE_MAP=ledger-admins=admin,ledger-editors=editor,ledger-viewers=viewer
AGENTICLEDGER_PUBLIC_URL=https://ledger.example.com          # the redirect back lands here
```

Register `https://ledger.example.com/auth/callback` as the redirect URI with the provider. A sign-in is a server-side row the browser holds a cookie for (httponly, SameSite=Lax, Secure over https): it ends after 12 idle hours or 7 days (`AGENTICLEDGER_SESSION_IDLE_HOURS`, `AGENTICLEDGER_SESSION_MAX_HOURS`), on Sign out, or when an admin ends it (`POST /api/people/{id}/signout`). Mutating requests that ride a cookie must come from the dashboard's own origin. Every audit row names the person by email. `GET /api/people` lists who has signed in, with their role and groups.

**Scoped access.** `AGENTICLEDGER_OIDC_SCOPE_MAP=team-alpha=alpha,team-alpha=alpha-infra` makes anyone in `team-alpha` see exactly those projects: the lists, single sessions and runs, search, reports, exports, what-if, replay and the MCP tools all answer inside the scope, and anything outside it reads as not found. Work filed under no project is invisible to a scoped person until someone files it. A person in no mapped group is unscoped and sees everything their role allows, as before. Scoped editors can file work only under their own projects.

To try it without a provider: `agenticledger idp` runs a test provider on loopback with four fake people (alice is an admin, dave has no mapped group), prints the four lines to set, and says on every page that it is not for production.

#### Scoped API tokens (RBAC)

The master key is convenient but coarse. For team access, mint **scoped, revocable tokens** with roles instead of sharing the master secret. Tokens are random secrets shown once at creation; only their SHA-256 hash is stored.

Roles are hierarchical, with one exception: `ingest` sits outside the hierarchy and opens only the proxy path.

| Role | Can |
|---|---|
| `ingest` | send calls through the proxy only (this is what a team card is): attributes each call to its team and carries the team's daily budget; cannot read the dashboard, API, export or MCP |
| `viewer` | read captured data - dashboard, API, export, MCP |
| `editor` | viewer + delete sessions |
| `admin` | editor + manage API tokens |

```bash
# Mint a viewer token (admin only - use the master key to bootstrap)
curl -X POST http://localhost:8000/api/tokens \
  -H "x-agenticledger-api-key: my-secret" \
  -H "content-type: application/json" \
  -d '{"name": "grafana-readonly", "role": "viewer", "expires_in_days": 90}'
# → {"token_id": "...", "token": "agl_…", "role": "viewer", ...}  (token shown once)

# Use it (Authorization: Bearer or the x-agenticledger-token header)
curl -H "Authorization: Bearer agl_…" http://localhost:8000/api/sessions

# List and revoke
curl -H "x-agenticledger-api-key: my-secret" http://localhost:8000/api/tokens
curl -X DELETE -H "x-agenticledger-api-key: my-secret" http://localhost:8000/api/tokens/<token_id>
```

> Auth is enforced only when `AGENTICLEDGER_API_KEY` is set; the master key is the admin bootstrap for minting tokens. The live `/ws` feed accepts a header (`Authorization: Bearer` or `x-agenticledger-token`) or a ticket: a browser cannot put a header on a websocket handshake, so the dashboard first calls `POST /api/ws/ticket` with its key in a header and connects with `/ws?ticket=...`, a random one-minute, single-use ticket that is worthless once used. Unauthenticated connects, and any connect with a key in its URL, are rejected with close code 1008.

---

### Request headers

Pass these from your agent on each LLM call. All optional. They enrich captured data, power the Flow tab, and enable per-dimension budgets and rate limits.

| Header | Default | Description |
|---|---|---|
| `x-agenticledger-session-id` | _(none)_ | Groups all calls in a run. Use a consistent ID per agent execution (e.g. a UUID or `"run-1"`). Without this, calls are stored but not grouped in the dashboard. |
| `x-agenticledger-user-id` | _(none)_ | End user who triggered this run. Enables per-user rate limiting and auditing. |
| `x-agenticledger-agent-name` | _(none)_ | Name of the agent making this call (e.g. `"orchestrator"`, `"researcher"`). Powers the Flow tab DAG and agent-level budgets and rate limits. |
| `x-agenticledger-app-id` | _(none)_ | Application name or ID. Useful when multiple apps share one proxy. |
| `x-agenticledger-parent-action-id` | _(none)_ | The `action_id` of the call that spawned this one. When set, the Trace tab draws explicit parent→child connectors. Without it, the Trace tab infers relationships from timestamps automatically. |
| `x-agenticledger-environment` | `development` | `production`, `staging`, or `development`. Shown in the dashboard. |
| `x-agenticledger-handoff-from` | _(none)_ | Agent handing off control (e.g. `"orchestrator"`). Renders as a directed edge in the Flow DAG. |
| `x-agenticledger-handoff-to` | _(none)_ | Agent receiving control (e.g. `"researcher"`). Renders as a directed edge in the Flow DAG. |
| `x-agenticledger-framework` | _(auto-detected)_ | Framework/tool making the call (e.g. `"langgraph"`, `"bmad"`). When absent, well-known clients are fingerprinted automatically (Claude Code, Gemini CLI, LiteLLM). |
| `x-agenticledger-run-id` | _(auto-inferred)_ | Groups sessions into a loop run (e.g. a Ralph overnight run). When absent, fresh-context sessions sharing a system prompt within `AGENTICLEDGER_LOOP_RUN_GAP_SECONDS` are grouped automatically. |
| `x-agenticledger-iteration` | _(auto-inferred)_ | Iteration number within the run. |

**Single agent - fully annotated:**
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-openai-key",
    default_headers={
        "x-agenticledger-session-id":  "run-abc123",
        "x-agenticledger-user-id":     "user-42",
        "x-agenticledger-agent-name":  "researcher",
        "x-agenticledger-app-id":      "my-app",
        "x-agenticledger-environment": "production",
    },
)
```

**Multi-agent system - tracking handoffs:**
```python
from openai import OpenAI

# Orchestrator
orchestrator_client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-openai-key",
    default_headers={
        "x-agenticledger-session-id":  "run-abc123",
        "x-agenticledger-agent-name":  "orchestrator",
    },
)

# Researcher (receives handoff from orchestrator)
researcher_client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-openai-key",
    default_headers={
        "x-agenticledger-session-id":   "run-abc123",
        "x-agenticledger-agent-name":   "researcher",
        "x-agenticledger-handoff-from": "orchestrator",
        "x-agenticledger-handoff-to":   "researcher",
    },
)
```

The Flow tab renders `orchestrator → researcher` as a DAG with cost and latency on each node.

**OpenAI Agents SDK (`openai-agents`) - per-agent clients:**

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
        return model  # proxy not configured - use default client
    headers = {
        "x-agenticledger-session-id": SESSION_ID,
        "x-agenticledger-agent-name": agent_name,
    }
    if handoff_from:
        headers["x-agenticledger-handoff-from"] = handoff_from
    if handoff_to:
        headers["x-agenticledger-handoff-to"] = handoff_to
    client = AsyncOpenAI(base_url=BASE_URL, api_key=os.getenv("OPENAI_API_KEY", ""),
                         default_headers=headers)
    return OpenAIResponsesModel(model=model, openai_client=client)

planner = Agent(name="PlannerAgent", model=al_model("PlannerAgent", handoff_to="SearchAgent"), ...)
searcher = Agent(name="SearchAgent",  model=al_model("SearchAgent",  handoff_from="PlannerAgent", handoff_to="WriterAgent"), ...)
writer   = Agent(name="WriterAgent",  model=al_model("WriterAgent",  handoff_from="SearchAgent",  handoff_to="EmailAgent"), ...)
emailer  = Agent(name="EmailAgent",   model=al_model("EmailAgent",   handoff_from="WriterAgent"), ...)
```

Each agent's calls are tagged with its name and pipeline position. The Flow tab renders the full `PlannerAgent → SearchAgent → WriterAgent → EmailAgent` DAG automatically.

> **Why per-agent clients?** `set_default_openai_client()` sets a single global client - fine for single-agent apps, but it can't carry different `agent_name` or `handoff_*` headers per agent in a multi-agent system. Per-agent `OpenAIResponsesModel` instances are the correct approach.

---

## Alerts

Agentic Ledger posts to your webhook URL when a threshold is breached, a loop is flagged, a run hits a wall, or a run ends. Slack incoming webhooks, Discord webhooks and PagerDuty Events v2 are recognised from the URL and get their native shape (override with `AGENTICLEDGER_ALERT_FORMAT`); anything else gets the plain JSON below.

**Reliable by construction.** Every notification is tried three times with backoff (1s, 3s, 9s) off the request path, said once per window (a crossed daily budget once a day, a flagged loop once per ten minutes, a run hitting a wall once an hour), and recorded: the Settings page lists what was sent, whether it landed, after how many tries, and why not, with a **Send a test notification** button so wiring Slack takes one click. `GET /api/notifications` returns the same history. Set `AGENTICLEDGER_PUBLIC_URL` and every notification about a run or session carries a link straight to it.

**Payload format** (plain JSON; the native shapes carry the same facts):
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
| `high_cost` | A single call exceeds `AGENTICLEDGER_ALERT_COST_PER_CALL` |
| `high_latency` | A single call takes longer than `AGENTICLEDGER_ALERT_LATENCY_MS` |
| `high_error_rate` | Session error rate exceeds `AGENTICLEDGER_ALERT_ERROR_RATE` |
| `daily_spend` | Daily total spend crosses `AGENTICLEDGER_ALERT_DAILY_SPEND` |
| `budget_exceeded` | A budget limit is hit and `AGENTICLEDGER_BUDGET_ACTION` is `warn` or `both` |
| `run_ceiling_approaching` | A run's spend reaches 80% of its cost ceiling (fired once per run) |
| `loop_flag` | The loop engine raised flags on a call (`repeat_tool_call`, `step_budget_exceeded`, `completion_promise`) |
| `run_complete` | A run's completion promise was seen - the payload carries the full run summary (iterations, cost, tokens, flagged calls) |
| `run_ended` | A run went quiet (no calls for the run gap) - the same summary, so an overnight loop's end is in your channel by morning |
| `run_failed` | A run went quiet and its last iteration ended in an error - the same summary, flagged as a failure |
| `run_blocked` | A run's call was refused at the wall (kill switch, cost ceiling, budget, stop all calls, a model or provider list) - once per run and reason per hour, with the reason |
| `test` | You pressed Send a test notification |

### Team cards - one proxy, many teams

Think allowance cards: you keep the one real provider key, and hand each
team a card of its own. Each card opens the proxy, stamps every call with
the team's name, and can carry its own daily budget - when marketing hits
$10, only marketing gets blocked (with an honest `Retry-After`).

```bash
curl -X POST http://localhost:8000/api/tokens \
  -H "x-agenticledger-api-key: $ADMIN_KEY" -H 'content-type: application/json' \
  -d '{"name": "marketing", "role": "ingest", "budget_daily": 10.00}'
```

The response shows the card once - the ledger stores only its hash. The
team puts it in `x-agenticledger-ingest-key` instead of the shared key;
Reports gains a by-team table with errors, blocks, and spend-today against
each card's allowance. Revoke a card with `DELETE /api/tokens/{token_id}`
and only that team is affected - from that instant the card gets a final
**403** ("the answer is no"), which agents accept without retry storms.
Paste a card into the dashboard's ⚿ panel by mistake and it tells you, in
plain words, that cards open the relay, not the dashboard.

A card can also carry its own allow and deny lists for models and
providers, on top of the fleet-wide `AGENTICLEDGER_ALLOW_MODELS` and
friends. The fleet lists always apply; a card can only narrow them, never
grant a model the fleet denies. Refusals name the rule and the team, and
show up in the by-team table like any other block.

```bash
curl -X POST http://localhost:8000/api/tokens \
  -H "x-agenticledger-api-key: $ADMIN_KEY" -H 'content-type: application/json' \
  -d '{"name": "marketing", "role": "ingest", "budget_daily": 10.00,
       "allow_models": ["gpt-4o", "claude-sonnet-*"], "deny_providers": ["bedrock"]}'
```

**Budgets vs alerts:**
- **Budgets** (`AGENTICLEDGER_BUDGET_*`) - block the call before it reaches the LLM. Agent gets HTTP 429.
- **Alerts** (`AGENTICLEDGER_ALERT_*`) - the call goes through, you get notified after.

**What the webhook receives.** Every alert is one JSON POST with our own field names: `type` (see the table below), `message`, `value`, `threshold`, `action_id`, `session_id`, `agent_name`, `timestamp`. The daily digest (`AGENTICLEDGER_DIGEST_HOUR=8`) is a separate POST with `type: daily_digest`, a ready-to-read `text` block (last-24h spend, cache savings, top models and agents), and `totals`.

**Slack** - paste an [Incoming Webhook](https://api.slack.com/messaging/webhooks) URL (`hooks.slack.com`): each notification arrives as a titled message with the detail and an "Open in Agentic Ledger" link.

**PagerDuty** - use the [Events API v2](https://developer.pagerduty.com/docs/events-api-v2/) URL (`https://events.pagerduty.com/v2/enqueue`) and set `AGENTICLEDGER_ALERT_PAGERDUTY_KEY` to the integration key: notifications trigger incidents with a severity per type (critical for a failed or blocked run, warning for thresholds, info for summaries and digests), a dedup key, and a link to the run.

**Discord** - a channel webhook URL (`discord.com/api/webhooks/...`): a titled embed with the detail and the link.

**Custom** - any HTTP endpoint that accepts a JSON `POST`.

---

## OpenTelemetry export

Agentic Ledger can emit every intercepted LLM call as an OTel span to any OTLP-compatible collector: Grafana Tempo, Jaeger, Honeycomb, Datadog, Dynatrace, or any vendor that supports OTLP/HTTP.

**Install the extra** (Docker image includes OTel - no extra step needed when using Docker):
```bash
pip install "agentic-ledger[otel]"
# or
uv add "agentic-ledger[otel]"
```

**Configure:**

| Variable | Default | Description |
|---|---|---|
| `AGENTICLEDGER_OTEL_ENDPOINT` | _(none)_ | OTLP/HTTP base URL, e.g. `http://localhost:4318`. OTel export is disabled when not set. |
| `AGENTICLEDGER_OTEL_SERVICE_NAME` | `agenticledger` | Value of `service.name` in the emitted resource. |
| `AGENTICLEDGER_OTEL_HEADERS` | _(none)_ | Comma-separated `key=value` pairs for auth headers, e.g. `x-honeycomb-team=abc123,x-honeycomb-dataset=llm`. |

**Example - Grafana Tempo:**
```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com \
AGENTICLEDGER_OTEL_ENDPOINT=http://localhost:4318 \
AGENTICLEDGER_OTEL_SERVICE_NAME=my-agent \
uv run python -m agenticledger.proxy
```

**Example - Honeycomb:**
```bash
AGENTICLEDGER_OTEL_ENDPOINT=https://api.honeycomb.io \
AGENTICLEDGER_OTEL_HEADERS=x-honeycomb-team=YOUR_API_KEY,x-honeycomb-dataset=llm-traces \
uv run python -m agenticledger.proxy
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
| `agenticledger.action_id` | Unique call ID |
| `agenticledger.session_id` | Run grouping |
| `agenticledger.agent_name` | From header |
| `agenticledger.user_id` | From header |
| `agenticledger.cost_usd` | Estimated cost |
| `agenticledger.latency_ms` | End-to-end latency |
| `agenticledger.environment` | From header |
| `agenticledger.handoff_from` / `agenticledger.handoff_to` | Agent handoffs |
| `http.status_code` | HTTP status from upstream |

Spans are grouped into traces by `session_id` - all calls in a session appear as one trace in your backend. Parent-child relationships follow `x-agenticledger-parent-action-id`. Error spans (`status_code != 200`) are marked with `StatusCode.ERROR`.

---

## Compliance documents

For a security or privacy review: [docs/compliance](https://github.com/ShekharBhardwaj/AgenticLedger/tree/main/docs/compliance) holds the data-flow diagram, a data-processing description with a DPA annex, the subprocessor statement (none: the software runs where you install it and sends the project nothing), the HIPAA posture, a SOC 2 and ISO 27001 control mapping with evidence for every row, and the support window (the latest minor gets every fix, the previous minor gets security fixes for 90 days). Written to be attached as they are, and honest about what the project does not hold: no SOC 2 report, no ISO certificate, no BAA.

## Compliance export

Every session can be exported as an integrity-tagged audit trail - useful for regulated industries, internal audits, or passing traces to external tools.

```bash
# Machine-readable JSON with an integrity tag over the calls array
curl http://localhost:8000/export/run-1 -o audit-run-1.json

# Printable HTML - open in browser and print to PDF
open http://localhost:8000/export/run-1/report
```

The JSON export carries an integrity tag over the calls array. By default this is a `sha256` **checksum** - it catches accidental corruption but is not a signature (anyone who edits the calls can recompute it). Set `AGENTICLEDGER_EXPORT_HMAC_KEY` to switch to a keyed **`hmac-sha256`** tag, which is tamper-evident: a recipient holding the key can detect any modification, and the tag cannot be forged without the key.

---

## Releasing

Tagging a version triggers the full release pipeline automatically:

```bash
git tag v0.2.0
git push origin v0.2.0
```

This runs three jobs:
1. **Docker** - builds `ghcr.io/shekharbhardwaj/agentic-ledger:{version}` and `:latest` for linux/amd64 + linux/arm64, pushes with SBOM + provenance attestations, signs the digest with Sigstore cosign (keyless), and mirrors to Docker Hub when the `DOCKERHUB_USERNAME`/`DOCKERHUB_TOKEN` secrets are configured
2. **PyPI** - builds and publishes `agentic-ledger=={version}` to PyPI using trusted publishing (no API token needed), with PEP 740 attestations
3. **GitHub Release** - creates a release with auto-generated changelog and attaches the image SBOM (SPDX)

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
4. That's it - no secrets needed

---

## Troubleshooting

**Start here: `agenticledger doctor`.** One command prints the whole truth
of your machine: every install on PATH and who shadows whom, which Python
owns each one and whether it can actually run (wrong-architecture wheels
and missing dependencies caught by a real import probe), what the
background service is serving, and a fix-it command per finding. Most of
the problems below diagnose themselves with it. Add `--fix` and it applies the fixes it names: shadow installs evicted with their own interpreter's pip, the PATH prepend offered for cleanup, then a second diagnostic pass.

**Old version / commands or env vars named `agentledger` (no "ic")** - 
you're running a pre-0.4 release, most likely from a venv that already had
the package installed: plain `pip install agentic-ledger` says "requirement
already satisfied" and does NOT upgrade. Run `agenticledger upgrade` - it
uses the Python environment that owns the install, so there's no guessing
which pip is the right one - then restart. The proxy prints its version on the first line at startup, and
`curl localhost:8000/health` reports it too. Since 0.4.0 everything is named
`agenticledger` - see the migration notes in the CHANGELOG.

**Replay fails with 401 "invalid x-api-key"** - `AGENTICLEDGER_REPLAY_API_KEY`
needs a real provider API key from [console.anthropic.com](https://console.anthropic.com)
(or platform.openai.com). A Claude Code subscription login is **not** an API
key and cannot be used. No key? Replay for free against a local model - see
the [LM Studio guide](https://github.com/ShekharBhardwaj/AgenticLedger/blob/main/docs/integrations/lm-studio.md).

**`incompatible architecture (have 'arm64', need 'x86_64')`** on macOS - your
terminal is running under Rosetta, so Python picks its x86_64 slice while pip
installed arm64 native wheels. Check with `arch` (should print `arm64` on
Apple Silicon). Quick fix: prefix the command with `arch -arm64`. Permanent
fix: uncheck "Open using Rosetta" on your terminal app, use an Apple Silicon
build of your editor, and restart any long-lived `tmux` server.

**`module 'httpx' has no attribute 'AsyncClient'`** - fixed in
`0.3.0-alpha.2`; upgrade with `pip install --upgrade agentic-ledger`.

**Port 8000 already in use** - another proxy instance (or app) is running;
stop it or set `AGENTICLEDGER_PORT`.

**`401 OAuth access token has expired` from Claude Code** - the proxy passed
Anthropic's answer through unmodified; re-authenticate with `claude` →
`/login`. Errored calls are still captured, so you'll see the 401 in the
dashboard.

**`/` answers 404 "Web app not built"** - you're running from a source
checkout without the web-app build. `cd dashboard-app && npm ci &&
npm run build` and restart. PyPI and Docker installs always include the app.

---

## License

MIT

<!-- MCP registry ownership verification -->
`mcp-name: io.github.ShekharBhardwaj/agentic-ledger`
