# Integrations

> **Starting the proxy.** Every guide below shows an inline
> `AGENTICLEDGER_UPSTREAM_URL=... python -m agenticledger.proxy` command,
> which still works exactly as written. The comfortable equivalent is
> `agenticledger init` (writes a config file) then `agenticledger start`
> (background — the terminal stays yours and closing it doesn't stop the
> proxy). Environment variables always override the config file, so mix
> freely. See the [Configuration section](../../README.md#configuration).

One guide per framework. Two integration styles:

- **Base URL (full capture):** the agent's traffic flows through the proxy —
  full prompts, tool calls, cache-aware cost, budgets, and loop guards.
- **OTLP (metadata):** OTel-native tools export spans to the ledger — model,
  tokens, cost, latency, session grouping. No message bodies.

| Framework | Style | Guide |
|---|---|---|
| Claude Code | base URL (+ OTel logs for the on-machine audit trail) | [claude-code.md](claude-code.md) |
| Codex CLI | base URL or OTel | [codex-cli.md](codex-cli.md) |
| opencode | base URL | [opencode.md](opencode.md) |
| OpenClaw | base URL (provider override) | [openclaw.md](openclaw.md) |
| BMAD-METHOD | rides its host tool; personas auto-detected | [bmad.md](bmad.md) |
| LangGraph / LangChain | client base_url | [langgraph.md](langgraph.md) |
| CrewAI | client base_url | [crewai.md](crewai.md) |
| OpenAI Agents SDK | client base_url | [openai-agents.md](openai-agents.md) |
| Gemini CLI | OTLP | [gemini-cli.md](gemini-cli.md) |
| AutoGen / AG2 | base_url or OTLP | [autogen.md](autogen.md) |
| Pydantic AI | base_url or OTLP | [pydantic-ai.md](pydantic-ai.md) |
| Vercel AI SDK | OTLP (or provider baseURL) | [vercel-ai-sdk.md](vercel-ai-sdk.md) |
| LiteLLM | upstream gateway or library base_url | [litellm.md](litellm.md) |
| OpenRouter | upstream gateway | [openrouter.md](openrouter.md) |
| LM Studio | local upstream (fully offline stack) | [lm-studio.md](lm-studio.md) |

## OTLP protocol note

The ledger accepts both OTLP/HTTP encodings on `/v1/traces` and `/v1/logs`:
**JSON** always, and **protobuf** when installed with the `[otel]` extra
(`pip install "agentic-ledger[otel]"` — the Docker image includes it).
That covers JavaScript tools (JSON) and Python SDKs (protobuf) directly.
Exporters speaking **gRPC** should switch to HTTP
(`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`), or bridge through a minimal
OpenTelemetry Collector:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
      http:
exporters:
  otlphttp:
    endpoint: http://localhost:8000
    encoding: json
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [otlphttp]
    logs:
      receivers: [otlp]
      exporters: [otlphttp]
```
