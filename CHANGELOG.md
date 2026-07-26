# Changelog

All notable changes to Agentic Ledger are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-07-26

The agentic-loop release. Consolidates the 0.3.0 pre-release line
(alpha.1 through beta.1, all detailed below) into the first stable release
under the Agentic Ledger name. Headlines:

- **Loop engine** — threads, runs, iterations, and stuck-loop detection
  inferred from raw traffic, with an in-path circuit breaker.
- **Loop Lens web app** at `/` — runs, cost per iteration, flag explanations,
  Flow DAG, and a Trace waterfall built from real parent links.
- **Costs you can bill against** — cache-aware pricing per provider
  convention, gateway-tolerant model matching, loud warnings for unpriced
  models.
- **`agentledger run`** — the observable, budgeted loop runner, with
  completion promises and a morning-report webhook.
- **Zero-config Claude Code support** and OTLP ingest for OTel-native tools;
  fourteen framework guides under docs/integrations/.

### Added
- Per-framework integration guides for all fourteen supported tools under
  docs/integrations/ with an index and an OTLP protocol note (JSON encoding;
  Collector bridge snippet for protobuf-only Python SDKs). Initially:
  guides for opencode, LangGraph/LangChain, and the OpenAI Agents
  SDK (docs/integrations/), plus an MCP registry manifest (server.json) as a
  PyPI package listing with the ownership marker in the README; the release
  workflow re-publishes the registry listing on every tag via GitHub OIDC.

## [0.3.0-beta.1] - 2026-07-26

### Added
- **Gateway-tolerant pricing + unpriced-model visibility.** Model-id matching
  now unifies dots and dashes, so OpenRouter (`anthropic/claude-3.5-sonnet`),
  Bedrock (`us.anthropic.claude-3-5-sonnet-20241022-v2:0`), and dated ids all
  price at the underlying model's rate. Unknown models log one loud warning
  (with the exact `AGENTLEDGER_PRICING` override to add) instead of silently
  recording unknown cost forever. Table refreshed: GPT-5 family, Claude
  4.5-generation models, Gemini 2.5 Flash, and o3's mid-2025 reprice.

## [0.3.0-alpha.4] - 2026-07-26

### Added
- **Flow + Trace in the web app — full parity, and the app is now the default.**
  The Sessions view gains a Calls / Flow / Trace switcher: Flow renders the
  agent handoff DAG (per-agent cost, calls, latency; cycles as dashed
  back-edges with counts), and Trace renders the waterfall with parent
  connectors from **real thread links** (`prev_action_id`) instead of the
  classic view's timestamp inference. `/` now serves the web app (falling back
  to the classic dashboard on source checkouts without a Node build);
  the classic dashboard moved to `/classic`; `/app` still works.
- **Loop-engine robustness.** (1) Claude Code's small housekeeping calls
  (session titles/summaries, identified by tiny `max_tokens` on detected
  claude-code traffic) are captured but excluded from loop inference — they
  were inflating step counts and resetting repeat-streak detection. (2)
  Context compaction is tolerated: a rewritten history carrying Claude Code's
  continuation marker re-links to its thread with an informational
  `context_compaction` flag instead of starting a phantom new thread.
  Flagged-call counts now include only problem flags (`repeat_tool_call`,
  `step_budget_exceeded`) — informational flags don't turn things amber.

## [0.3.0-alpha.3] - 2026-07-26

Alpha-tester feedback release — everything here came out of first-contact
dogfooding.

