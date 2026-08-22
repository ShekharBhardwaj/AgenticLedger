# Agentic Ledger + opencode

opencode supports a per-provider `baseURL` override, which is all the ledger
needs. Start the proxy, add the override, and every call is captured with
full prompts, tool calls, and cache-aware cost.

## Setup

Start the proxy in front of your provider:

```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.anthropic.com python -m agenticledger.proxy
```

In `opencode.json`, point the provider at the proxy:

```json
{
  "provider": {
    "anthropic": {
      "options": { "baseURL": "http://localhost:8000" }
    }
  }
}
```

For OpenAI-compatible providers use `http://localhost:8000/v1`.

## What you get

- Every call in the dashboard at `http://localhost:8000` with tokens, cost,
  latency, tool calls, and thread stitching (step numbers, parent links).
- Budgets: `AGENTICLEDGER_BUDGET_DAILY=10.00` hard-stops spend at the proxy.
- Loop runs: wrap repeated opencode invocations with
  `agenticledger run --max-iterations N -- opencode run "..."` for per-iteration
  cost and stuck-loop detection.

## Attribution

opencode traffic groups by inferred session. For explicit grouping, use
path-segment attribution in the baseURL, no headers needed:

```
http://localhost:8000/r/<run-name>/1
```

Tools that read `OPENAI_BASE_URL`/`ANTHROPIC_BASE_URL` from the environment
don't need the URL surgery — `agenticledger run <name> -- <command>` sets it
for them, and `--project <p>` files the run as it starts. opencode reads its
baseURL from config, so the explicit form above is the one that applies here.
