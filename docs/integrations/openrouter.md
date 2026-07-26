# Agentic Ledger + OpenRouter

Run the ledger in front of OpenRouter and keep one ledger across every model
it routes:

```bash
AGENTLEDGER_UPSTREAM_URL=https://openrouter.ai/api python -m agentledger.proxy
```

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-openrouter-key",
)
```

OpenRouter model ids like `anthropic/claude-3.5-sonnet` are normalized and
priced at the underlying model's rate. Unknown models log a warning with the
exact `AGENTLEDGER_PRICING` override to add.
