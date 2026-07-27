# Agentic Ledger + CrewAI

CrewAI's `LLM` accepts a `base_url` — that's the whole integration.

```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com python -m agenticledger.proxy
```

```python
from crewai import LLM

llm = LLM(
    model="gpt-4o",
    base_url="http://localhost:8000/v1",
    extra_headers={
        "x-agenticledger-session-id": "crew-run-1",
        "x-agenticledger-agent-name": "researcher",  # one per crew agent
    },
)
```

Give each crew member its own `LLM` with its own agent name and the Flow view
renders your crew as a DAG with per-agent cost. Without headers, calls are
still captured and thread-stitched automatically.

Budgets cap a runaway crew at the proxy: `AGENTICLEDGER_BUDGET_SESSION=2.00`.
