# Agentic Ledger + OpenClaw

OpenClaw is an always-on assistant: heartbeats, cron jobs, and channel
chatter burn tokens 24/7, and OpenClaw itself tracks usage but **never
blocks spend**. Agentic Ledger sits under it as a transparent proxy and adds
the two things operators ask for most: a complete ledger of every prompt
that leaves the machine, and a **hard spend cap**.

## Setup (config edit only — no OpenClaw code changes)

Start the proxy in front of your provider. For an always-on assistant you
want the ledger always on too — so run it as a background service, not in a
terminal that a closed window would kill:

```bash
agenticledger init
agenticledger start        # survives the terminal closing
agenticledger status       # is it up, and is the store healthy?
```

```toml
# agenticledger.toml
[proxy]
upstream_url = "https://api.anthropic.com"

[budgets]
daily = 10.0        # the hard cap OpenClaw doesn't have
```

(The old `AGENTICLEDGER_UPSTREAM_URL=... python -m agenticledger.proxy`
command still works — env vars always beat the config file.)

Then override the **native** provider's `baseUrl` in `~/.openclaw/openclaw.json`
(overriding the native provider keeps OpenClaw's request shaping — prompt
caching hints, service tiers — intact; defining a generic custom provider
does not):

```json5
{
  models: {
    providers: {
      anthropic: {
        baseUrl: "http://127.0.0.1:8000"
      }
    }
  }
}
```

For OpenAI-compatible providers, point their `baseUrl` at
`http://127.0.0.1:8000/v1` the same way.

## Per-agent attribution without headers

OpenClaw's provider config can't inject dynamic headers. Use path-segment
attribution instead — one ledger instance, one entry per OpenClaw agent:

```json5
// agent "main"
baseUrl: "http://127.0.0.1:8000/r/openclaw-main/1"
```

Calls land under run `openclaw-main`, and the dashboard's Sessions view
splits traffic per agent. (The trailing segment is the iteration slot —
static `1` is fine for an always-on agent.)

## The hard spend cap

Budgets are enforced **before** the call reaches the provider:

- `AGENTICLEDGER_BUDGET_DAILY=10.00` — total across all agents per UTC day.
- `AGENTICLEDGER_BUDGET_SESSION=2.00` — per session.
- On breach the proxy returns HTTP 429, which OpenClaw's model fallback
  chain treats as a provider failure — configure a cheap local model as the
  last fallback and the assistant degrades instead of going dark.

Add loop guards for runaway tool cycles:

```toml
[env]
AGENTICLEDGER_LOOP_ACTION = "block"
AGENTICLEDGER_LOOP_REPEAT_THRESHOLD = "4"
```

Calls stopped by a cap are recorded as **blocked** (amber), separately from
real failures (red) — so a working spend cap never looks like a broken
assistant in Reports.

## Watching an always-on agent

- `/app` — Loop Lens and Sessions with live updates; cache-read columns show
  where context-creep is inflating cost.
- Alerts — `AGENTICLEDGER_ALERT_DAILY_SPEND`, `AGENTICLEDGER_ALERT_COST_PER_CALL`,
  and `loop_flag` webhooks POST to Slack/Discord (or back into an OpenClaw
  channel via a webhook skill).
- Security ledger — every outbound prompt/response is recorded; after the
  2026 exposed-instance wave, "what did my agent send in the last 24h?" is
  answerable from `GET /export/{session_id}`. A token-spike alert
  (`AGENTICLEDGER_ALERT_COST_PER_CALL`) doubles as an exfiltration tripwire.

## Cheaper heartbeats, proven before you switch

Always-on means the boring traffic dominates the bill. Open a day's session
in the dashboard, hit **⟳ Replay whole session**, and point it at a local
model: every captured moment re-runs and you get one line — *"37 / 40
moments matched · $0.00 vs $6.80"* — plus the handful of moments where the
small model would have dropped a tool call. That's how you decide which
OpenClaw agents can move to a local model without guessing.

## Per-agent cards

Rather than one shared ingest key across agents, mint a **team card** per
OpenClaw agent (`role: ingest`, optional `budget_daily`) and put it in that
agent's headers if your setup allows them. Each agent then carries its own
allowance: the chatty one hitting its ceiling never silences the rest.

Known OpenClaw caveat: custom-provider `baseUrl` propagation has an open
issue (openclaw#2903) — the native-provider override above sidesteps it.
