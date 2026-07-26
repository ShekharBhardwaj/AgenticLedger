# Agentic Ledger + LiteLLM

LiteLLM plays either role:

## LiteLLM as your gateway (ledger in front)

```bash
AGENTLEDGER_UPSTREAM_URL=http://localhost:4000 python -m agentledger.proxy
```

Point clients at `http://localhost:8000/v1`. Every call through the gateway
is captured; LiteLLM client traffic is auto-tagged `framework=litellm`, and
cache tokens forwarded by LiteLLM are priced correctly.

## LiteLLM as a library

```python
import litellm
litellm.api_base = "http://localhost:8000/v1"
```

Either way the ledger's budgets and loop guards sit in the request path.
