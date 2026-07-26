# Agentic Ledger + AutoGen / AG2

The AutoGen runtime is OpenTelemetry-instrumented out of the box — no
framework-side code needed. Two options:

## Option 1: base URL (full capture)

AutoGen model clients accept `base_url`; point them at the proxy for full
request/response capture:

```python
from autogen_ext.models.openai import OpenAIChatCompletionClient

client = OpenAIChatCompletionClient(
    model="gpt-4o",
    base_url="http://localhost:8000/v1",
)
```

## Option 2: OTLP (metadata)

Export the runtime's traces to the ledger's OTLP endpoint
(`http://localhost:8000`, JSON encoding). Python's OTLP exporter emits
protobuf, so bridge with a small OpenTelemetry Collector — snippet in the
[integrations index](README.md#otlp-protocol-note).
