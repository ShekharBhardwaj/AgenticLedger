# Agentic Ledger + OpenRouter

Run the ledger in front of OpenRouter and keep one ledger across every model
it routes:

```bash
AGENTICLEDGER_UPSTREAM_URL=https://openrouter.ai/api python -m agenticledger.proxy
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
exact `AGENTICLEDGER_PRICING` override to add.

## Decisions API (Jev and other decision models)

OpenRouter's Decisions endpoint (`/api/alpha/decisions`) is a different
wire from chat: a state and a set of questions go in, answers with a
choice and a confidence come out. The ledger records it like any other
model call. The questions become the system prompt, the state the user
message, the answers the reply, and the cost is the one OpenRouter
charged, taken from the response rather than estimated.

The OpenRouter SDK takes the ledger's address per call:

```python
from openrouter import OpenRouter

client = OpenRouter(api_key=openrouter_api_key)
response = client.alpha.decisions.create(
    model="typesafe/jev-1.13",
    state=state,
    questions=questions,
    server_url="http://localhost:8010",       # the ledger; it forwards to OpenRouter
)
```

Run and session ids ride the same headers as every other wire
(`x-agenticledger-run-id`, `x-agenticledger-session-id`), or the
`/r/<run>/<iteration>` prefix on the server URL.

## Naming your agents

Every agent on the ledger gets a name, in this order: the
`x-agenticledger-agent-name` header; OpenRouter's own `X-Title` header
(the OpenAI SDK's `default_headers`, or `x_open_router_title` on the
OpenRouter SDK); otherwise `unnamed-<fingerprint>`, derived from the
agent's instructions, so two headerless agents with different jobs are
still two rows in the reports and two sessions in the record.
`HTTP-Referer` fills the app id and OpenAI's `user` field fills the user.
Calls recorded before the ledger learned this get their names from
`POST /api/redetect`.