### Added
- **Flag drill-down.** New `GET /api/runs/{run_id}/flags` and a "Flags — what
  happened and why" section in the Loop Lens: each flagged call gets a
  plain-English explanation, the offending tool call and arguments, and an
  "Open session" jump into the Sessions view. Flag badges carry explanatory
  tooltips everywhere; `completion_promise` renders green (it's the good flag).
- **Call tiles show tools and interaction types.** Collapsed call cards show
  which tools the call issued, plus H2A / A2T / A2A / A2H indicators (human→agent,
  agent→tool, agent→agent handoff, agent→human) derived from captured data.
- **Abstract raccoon mark** in the web app's top bar and favicon — inline SVG,
  theme-aware, no image assets.
- **Fresh-install CI job**: builds the wheel, installs it with `--pre` into a
  clean venv, boots the proxy, and checks `/health` and `/app` — the exact
  first-contact path that broke in alpha.1.
- **Troubleshooting section** in the README (Rosetta/arch mismatch, port
  conflicts, expired OAuth, source installs without the web-app build).

## [0.3.0-alpha.2] - 2026-07-26

### Fixed
- **Fresh `pip install --pre` was broken.** `--pre` applies to dependency
  resolution too, so the unbounded `httpx>=0.27` requirement pulled httpx's
  experimental `1.0.dev3` build, which removes `AsyncClient` and crashed the
  proxy at startup. httpx is now capped below its 1.0 dev builds
  (`<1.0.dev0` — PEP 440 orders `1.0.devN` before `1.0`, so a plain `<1.0`
  cap would not have excluded them under `--pre`).

## [0.3.0-alpha.1] - 2026-07-25

### Upgrade notes
- **Costs for cache-heavy traffic (e.g. Claude Code) are now accurate.** Prompt-cache
  reads/writes were previously ignored, so `tokens_in` could reflect under 1% of the
  real context and `cost_usd` was significantly under-reported — which also meant
  budgets and alerts under-enforced. Newly captured calls will show higher, correct
  costs. Three new columns are added automatically: `cache_read_tokens`,
  `cache_write_tokens`, `thinking`.

### Added
- **Prompt-cache accounting.** Anthropic `cache_read_input_tokens` /
  `cache_creation_input_tokens` (native and via LiteLLM) and OpenAI
  `prompt_tokens_details.cached_tokens` / Responses `input_tokens_details.cached_tokens`
  are captured, stored, and priced with the correct provider convention (Anthropic:
  reads 0.1×, writes 1.25× on top of input; OpenAI: cached subset re-billed at 0.5×).
  Pricing overrides accept an extended `[input, output, cache_read, cache_write]` form.
- **Extended-thinking capture.** Anthropic `thinking` blocks (streaming
  `thinking_delta` and non-streaming) are stored in a new `thinking` field, covered by
  capture levels and redaction like all other content.
- **OpenAI Responses API streaming capture.** `response.*` SSE events (OpenAI Agents
  SDK et al.) are now reconstructed from the terminal `response.completed` /
  `response.incomplete` / `response.failed` event — previously they were misrouted to
  the Anthropic reconstructor and captured as an empty response.
- **Errored and interrupted streams are captured.** Streaming calls that return a
  non-200 are recorded with the upstream status and error body; client disconnects
  mid-stream record a partial capture (`error_detail: partial: client disconnected…`);
  mid-stream provider error events (e.g. Anthropic `overloaded_error` after a 200) are
  surfaced as `stream_error: …` instead of posing as clean calls.

- **`count_tokens` capture.** `POST /v1/messages/count_tokens` is now recorded
  (previously it was forwarded by the catch-all but invisible in the ledger). Captured
  calls are marked `stop_reason="count_tokens"` with the counted value in `content`,
  carry zero cost and no `tokens_in`/`tokens_out` — so session cost/token aggregates
  are unaffected — and are exempt from rate-limit and budget enforcement (the
  endpoint is free).
- **Zero-config agent detection.** Well-known clients are fingerprinted when no
  `x-agentledger-*` headers are present: Claude Code traffic is tagged
  `framework=claude-code` / `agent_name=claude-code` and grouped under its **real
  session UUID** (extracted from `metadata.user_id`, the same id `claude --resume`
  shows) instead of the shared `auto-<date>` bucket. LiteLLM clients are tagged
  `framework=litellm`. Explicit headers always win. New `framework` column and
  `x-agentledger-framework` header.

- **Loop engine (v1).** Raw traffic is stitched into agentic structure with no
  client cooperation:
  - *ReAct threads* — message-chain prefix matching links calls into threads with
    `thread_id`, `step_index`, `turn_index`, and `prev_action_id` columns.
  - *Runs* — `x-agentledger-run-id`/`x-agentledger-iteration` headers, or automatic
    grouping of fresh-context loop iterations (new session, same system-prompt hash,
    within `AGENTLEDGER_LOOP_RUN_GAP_SECONDS`) — the Ralph-loop pattern. New
    `GET /api/runs` aggregation endpoint.
  - *Stuck-loop detection* — consecutive identical tool calls
    (`AGENTLEDGER_LOOP_REPEAT_THRESHOLD`, default 3) and step budgets
    (`AGENTLEDGER_LOOP_MAX_STEPS`) raise `loop_flags` on the call, fire a
    `loop_flag` webhook alert, and — with `AGENTLEDGER_LOOP_ACTION=block` — return
    HTTP 429 (`loop_detected`) before the next call burns more budget.
  - *Completion promises* — `AGENTLEDGER_COMPLETION_PROMISE` (regex) flags the
    response that declares a loop done; `GET /api/runs/{run_id}` derives run
    status (`running` / `flagged` / `complete`) for loop runners to poll.
  - *Path-segment attribution* — `/r/<run_id>/<iteration>/v1/...` base URLs tag
    calls for clients that can't send custom headers.
- **`agentledger run` loop runner (new CLI).** Wraps any agent command in an
  observable, budgeted Ralph-style loop: per-iteration base-URL attribution,
  stops on completion promise / `--budget` USD ceiling / `--max-iterations`,
  prints an end-of-run cost summary. Installed as the `agentledger` console
  script.
- **MCP run tools.** `list_runs` and `get_run_status(run_id)` join the MCP
  server, so agents can inspect their own loops and self-terminate.
- **Derived tool executions.** The proxy pairs every tool call issued by call N
  with the result fed back in call N+1, yielding a `tool_executions` table with
  tool name, arguments, wall-clock latency (the gap between the calls), and
  error status (from Anthropic `is_error`). Served at
  `GET /api/sessions/{session_id}/tools`.
- **OTLP ingest.** `POST /v1/traces` accepts the OTLP/HTTP JSON encoding and
  maps GenAI-semconv spans into ledger calls (model, tokens, cost, session from
  `gen_ai.conversation.id`, agent, framework from `service.name`, error status)
  with deterministic ids so re-exported batches never duplicate. Unlocks Gemini
  CLI, Codex `[otel]`, AutoGen/AG2, Pydantic AI, and Vercel AI SDK with one env
  var; `/v1/logs` + `/v1/metrics` are acknowledged. Honors `AGENTLEDGER_INGEST_KEY`.
- **BMAD-METHOD detection + guide.** BMAD persona prompts are fingerprinted:
  calls tagged `framework=bmad` with `agent_name=bmad:sm|dev|qa|architect|analyst|pm|po|ux`
  (data-driven table in detect.py). New docs/integrations/bmad.md and
  docs/integrations/openclaw.md recipes.
- **New web app at `/app` (React SPA).** Loop Lens — runs with status badges,
  a cost-per-iteration chart, and per-iteration breakdowns (calls, tokens,
  cache reads, flags, errors, via new `GET /api/runs/{run_id}/iterations`) —
  plus a Sessions browser with expandable call cards (response, thinking, tool
  calls, cache tokens, loop flags) and live WebSocket updates. Built from
  `dashboard-app/` (Vite + React), shipped inside the wheel; source installs
  without the build keep the classic dashboard at `/`. The SPA attaches the
  api key to its requests (the classic dashboard's fetches didn't).

### Changed
- The request body is parsed once per call instead of two-to-three times — a real
  saving on multi-megabyte coding-agent contexts.

## [0.2.0] - 2026-06-19

### Upgrade notes
- **Postgres:** on first connect, `session_id` is migrated in place from `UUID` to `TEXT`
  (`ALTER COLUMN`). This is automatic and safe — no action required — but it is a schema change.
- **Audit log is on by default:** a new `audit_log` table is created and a row is written for
  each sensitive read/export/delete. Set `AGENTLEDGER_AUDIT_LOG=0` to disable.
- **Costs for `*-mini`/`*-nano` models are now correct** (previously over-reported, e.g.
  `gpt-4o-mini` was billed at `gpt-4o` rates). Newly captured costs will be lower/accurate.

### Security
- **The live-events WebSocket (`/ws`) now requires auth.** When `AGENTLEDGER_API_KEY`
  is set, `/ws` accepts the same credentials as the dashboard and API (master key or
  API token via `?api_key=`/`?token=`, `Authorization: Bearer`, or
  `x-agentledger-token`) and closes unauthenticated connects with code 1008.
  Previously any client could connect and observe live call metadata (action ids,
  session ids, status codes). The dashboard forwards its page credential to the
  socket automatically.
- Optional **proxy-ingest key** (`AGENTLEDGER_INGEST_KEY`): when set, the proxy only
  forwards requests that carry a matching `x-agentledger-ingest-key`, closing the open
  relay. Off by default to preserve the zero-config quickstart, with a loud startup
  warning when unset. The key (and the dashboard `x-agentledger-api-key`) are stripped
  before forwarding upstream.
- API key comparison is now constant-time (`hmac.compare_digest`), removing a timing
  oracle that could leak the key.
- Compliance export integrity is now honest about its guarantee. The default remains a
  SHA-256 **checksum** (catches corruption, not tampering — anyone who edits the calls
  can recompute it). Set `AGENTLEDGER_EXPORT_HMAC_KEY` to emit a tamper-evident keyed
  `hmac-sha256` tag instead. README/docs wording corrected (no longer calls the default
  checksum a "signed" trail).

### Added
- **Audit log.** Records who did what to which target — session views, searches, exports,
  deletes, token create/revoke, and erasure — with the acting principal (master / token /
  open, role, name) and client IP. Viewable at `GET /api/audit` (admin). On by default;
  `AGENTLEDGER_AUDIT_LOG=0` disables it.
- **Right-to-erasure.** `DELETE /api/users/{user_id}` (admin) deletes all captured calls for
  a user (`Store.delete_user` on SQLite and Postgres) and records the action in the audit log.
- **Data retention / TTL.** `AGENTLEDGER_RETENTION_DAYS` runs a background worker that
  periodically deletes captured calls older than the configured window (new
  `Store.purge_older_than` on SQLite and Postgres). Unset = keep forever.
- **Data governance — capture levels & redaction.** `AGENTLEDGER_CAPTURE_LEVEL=metadata`
  stores only metrics/metadata (model, tokens, cost, latency, agent, status) and drops
  prompts, responses, and tools. `AGENTLEDGER_REDACT` (`all` or a comma list of
  `email,ssn,credit_card,ip,api_key`) plus `AGENTLEDGER_REDACT_PATTERNS` (custom regexes)
  replace PII/secrets with `[REDACTED:<label>]` before anything is stored, traced, or
  broadcast. Governance transforms only the captured copy — the agent always receives the
  real, unmodified upstream response. (In async mode, redaction runs off the hot path.)
- **Async ingestion (opt-in)** via `AGENTLEDGER_ASYNC_CAPTURE`: post-call persistence
  (store write, OTel span, dashboard broadcast, alerts) runs on a bounded background
  worker so it never adds latency to the agent's call. When the queue is full (default
  10000, `AGENTLEDGER_CAPTURE_QUEUE_MAX`) load is shed and counted rather than blocking.
  Default off — sync mode keeps read-after-write; async mode is eventually consistent.
