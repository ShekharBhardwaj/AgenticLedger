# Agentic Ledger + Claude Code

The flagship integration: one environment variable, zero code changes, and
the ledger understands Claude Code traffic natively.

## Setup

```bash
AGENTICLEDGER_UPSTREAM_URL=https://api.anthropic.com python -m agenticledger.proxy
```

```bash
export ANTHROPIC_BASE_URL=http://localhost:8000
claude
```

## What happens automatically

- **Real sessions.** Calls are fingerprinted and grouped under the session's
  actual UUID (the same id `claude --resume` shows), tagged
  `framework=claude-code`.
- **True costs.** Prompt-cache reads and writes are captured and priced with
  Anthropic's convention - cache traffic is most of a coding session's spend.
- **Thread stitching.** Steps, parent links, and tool pairing; small
  housekeeping calls (titles, summaries) are excluded so step counts stay
  honest; `/compact` re-links instead of breaking the thread.

## The on-machine record, through hooks (works behind a managed gateway)

The proxy sees what Claude Code sends to the model. It cannot see what
Claude Code does on the machine: which tool ran, with what input, what
came back, how long it took, whether it failed, what permission was asked
or denied and under which rule, when a sub-agent started and stopped,
when the context was compacted. Claude Code reports all of that through
hooks, and the ledger records them as steps on the trace's **Timeline**:

```bash
agenticledger connect claude-code --hooks
```

That writes twelve http hooks into your user settings
(`~/.claude/settings.json`), each posting to
`http://localhost:8000/v1/hooks/claude-code`. Existing hooks are kept, the
ledger's own entries are marked and replaced on re-run, and
`--remove-hooks` takes them out. The hook's session id is the same id the
proxy records for that session's model calls, so the trace's Timeline
shows both halves as one tree: each tool run hangs off the model call
that asked for it (by its tool_use id), sub-agents fold open to their own
steps, and a turn groups everything one prompt set off. Every hook gets an empty 200;
the ledger never blocks or rewrites what the agent does.

Hooks never touch the model path. When IT pins `ANTHROPIC_BASE_URL` to a
company gateway and the proxy cannot sit in front of it, the hooks still
deliver the full on-machine record, and `telemetry_calls` (below) adds
the per-call cost and tokens. If your ledger has an ingest key, export
`AGENTICLEDGER_INGEST_KEY` in the shell that starts Claude Code; the hook
headers read it from there and the settings file never holds it. In a
managed environment, `allowedHttpHookUrls` must permit the ledger's URL.

## Optional: the on-machine audit trail through telemetry

The proxy sees LLM calls; Claude Code's telemetry sees what ran on the
machine. Send both to the ledger:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000
```

Tool executions (name, duration, success) land in the same sessions at
`GET /api/sessions/{id}/tools`.

## When the proxy cannot be in the path

On a company laptop, Claude Code's base URL often lives in the IT-managed
settings file (`/Library/Application Support/ClaudeCode/managed-settings.json`),
which overrides the environment, your own settings and the ledger's run
wrapper. The agent then answers while the ledger sees nothing. You cannot
route around a managed setting, and should not try; but Claude Code's
own telemetry is yours to point anywhere, and the ledger can record from
it:

```bash
agenticledger config set proxy.telemetry_calls true
agenticledger stop && agenticledger start
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:8000
```

Every `claude_code.api_request` and `api_error` event becomes a call in
the ledger: model (Bedrock ids recognised), tokens, cache tokens, the
cost Claude Code was charged, latency, and the session, with tool
executions beside them. What this mode cannot give you is stated plainly:
no prompt or response content (the events carry none), and no refusals
(the ledger is not in the path, so budgets and stops cannot act). Loop
Lens, Sessions, Reports and notifications work on the record as it is.
Leave `telemetry_calls` off wherever the proxy is in the path, or every
call is counted twice.

## Loops and guardrails

```bash
agenticledger run overnight --max-iterations 50 --budget 25 -- claude -p "$(cat PROMPT.md)"
```

`AGENTICLEDGER_BUDGET_DAILY`, `AGENTICLEDGER_LOOP_ACTION=block`, and
`AGENTICLEDGER_COMPLETION_PROMISE` turn an overnight loop into something you
can trust unattended. See the Loop Lens at `http://localhost:8000`.
