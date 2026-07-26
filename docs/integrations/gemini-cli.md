# Agentic Ledger + Gemini CLI

Gemini CLI has no base-URL override for Google endpoints, so the integration
is OTel-native: point its telemetry at the ledger.

```bash
gemini --telemetry \
  --telemetry-otlp-endpoint http://localhost:8000 \
  --telemetry-otlp-protocol http
```

GenAI spans become ledger calls: model, tokens, cost, session grouping via
`gen_ai.conversation.id`, and error status. OTel capture is metadata-level —
no prompt bodies.

**Protocol note:** the ledger accepts the OTLP *JSON* encoding over HTTP. If
your exporter emits protobuf-over-HTTP, put a small OpenTelemetry Collector
in between (OTLP receiver → `otlphttp` exporter with `encoding: json`). See
the [integrations index](README.md#otlp-protocol-note) for the snippet.
