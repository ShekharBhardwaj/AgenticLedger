# Agentic Ledger + Codex CLI

Two routes; the proxy route captures full request/response bodies.

## Route 1: proxy (recommended)

Start the proxy in front of OpenAI, then define a model provider with a
`base_url` of `http://localhost:8000/v1` in `~/.codex/config.toml` and select
it with `model_provider`. Every call is captured with prompts, tool calls,
and cost.

```bash
AGENTLEDGER_UPSTREAM_URL=https://api.openai.com python -m agentledger.proxy
```

## Route 2: OTel

Codex's `[otel]` section in `~/.codex/config.toml` can export telemetry.
Point it at the ledger's OTLP endpoint (`http://localhost:8000`) using the
JSON encoding. OTel-only capture is metadata-level (model, tokens, cost,
latency) — no message bodies.

## Guardrails

Budgets and loop detection apply on the proxy route:
`AGENTLEDGER_BUDGET_DAILY=10.00`, `AGENTLEDGER_LOOP_ACTION=block`.
