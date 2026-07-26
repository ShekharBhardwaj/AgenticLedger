# Agentic Ledger + Pydantic AI

## Option 1: base URL (full capture)

```python
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider

model = OpenAIModel(
    "gpt-4o",
    provider=OpenAIProvider(base_url="http://localhost:8000/v1"),
)
```

Anthropic models: point the provider at `http://localhost:8000`.

## Option 2: OTel (metadata)

Pydantic AI is OTel-native and works with any OTLP backend. The ledger
accepts the OTLP JSON encoding; Python's exporter emits protobuf, so bridge
with a Collector — see the [integrations index](README.md#otlp-protocol-note).

The proxy route also unlocks budgets, rate limits, and stuck-loop breaking.
