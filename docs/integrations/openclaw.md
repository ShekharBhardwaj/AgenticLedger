# Agentic Ledger + OpenClaw

OpenClaw is an always-on assistant: heartbeats, cron jobs, and channel
chatter burn tokens 24/7, and OpenClaw itself tracks usage but **never
blocks spend**. Agentic Ledger sits under it as a transparent proxy and adds
the two things operators ask for most: a complete ledger of every prompt
that leaves the machine, and a **hard spend cap**.

## Setup (config edit only — no OpenClaw code changes)

Start the proxy in front of your provider:

```bash
AGENTLEDGER_UPSTREAM_URL=https://api.anthropic.com \
AGENTLEDGER_BUDGET_DAILY=10.00 \
python -m agentledger.proxy
```

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

- `AGENTLEDGER_BUDGET_DAILY=10.00` — total across all agents per UTC day.
- `AGENTLEDGER_BUDGET_SESSION=2.00` — per session.
- On breach the proxy returns HTTP 429, which OpenClaw's model fallback
  chain treats as a provider failure — configure a cheap local model as the
  last fallback and the assistant degrades instead of going dark.

Add loop guards for runaway tool cycles:

```bash
AGENTLEDGER_LOOP_ACTION=block AGENTLEDGER_LOOP_REPEAT_THRESHOLD=4
```

## Watching an always-on agent

- `/app` — Loop Lens and Sessions with live updates; cache-read columns show
  where context-creep is inflating cost.
- Alerts — `AGENTLEDGER_ALERT_DAILY_SPEND`, `AGENTLEDGER_ALERT_COST_PER_CALL`,
  and `loop_flag` webhooks POST to Slack/Discord (or back into an OpenClaw
  channel via a webhook skill).
- Security ledger — every outbound prompt/response is recorded; after the
  2026 exposed-instance wave, "what did my agent send in the last 24h?" is
  answerable from `GET /export/{session_id}`. A token-spike alert
  (`AGENTLEDGER_ALERT_COST_PER_CALL`) doubles as an exfiltration tripwire.

Known OpenClaw caveat: custom-provider `baseUrl` propagation has an open
issue (openclaw#2903) — the native-provider override above sidesteps it.