- **`/metrics`** Prometheus endpoint: captures persisted/dropped (counters), capture queue
  depth, and whether async capture is enabled. Low-cardinality (no per-session labels).
- **Scoped, role-based API tokens** (`viewer` < `editor` < `admin`) as an alternative to
  sharing the master key. Tokens are random secrets shown once at creation; only their
  SHA-256 hash is stored, and each can be given an expiry or revoked. Managed via
  `POST/GET/DELETE /api/tokens` (admin only); presented as `Authorization: Bearer`,
  `x-agentledger-token`, or `?token=`. Read endpoints require `viewer`, session delete
  requires `editor`, token management requires `admin`. Auth is enforced only when
  `AGENTLEDGER_API_KEY` is set (the master key grants `admin` and bootstraps tokens).
- `/readyz` readiness probe (runs a store `SELECT 1`; returns 503 when the store is
  unreachable) so load balancers and k8s can gate traffic. `/health` remains a pure
  liveness check that never touches the store.
- A `capture_dropped` counter: when a call is served upstream but its record can't be
  persisted, the proxy still fails open, but the loss is now logged at WARNING and
  counted (surfaced via `/readyz`) instead of being silently swallowed.

- Comprehensive automated test suite (`tests/`) with ~280 tests covering request/response
  normalization, streaming SSE reconstruction, pricing, rate limiting, budgets, the storage
  layer, the proxy request path, compliance export, the MCP server, and webhook alerts.
