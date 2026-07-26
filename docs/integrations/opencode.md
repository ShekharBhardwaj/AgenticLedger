# Agentic Ledger + opencode

opencode supports a per-provider `baseURL` override, which is all the ledger
needs. Start the proxy, add the override, and every call is captured with
full prompts, tool calls, and cache-aware cost.

## Setup

Start the proxy in front of your provider:

```bash
AGENTLEDGER_UPSTREAM_URL=https://api.anthropic.com python -m agentledger.proxy
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
- Budgets: `AGENTLEDGER_BUDGET_DAILY=10.00` hard-stops spend at the proxy.
- Loop runs: wrap repeated opencode invocations with
  `agentledger run --max-iterations N -- opencode run "..."` for per-iteration
  cost and stuck-loop detection.

## Attribution

opencode traffic groups by inferred session. For explicit grouping, use
path-segment attribution in the baseURL, no headers needed:

```
http://localhost:8000/r/<run-name>/1
```
