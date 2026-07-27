# Agentic Ledger + OpenAI Agents SDK

The Agents SDK uses an internal OpenAI client. Point a client at the proxy
and hand it to your agents.

## Setup

```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com python -m agenticledger.proxy
```

Simplest, one global client:

```python
from agents import set_default_openai_client
from openai import AsyncOpenAI

set_default_openai_client(AsyncOpenAI(
    base_url="http://localhost:8000/v1",
    default_headers={"x-agenticledger-session-id": "run-1"},
))
```

Per-agent attribution (recommended for multi-agent apps): create one
`AsyncOpenAI` client per agent with its own `x-agenticledger-agent-name` and
`x-agenticledger-handoff-from`/`-to` headers, and pass it via
`OpenAIResponsesModel(model=..., openai_client=client)` as the agent's model.
The Flow view renders the handoff DAG from those headers.

## Notes

- Streaming Responses API traffic is fully reconstructed (tool calls, usage,
  and cached-token accounting included).
- Handoffs, guardrails, and tool spans from the SDK's own tracing can also be
  sent to the ledger's OTLP endpoint: `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000`
  with the http/json protocol.
