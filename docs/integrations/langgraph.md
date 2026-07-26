# Agentic Ledger + LangGraph / LangChain

One argument on your model client routes every call through the ledger.

## Setup

```bash
AGENTLEDGER_UPSTREAM_URL=https://api.openai.com python -m agentledger.proxy
```

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4o",
    base_url="http://localhost:8000/v1",
    default_headers={
        "x-agentledger-session-id": "graph-run-1",
        "x-agentledger-agent-name": "researcher",   # one per graph node
    },
)
```

`ChatAnthropic` works the same way with `base_url="http://localhost:8000"`.

## Multi-node graphs

Give each node its own client with its own `x-agentledger-agent-name`, and
add `x-agentledger-handoff-from` / `x-agentledger-handoff-to` on edges you
want rendered in the Flow DAG. Without any headers, calls are still captured
and stitched into threads by message-prefix inference.

## Guardrails

- `AGENTLEDGER_BUDGET_SESSION=2.00` caps a single graph run.
- `AGENTLEDGER_LOOP_ACTION=block` stops a node stuck re-issuing the same
  tool call (LangGraph's recursion_limit catches infinite graphs; the ledger
  catches semantic loops inside a node).
