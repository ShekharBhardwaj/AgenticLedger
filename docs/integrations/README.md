# Integrations

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

## OTLP protocol note

The ledger accepts the OTLP **JSON** encoding over HTTP (`/v1/traces`,
`/v1/logs`). JavaScript-based tools (Claude Code, Vercel AI SDK) emit JSON
natively. Python SDKs emit protobuf; bridge them with a minimal
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