- Shared pytest harness (`tests/conftest.py`) with a mock-upstream proxy fixture and
  provider wire-format builders — tests are fully offline and deterministic.
- CI now runs `ruff` linting and `pytest` with coverage (gated at 70%) across Python
  3.10 / 3.11 / 3.12.
- CodeQL security scanning and Dependabot dependency updates.
- Community health files: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
  pull-request template, and issue templates.
- `ruff` configuration and a `[dev]` tooling extra (`pytest-cov`, `ruff`).

### Fixed
- **Postgres data loss:** the production Postgres backend typed `session_id` as `UUID`
  and cast every id with `uuid.UUID(...)`. Agent session ids are arbitrary strings — the
  proxy mints `auto-<date>` when no header is supplied, and users pass human-readable run
  names — so any non-UUID session id raised and, because the proxy save path is fail-open,
  was **silently dropped**. `session_id` is now `TEXT` (matching SQLite); existing databases
  are migrated in place on connect (`ALTER COLUMN session_id TYPE TEXT`). Added a Postgres
  regression test suite (runs in CI against a Postgres service; skipped locally without one).
- **Cost computation:** `compute_cost` now matches the longest (most specific) pricing
  pattern instead of the first substring match. Previously `gpt-4o-mini`, `o1-mini`,
  `o3-mini`, `gpt-4.1-mini`, and `gpt-4.1-nano` were each priced at their parent model's
  (much higher) rate — e.g. `gpt-4o-mini` was billed at `gpt-4o` rates (~16× too high on
  input). Captured costs for these models are now correct.

### Changed
- Rate-limiter memory is now bounded: idle per-session/agent/user windows are evicted as
  they age out, with a sweep guarding against pathological key cardinality. Corrected the
  docstring that overclaimed cross-process safety (limits are enforced per process).
- Stopped tracking the runtime SQLite database (`agentledger.db`) in git and added `*.db`
  to `.gitignore`. The database is a runtime artifact and may contain captured prompt data.

<!--
## [0.1.7] - YYYY-MM-DD
Older releases predate this changelog. See the GitHub Releases page for history:
https://github.com/ShekharBhardwaj/AgenticLedger/releases
-->

[Unreleased]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.2.0...v0.3.0
[0.3.0-beta.1]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0-alpha.4...v0.3.0-beta.1
[0.3.0-alpha.4]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0-alpha.3...v0.3.0-alpha.4
[0.3.0-alpha.3]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0-alpha.2...v0.3.0-alpha.3
[0.3.0-alpha.2]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.3.0-alpha.1...v0.3.0-alpha.2
[0.3.0-alpha.1]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.2.0...v0.3.0-alpha.1
[0.2.0]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.1.7...v0.2.0
