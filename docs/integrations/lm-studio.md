# Agentic Ledger + LM Studio

LM Studio's local server is OpenAI-compatible, so it plugs in as a standard
upstream — a fully local stack: local model, local proxy, local ledger.
Nothing leaves your machine.

## Setup

Start LM Studio's server (default port 1234), then put the ledger in front:

```bash
AGENTICLEDGER_UPSTREAM_URL=http://localhost:1234 python -m agenticledger.proxy
```

Point your agent at the proxy as usual:

```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
```

Everything works as with a cloud provider: sessions, tool-call capture,
loop detection and flags, run comparison, prompt drift, streaming.

## Cost shows as empty — that's correct

Local models aren't in the pricing table, so calls record tokens and latency
but no dollar cost (you'll see a one-time "unpriced model" log line). Local
inference is free; the rest of the ledger is unaffected. If you want to
assign a nominal rate anyway (e.g. to compare against cloud pricing):

```bash
AGENTICLEDGER_PRICING='{"qwen": [0.0, 0.0], "llama": [0.0, 0.0]}'
```

## Free replay — including your captured Claude calls

LM Studio doesn't check API keys, so a replay target works with any
placeholder:

```bash
AGENTICLEDGER_REPLAY_OPENAI_URL=http://localhost:1234
AGENTICLEDGER_REPLAY_OPENAI_KEY=lm-studio
```

Now ↻ Replay on **any** captured call — even ones captured from Claude —
re-executes it on your local model at zero cost: the ledger translates the
conversation (tool calls, schemas, system prompt) into the OpenAI wire
format LM Studio speaks. Type the name of any model you have loaded and
compare its answer, speed, and $0 cost against the original side by side.

## Local models that query their own ledger

Recent LM Studio versions can act as an MCP host. Add the ledger's stdio
server to LM Studio's `mcp.json` and a local model can ask about its own
history — sessions, costs, loop status — entirely offline:

```json
{
  "mcpServers": {
    "agenticledger": {
      "command": "agenticledger",
      "args": ["mcp"],
      "env": { "AGENTICLEDGER_DSN": "sqlite:///path/to/agenticledger.db" }
    }
  }
}
```
