# Coming from Helicone or LangSmith

You already believe in LLM observability, so this page skips the pitch
and does the translation: what your current concepts are called here,
the two-line switch, what you gain, and honestly, what you lose.

## The two-line switch

**From Helicone** (you already route through a proxy, so this is a
base-URL swap):

```bash
agenticledger start
# wherever you set https://oai.helicone.ai/v1 or the Anthropic gateway:
export OPENAI_BASE_URL=http://localhost:8000/v1
export ANTHROPIC_BASE_URL=http://localhost:8000
```

Your API keys keep flowing through untouched, exactly as before; the
ledger never stores them. Requests now terminate their observability
hop on your machine instead of a vendor's.

**From LangSmith** (SDK tracing, so the switch is adding the proxy and
removing the tracer):

```bash
agenticledger start
export ANTHROPIC_BASE_URL=http://localhost:8000   # or OPENAI_BASE_URL
unset LANGCHAIN_TRACING_V2                         # optional; they can coexist
```

No SDK, no callbacks, no wrapper imports. If your stack emits
OpenTelemetry instead, point the OTLP exporter at the ledger and keep
your instrumentation: see the OTel section of the README.

## Concept map

| You say | The ledger says | Notes |
|---|---|---|
| Helicone custom properties | `x-agenticledger-*` headers | user, app, environment, agent name; all optional |
| Helicone sessions | sessions | auto-detected for Claude Code and friends, header-set otherwise |
| Helicone caching | your provider's caching | the ledger measures it (the cache audit) rather than proxying its own |
| LangSmith project | project | bind an app id and sessions file themselves |
| LangSmith trace / run tree | session, and its calls | reconstructed from the wire, no instrumentation |
| LangSmith monitoring dashboards | Reports | spend per day, model mix, latency percentiles, cache savings |
| rate limits / spend alerts | budgets and ceilings | enforced in the request path, not alerts after the fact |

## What you gain

- **Local-first.** Prompts, replies, and spend never leave your
  machine. There is no vendor copy of your traffic to think about.
- **Refusal, not just alerts.** Budgets, per-run cost ceilings, and a
  kill switch enforced by the proxy before a call reaches the
  provider. A dashboard can show you a runaway loop; only something in
  the request path can stop one.
- **Loop awareness.** Fresh-context loops are recognized, grouped,
  flagged when stuck, and priced per iteration; two runs diff side by
  side.
- **The cache audit.** The repeat-discount a run was eligible for and
  did not receive, with the reason and the one-line fix.

## What you lose, honestly

- **Evals and datasets.** LangSmith's eval suites, annotation queues,
  and prompt playground have no equivalent here. If those are central
  to your workflow, keep LangSmith for them; the tools coexist fine.
- **Hosted team dashboards.** The ledger's dashboard runs where the
  ledger runs. Team access is pairing links and team cards, not a
  SaaS org with SSO (that posture is deliberately downstream).
- **Helicone's gateway extras.** Provider fallback routing and
  vendor-side caching are not this product; the ledger forwards your
  bytes untouched, always.

## Keeping both

Nothing about the ledger conflicts with SDK tracers: the proxy sits on
the wire while tracers sit in your code. Teams migrating gradually run
both for a week and compare numbers; if the ledger's costs disagree
with your provider bill, that is a bug we want reported.
