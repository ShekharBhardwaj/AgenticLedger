# Agentic Ledger + LangGraph / LangChain

One argument on your model client routes every call through the ledger.

## Setup

```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com python -m agenticledger.proxy
```

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4o",
    base_url="http://localhost:8000/v1",
    default_headers={
        "x-agenticledger-session-id": "graph-run-1",
        "x-agenticledger-agent-name": "researcher",   # one per graph node
    },
)
```

`ChatAnthropic` works the same way with `base_url="http://localhost:8000"`.

## Multi-node graphs

Give each node its own client with its own `x-agenticledger-agent-name`, and
add `x-agenticledger-handoff-from` / `x-agenticledger-handoff-to` on edges you
want rendered in the Flow DAG. Without any headers, calls are still captured
and stitched into threads by message-prefix inference.

## Guardrails

- `AGENTICLEDGER_BUDGET_SESSION=2.00` caps a single graph run.
- `AGENTICLEDGER_LOOP_ACTION=block` stops a node stuck re-issuing the same
  tool call (LangGraph's recursion_limit catches infinite graphs; the ledger
  catches semantic loops inside a node).
