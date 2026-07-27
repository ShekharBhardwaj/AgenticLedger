# Agentic Ledger + BMAD-METHOD

BMAD has no telemetry of its own — it rides on host coding agents. Because
every host supports a base-URL override, Agentic Ledger captures **100% of a
BMAD project's LLM traffic with zero BMAD changes**, and answers the questions
the community asks constantly: *what did this story cost, which persona burns
the tokens, how many QA→Dev bounces did this epic take?*

## Setup (once per host tool)

Start the proxy:

```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.anthropic.com python -m agenticledger.proxy
```

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

- **Persona detection.** BMAD persona prompts are fingerprinted from the
  system prompt: calls are tagged `framework=bmad` with
  `agent_name=bmad:sm`, `bmad:dev`, `bmad:qa`, `bmad:architect`,
  `bmad:analyst`, `bmad:pm`, `bmad:po`, `bmad:ux`. The Flow view renders
  SM → Dev → QA handoffs from these tags; per-persona budgets and rate
  limits key off them.
- **Cost per story cycle.** Each fresh-context dev cycle appears as its own
  session (Claude Code sessions are auto-detected); repeated cycles against
  the same story prompt group into a run — see the Loop Lens at `/app`.
- **Unattended `bmad-loop` guardrails.** Budgets stop runaway stories:

  ```bash
  AGENTICLEDGER_BUDGET_SESSION=5.00 \
  AGENTICLEDGER_BUDGET_DAILY=25.00 \
  AGENTICLEDGER_LOOP_ACTION=block \
  AGENTICLEDGER_LOOP_MAX_STEPS=60 \
  python -m agenticledger.proxy
  ```

- **Mid-run introspection.** Register the MCP server in the same host tool
  (`http://localhost:8000/mcp`) and any persona can ask
  *"what has this session cost so far?"* (`get_session`, `list_runs`,
  `get_run_status`).

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
