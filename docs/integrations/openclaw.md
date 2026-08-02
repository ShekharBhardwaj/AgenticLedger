# Agentic Ledger + OpenClaw

OpenClaw is an always-on assistant: heartbeats, cron jobs, and channel
chatter burn tokens 24/7, and OpenClaw itself tracks usage but **never
blocks spend**. Agentic Ledger sits under it as a transparent proxy and adds
the two things operators ask for most: a complete ledger of every prompt
that leaves the machine, and a **hard spend cap**.

## Fastest: the ClawHub skill

The assistant can keep its own books. One install gives it a skill that
answers "what have I spent", reads real captured numbers from the ledger,
and teaches budget setup at the moment it matters:

```bash
clawhub install agentic-ledger
```

Docker installs: `clawhub --workdir ~/.openclaw/workspace install
agentic-ledger` (clawhub otherwise tries the container's own path on your
host). Skill source:
<https://github.com/ShekharBhardwaj/openclaw-agentic-ledger>. The skill
walks the rest of this setup itself; the sections below are the same
steps by hand.

## Setup (config edit only — no OpenClaw code changes)

Start the proxy in front of your provider. For an always-on assistant you
want the ledger always on too — so run it as a background service, not in a
terminal that a closed window would kill:

```bash
pip install -U agentic-ledger
agenticledger start        # survives the terminal closing
agenticledger status       # is it up, and is the store healthy?
```

No upstream config needed: since 0.8.1 the proxy routes each call to the
provider matching its wire format. Give the assistant the ceiling
OpenClaw doesn't have (both optional):

```bash
agenticledger config set budgets.daily 10.0
agenticledger config set budgets.status 402
```

Use 402 rather than the default 429 here: OpenClaw reads a 429 as a rate
limit and retries it over and over; 402 is a final no that nothing
retries.

(The old `AGENTICLEDGER_UPSTREAM_URL=... python -m agenticledger.proxy`
command still works — env vars always beat the config file.)

**The one-command way:**

```bash
agenticledger connect openclaw
```

That writes the provider override for you — it detects a Docker install
from your config's `/home/node/...` workspace path and uses
`host.docker.internal` automatically, derives the model list from the
models your config already uses (OpenClaw's validator requires a
`models` array of `{id, name}` objects, not just a `baseUrl`), routes
attribution through the URL path since OpenClaw can't send headers, and
backs up your config first. Then restart OpenClaw.

**The manual way** — override the **native** provider's `baseUrl` in `~/.openclaw/openclaw.json`
(overriding the native provider keeps OpenClaw's request shaping — prompt
caching hints, service tiers — intact; defining a generic custom provider
does not):

```json5
{
  models: {
    providers: {
      anthropic: {
        baseUrl: "http://127.0.0.1:8000/r/openclaw-main/1",
        // OpenClaw's validator requires this array — id AND name:
        models: [
          { id: "claude-opus-5", name: "Claude Opus 5" },
        ]
      }
    }
  }
}
```

> **Running OpenClaw in Docker?** (the ghcr.io/openclaw/openclaw image —
> if your config's workspace path looks like `/home/node/...`, that's you.)
> Inside a container, `127.0.0.1` means *the container*, so the override
> must use Docker's name for your machine:
>
> ```json5
> baseUrl: "http://host.docker.internal:8000"
> ```
>
> The failure mode of getting this wrong is silence — OpenClaw runs
> normally, the ledger just never sees a call. The dashboard's empty state
> walks through exactly this checklist.

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

## Reviving an old install — the failure decoder

An OpenClaw that has sat idle accumulates operational debt. The ledger
captures every failed attempt with its reason, so read them like this:

- **401 "API key is invalid"** — the stored key died. Beware two traps:
  OpenClaw prefers its stored auth profile over `ANTHROPIC_API_KEY` in the
  environment (remove `agents/main/agent/auth-profiles.json` to restore the
  env fallback), and **never pipe a key into `openclaw models auth
  paste-token`** — the interactive prompt echoes every character of the
  secret into your terminal. Use an `--env-file` on `docker run` instead.
- **404 "model: …"** — the configured model id has been retired since the
  install (April model ids don't survive to August). Update
  `agents.defaults.model.primary` and the provider's `models` array to
  current ids.
- **Silence** — the wiring never reached the ledger; the dashboard's empty
  state walks the checklist (base URL, Docker's host.docker.internal,
  upstream match).
- **Pairing requests from IPs you don't recognize** (Cloudflare ranges are
  relay traffic) — reject anything you can't explain before approving
  operator scopes.

Known OpenClaw caveat: custom-provider `baseUrl` propagation has an open
issue (openclaw#2903) — the native-provider override above sidesteps it.
