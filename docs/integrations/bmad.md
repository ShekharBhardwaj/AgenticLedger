# Agentic Ledger + BMAD-METHOD

BMAD has no telemetry of its own — it rides on host coding agents. Because
every host supports a base-URL override, Agentic Ledger captures **100% of a
BMAD project's LLM traffic with zero BMAD changes**, and answers the questions
the community asks constantly: *what did this story cost, which persona burns
the tokens, how many QA→Dev bounces did this epic take?*

## Setup (once per host tool)

Start the proxy — one config file, running in the background:

```bash
agenticledger init     # writes agenticledger.toml
agenticledger start    # background; terminal stays yours
```

In `agenticledger.toml`, point it at your provider and give BMAD work a
sensible ceiling:

```toml
[proxy]
upstream_url = "https://api.anthropic.com"

[budgets]
session = 5.0     # one story cycle
daily = 25.0      # the whole project, per day
```

(Environment variables still work and always override the file — the old
`AGENTICLEDGER_UPSTREAM_URL=... python -m agenticledger.proxy` command is
unchanged if you prefer it.)

**Claude Code** — add to `.claude/settings.json` in your BMAD project:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:8000",
    "ANTHROPIC_CUSTOM_HEADERS": "x-agenticledger-app-id: my-bmad-project"
  }
}
```

**Codex CLI / OpenAI-style tools:**

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
```

**Gemini CLI** — no base-URL override; use OTLP instead:

```bash
gemini --telemetry --telemetry-otlp-endpoint=http://localhost:8000 \
       --telemetry-otlp-protocol=http
```

## What you get automatically

- **Persona detection.** Calls are tagged `framework=bmad` with the persona
  that is actually running — `bmad:spec`, `bmad:dev`, `bmad:analyst`,
  `bmad:architect`, `bmad:pm`, and so on. Both generations of BMAD are
  recognised: v4/v5 shipped personas as system prompts, while **v6 ships
  them as host-tool skills**, so the ledger reads the skill invocation
  (`Skill → bmad-spec`) and uses the most recent one, since every request
  carries the whole conversation. Before the first skill runs, calls are
  tagged `bmad` without a persona. The Flow view renders handoffs from
  these tags; per-persona budgets and rate limits key off them.
- **Cost per story cycle.** Each fresh-context dev cycle appears as its own
  session (Claude Code sessions are auto-detected); repeated cycles against
  the same story prompt group into a run — see the Loop Lens at `/app`.
- **Unattended `bmad-loop` guardrails.** Budgets stop runaway stories —
  set them in `[budgets]` above, and add loop guards in the same file:

  ```toml
  [env]
  AGENTICLEDGER_LOOP_ACTION = "block"
  AGENTICLEDGER_LOOP_MAX_STEPS = "60"
  ```

  A story stopped by its ceiling is recorded as **blocked** (amber), never
  as an error — a wall doing its job never makes the run look broken.

- **Mid-run introspection.** Register the MCP server in the same host tool
  (`http://localhost:8000/mcp`) and any persona can ask
  *"what has this session cost so far?"* (`get_session`, `list_runs`,
  `get_run_status`).

## Would this epic survive on a cheaper model?

The question BMAD teams actually argue about. Open a story cycle's run (or
session) in the dashboard and hit **⟳ Replay whole run**: every step
re-executes on the model you choose — including a free local one — with its
original inputs, and you get a report card instead of forty transcripts:

> **31 / 38 moments matched** · 7 to read · $0.00 on qwen3 vs $4.12 original

The fumbles are named — *dropped the tools*, *invented tools*, *different
tools* — which for BMAD usually means a persona that stopped calling its
file/story tools and started narrating. That's the evidence for keeping
Opus on `bmad:dev` while moving `bmad:analyst` somewhere cheap.

## One card per team, one key for the project

If several people run the same BMAD project, mint each a **team card**
instead of sharing the provider key:

```bash
curl -X POST http://localhost:8000/api/tokens \
  -H "x-agenticledger-api-key: $ADMIN_KEY" -H 'content-type: application/json' \
  -d '{"name": "squad-a", "role": "ingest", "budget_daily": 10.00}'
```

Each card opens the proxy, stamps every call with the squad's name, and
carries its own daily allowance — one squad running dry never blocks the
others. Revoke a card and it dies instantly.

## Naming what you'll want to find later

Epics generate a lot of look-alike sessions. In the dashboard: ✎ to name a
cycle ("story 2.4 — payment retries"), ★ to pin the one you're arguing
about, and a project field to file every session in an epic together —
then filter the whole view down to that epic.

## Explicit tagging (optional)

Headers always beat fingerprints. To pin a story or epic explicitly, have
your wrapper set:

```
x-agenticledger-run-id:    epic-2
x-agenticledger-iteration: 3          # story number within the epic
x-agenticledger-agent-name: bmad:dev
```

New BMAD versions change persona wording — the fingerprint table lives in
`agenticledger/proxy/detect.py` (`_BMAD_MARKERS` / `_BMAD_PERSONAS`); PRs
adding signatures are welcome.
