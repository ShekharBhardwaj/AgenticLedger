# Agentic Ledger + Claude Code

The flagship integration: one environment variable, zero code changes, and
the ledger understands Claude Code traffic natively.

## Setup

```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.anthropic.com python -m agenticledger.proxy
```

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
claude
```

## What happens automatically

- **Real sessions.** Calls are fingerprinted and grouped under the session's
  actual UUID (the same id `claude --resume` shows), tagged
  `framework=claude-code`.
- **True costs.** Prompt-cache reads and writes are captured and priced with
  Anthropic's convention — cache traffic is most of a coding session's spend.
- **Thread stitching.** Steps, parent links, and tool pairing; small
  housekeeping calls (titles, summaries) are excluded so step counts stay
  honest; `/compact` re-links instead of breaking the thread.

## Optional: the on-machine audit trail

The proxy sees LLM calls; Claude Code's telemetry sees what ran on the
machine. Send both to the ledger:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000
```

Tool executions (name, duration, success) land in the same sessions at
`GET /api/sessions/{id}/tools`.

## Loops and guardrails

```bash
agenticledger run overnight --max-iterations 50 --budget 25 -- claude -p "$(cat PROMPT.md)"
```

`AGENTICLEDGER_BUDGET_DAILY`, `AGENTICLEDGER_LOOP_ACTION=block`, and
`AGENTICLEDGER_COMPLETION_PROMISE` turn an overnight loop into something you
can trust unattended. See the Loop Lens at `http://localhost:8000`.
