# Agentic Ledger + Vercel AI SDK

The AI SDK emits OTel spans via `experimental_telemetry`. The JS OTLP HTTP
exporter uses the JSON encoding, which the ledger accepts directly.

```ts
import { generateText } from "ai";

const result = await generateText({
  model: openai("gpt-4o"),
  prompt: "...",
  experimental_telemetry: { isEnabled: true },
});
```

Configure the exporter at the ledger:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000
```

GenAI spans become ledger calls (model, tokens, cost, latency). For full
prompt/response capture instead, point the provider's `baseURL` at the proxy:

```ts
import { createOpenAI } from "@ai-sdk/openai";
const openai = createOpenAI({ baseURL: "http://localhost:8000/v1" });
```
