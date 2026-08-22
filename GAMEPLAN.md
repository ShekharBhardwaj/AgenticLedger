# Agentic Ledger — The Agentic-Framework Game Plan

*Companion to [ROADMAP.md](ROADMAP.md). The roadmap sequences hardening → governance → tenancy → differentiation. This document answers a different question: what makes Agentic Ledger the observability tool **every agentic framework wants to ship with** — first-class ReAct/Ralph loop support, BMAD-METHOD, OpenClaw, and the coding-agent ecosystem at large.*

---

## The thesis: claim the empty category

**"The local-first flight recorder and control plane for agentic loops."**

Research across the 2026 landscape shows the category is genuinely open:

- **Helicone** — the closest proxy-based peer — entered **maintenance mode in March 2026** after joining Mintlify. The proxy-first niche has no active leader.
- **Ralph loops** (Geoff Huntley's `while :; do cat PROMPT.md | claude-code; done`) have become a mainstream overnight-coding practice with *zero* standard observability. Practitioners describe it as "a loop with better vibes"; one engineer burned through two $400/mo Claude Max subscriptions in days; Geocodio is writing a bespoke TUI (Chief) just to watch their loops. Nobody owns this.
- **BMAD-METHOD** (~51k stars, still accelerating) has **zero built-in telemetry**. Its community's loudest pain is "token hell" — they already talk in cost-per-story and tokens-per-phase vocabulary, and no tool measures it.
- **OpenClaw** (~200k stars, foundation-run) tracks tokens but **has no hard spend cap** — the single most-requested missing feature for an always-on agent — and just lived through a security crisis (tens of thousands of exposed instances, critical CVEs) that creates demand for an egress audit ledger.
- SDK-based incumbents (Langfuse, LangSmith, AgentOps, Braintrust, Phoenix) model loops from *instrumented* spans. None can reconstruct loop structure from raw LLM traffic, and none sits **in the request path** where budgets and circuit breakers can actually *stop* a runaway loop.
- Local-first alternatives (ai-observer, tokentelemetry, disler's hooks dashboards) are OTel-metrics-only or Claude-Code-only. None combines full request/response capture + flow DAG + budgets + MCP self-introspection.

Agentic Ledger already has the hardest part — a transparent zero-code proxy with an unusually rich per-call data model. What's missing is (1) loop semantics, (2) framework awareness, (3) OTel ingestion, (4) a UI worthy of the story, and (5) distribution.

Naming (updated 2026-07-27): the public name is **Agentic Ledger**, matching the PyPI package **agentic-ledger** and the Docker image `ghcr.io/…/agentic-ledger`. As of 0.4.0 the technical slugs match too: `agenticledger` Python module and CLI, `AGENTICLEDGER_*` env vars, `x-agenticledger-*` headers (a hard rename — the original `agentledger` slugs from before the 2026-07-25 rebrand were retired in one breaking cut while the install base was still small). The GitHub repo is `ShekharBhardwaj/AgenticLedger` (renamed 2026-07-25; old URLs auto-redirect).

---

## Pillar 0 — Make capture *true* for coding-agent traffic (prerequisite)

The codebase review found correctness gaps that would sink the whole positioning the first time a Ralph user checks the numbers. These come first because every pillar below builds on them:

| Fix | Why it's blocking | Where |
|---|---|---|
| **Cache-token accounting** — `cache_creation_input_tokens` / `cache_read_input_tokens` are ignored in both streaming and non-streaming paths | Claude Code traffic is cache-dominated; `tokens_in` can be <1% of real context and `cost_usd` is wrong by an order of magnitude. Budgets/alerts silently under-enforce. | `stream.py:124-127`, `normalize.py:168-170`, `pricing.py` (1.25×/0.1× cache rates) |
| **Responses-API SSE misdetection** — any chunk with a `type` key routes to the Anthropic reconstructor, so OpenAI Responses streaming (`type: response.*`) captures as an *empty* response | OpenAI Agents SDK streaming capture is currently broken | `stream.py:24-27` — detect on Anthropic-specific event types; add a Responses reconstructor |
| **Interrupted streams never captured** — client disconnect (Esc in Claude Code) skips the post-loop capture; streaming non-200s are never captured at all | Holes in every loop chain; not even counted in `capture_dropped` | `app.py:750-761` → move capture into `finally` with a `partial` flag; `app.py:734` |
| **Thinking deltas dropped** — `thinking_delta`/`signature_delta` unhandled | Extended-thinking transcripts silently missing | `stream.py:104-109` |
| **Parse-once refactor** — body is `json.loads`'d 2× already (`_is_streaming` + capture); body-based detection would make it 3× on multi-MB bodies | Hot-path latency on 200k-token contexts | `app.py:582, 683, 738` |
| **`count_tokens` capture** — forwarded but invisible | Complete-record story | `app.py:78-81, 581` |
| Mid-stream SSE `error` events unrecognized → truncated calls persist as status 200 | Loop inference misreads these as boundaries | `stream.py` both reconstructors |

Also carry forward the ROADMAP "quick wins" (Postgres auto-session UUID bug, swallowed capture failures, `/ws` auth — the WS is unauthenticated today, `app.py:417-424`). The new positioning is *unattended overnight runs with real money at stake*; trust in the numbers is the product.

---## Pillar 1 — The Loop Engine (ReAct + Ralph as first-class objects)

This is the differentiating build. A proxy that reconstructs loop structure from raw traffic is something **no incumbent does** — and being in the request path means detection can *enforce*, not just observe.

### Data model

Additive columns on `llm_calls` (via the existing `_MIGRATION_COLUMNS` pattern, `store.py:22-37`):
`thread_id`, `step_index`, `turn_index`, `prev_action_id`, `prefix_hash`, `run_id`, `loop_id`, `iteration`, `framework`, `finish_reason` (normalized across OpenAI `finish_reason` / Anthropic `stop_reason`), `delta_message_count`, `context_tokens`.

Two new tables (following the `api_tokens`/`audit_log` idempotent-DDL precedent, `store.py:186-209`):

- **`runs`** — id, session_id, name, framework, kind (`ralph` | `react` | `bmad-story` | …), status (`running`/`complete`/`stuck`/`killed`), started/ended, budget, outcome metadata, state-file snapshots. Define its relationship to `session_id` explicitly (a run *groups* sessions/iterations; today "run" semantics informally squat on `session_id` — `app.py:841`).
- **`tool_executions`** — derived: `tool_call_id`, tool name, args hash, `issued_by_action_id`, `resolved_by_action_id`, latency (gap between response N and request N+1), `is_error`, result preview. Unresolved after timeout = "abandoned tool call" (itself a failure signal).

### Inference engine (`agenticledger/proxy/loops.py`)

Runs at save-time — before `apply_capture_policy` empties messages (`app.py:240`), pre-redaction so hashes are stable; in async-capture mode the cost is fully off the hot path, and its single-worker FIFO preserves ordering:

1. **Chain stitching (ReAct)** — per-message canonical digests + a rolling per-session chain hash (hash only appended messages — never re-hash full history; O(Δ) not O(n²)). Call N+1 extending call N's messages + assistant reply + tool results ⇒ same `thread_id`, `step_index++`.
2. **Tool pairing** — match `tool_calls` ids in call N against `tool`/`tool_result` messages in call N+1 ⇒ synthesize `tool_executions` rows. Handles parallel tool_use and Claude Code subagent forks (model threads as a prefix *tree*, not a linear chain).
3. **Turn/compaction/fork events** — new trailing user message ⇒ `turn_index++`; prefix shrink/divergence ⇒ compaction or sub-agent fork event (Claude Code `/compact` tolerance).
4. **Fresh-context grouping (Ralph)** — iterations share *no* prefix; group by system-prompt hash + app identity + temporal adjacency ⇒ "Iteration N of run R." Explicit headers always win over inference.
5. **Utility-call filtering** — Claude Code's small haiku calls (titles, summaries) must be tagged and excluded from loop chains or they pollute every run.

### Loop health + in-path enforcement (the moat)

Detectors writing queryable `loop_flags`, wired into the existing `alerts.py` + budget/rate-limit path:

- `repeat_tool_call` — same (tool, normalized-args) hash k≥3 (the documented 58×-identical-answer failure)
- `oscillation` — A-B-A-B tool cycles
- `no_progress` — consecutive-output similarity over k steps; no-file-progress streaks
- `cost_runaway` / `context_creep` — per-iteration cost slope, context-growth curve
- `error_retry` — same tool erroring k times

**Circuit-breaker mode**: on trip, configurable action — alert only; HTTP 429 with a structured loop-warning body the agent can react to; or **soft-landing** (inject a synthetic final response telling the agent to commit work and emit its completion promise, then block). This ships frankbria-style circuit breakers *for every runner including raw bash* — nothing else on the market can do it because nothing else is in the path.

### Run lifecycle for loop runners

- New headers: `x-agenticledger-run-id`, `x-agenticledger-loop-id`, `x-agenticledger-iteration`, `x-agenticledger-step-index` (strip-list `app.py:82-93` updated in lockstep).
- **Completion-promise detection** — configurable regex over responses (e.g. `COMPLETE`, `EXIT_SIGNAL: true`) sets run status; runners consume it via `GET /v1/runs/{id}/status` or `agenticledger wait <run-id>` instead of fragile output-grepping.
- **Per-run budgets** — USD / tokens / max-iterations, enforced at the proxy. A $400 blowout becomes impossible even with a naive bash loop.
- **MCP additions** (the ledger becomes the loop's memory across fresh contexts): `get_run_status`, `get_iteration_summary(n)`, `get_previous_iteration_learnings`, `get_loop_health` — iteration N asks what iteration N−1 did instead of re-reading logs.

### `agenticledger run` — the wrapper CLI

```
agenticledger run --loop my-feature --budget 25 --max-iterations 50 -- bash ralph.sh
```

Exports `ANTHROPIC_BASE_URL`/`OPENAI_BASE_URL` at the proxy, mints run/iteration headers, snapshots `PROMPT.md`/`fix_plan.md`/`prd.json`/`progress.txt` + `git rev-parse HEAD` at each iteration boundary (POST to the runs API), enabling **iteration diffing** and **plan burn-down**. Plus a Claude Code hooks pack (SessionStart/Stop/PostToolUse) that instruments Anthropic's official ralph-wiggum plugin, whose in-session Stop-hook loop never spawns processes — headers alone can't segment it, hooks can.

**Morning report**: on run completion, a one-page summary (iterations, cost curve, commits, burn-down, stuck stretches, unresolved errors) via webhook/Slack.

---

## Pillar 2 — Framework awareness & integration kits

### Auto-detection (preserve the zero-config promise)

`detect_agent(headers, body)` fallback in `_extract_meta` (`app.py:838-851`) when headers are absent:

- **Headers**: `user-agent: claude-cli/…` + `x-app: cli` (Claude Code); `x-stainless-*` (OpenAI SDKs); `litellm/*`. Prefix-match, never exact; explicit headers always take precedence.
- **Body**: Anthropic `metadata.user_id` embeds Claude Code's `session_<uuid>` — a far better session key than today's `auto-<date>` bucket (`app.py:841`); system-prompt prefix `"You are Claude Code"` (list-form system blocks live in `messages[0]` — `normalize.py:66-68`); tool-schema fingerprints.
- **BMAD personas**: fingerprint system prompts against a shipped signature table (v4/v5 `.bmad-core/agents/*.md` activation blocks, v6 `SKILL.md` ids like `dev-story`, `create-next-story`) ⇒ `agent_name=bmad:sm`, `framework=bmad`. Keep the table as JSON data so the community PRs new signatures.
- **Path-segment attribution** for header-less clients: `baseUrl: http://localhost:8000/t/<app>/<agent>/v1` maps path → app/agent tags. This is what makes OpenClaw (static provider config, no dynamic headers) and every other base-url-only framework attributable.

### OTLP ingest — the single highest-ROI build

An OTLP receiver (`/v1/traces`, `/v1/metrics`, `/v1/logs`) mapping GenAI-semconv spans into the same ledger schema, with dedupe when a call arrives via both proxy and OTel. This one feature unlocks: **Gemini CLI** (OTLP is its only surface), **Codex CLI** `[otel]`, **AutoGen/AG2** (OTel out of the box), **Pydantic AI**, **Vercel AI SDK**, **Mastra**, and **Claude Code's event stream** (`tool_result`, `tool_decision`, commit/LOC metrics — the safety audit trail for `--dangerously-skip-permissions` runs that the proxy alone can't see). With `CLAUDE_CODE_PROPAGATE_TRACEPARENT=1`, join OTel events to proxy-captured bodies for one unified timeline. Langfuse's `/api/public/otel` proved this is how the long tail integrates.

On egress, map reconstructed structure back onto OTel GenAI semconv (synthesized `invoke_agent` span per thread, `chat` per call, `execute_tool` from pairing, `gen_ai.conversation.id` = session) plus an OpenInference flavor — Agentic Ledger becomes *the reference implementation of loop inference from raw LLM traffic*, which is the spec-shaped wedge that makes frameworks reference it.

### Flagship integrations

**OpenClaw** (largest audience, loudest pain):
- Recipe: override the **native** provider's `baseUrl` in `openclaw.json` (preserves prompt-caching hints and native shaping; sidesteps issue #2903) — plus `agenticledger init openclaw` to patch it automatically.
- **Hard spend caps** — the headline: per-day/agent/channel dollar caps with configurable breach behavior (429 → fallback chain rolls to a cheaper model; synthetic "budget reached, resuming at midnight" reply; or model-rewrite downgrade route). Market it as *"the hard spend cap OpenClaw doesn't have."*
- ClawHub plugin using OpenClaw's typed hooks (`model_call_started`/`model_call_ended`, session lifecycle) → ingest API; maps sessionId/agentId/channel/subagent spawns onto ledger attribution. Opik and PostHog already ship OpenClaw plugins — table stakes to be listed alongside them.
- **Idle-burn analytics**: heartbeat/cron vs user-initiated spend, context-creep trend, cache ROI; token-spike alert doubles as prompt-injection/exfiltration detection — plus an **egress audit mode** (hash-chained record of every prompt leaving the machine) that monetizes the post-CVE security narrative.

**BMAD-METHOD**:
- Persona auto-detection (above) + synthetic SM → Dev → QA handoff edges reusing the existing handoff model.
- **Story-level cost ledger**: parse slash commands and tool-call file paths (`docs/stories/story-{epic}.{n}.md`, `prd.md`, `architecture.md`, `sprint-status`) to attach story/epic/phase to each call ⇒ cost per story, per persona, per phase; rework detection (QA→Dev bounce count) — a quality signal no tool provides.
- Budgets tuned for `bmad-loop`/`bmad-dev-auto` unattended runs (max $/calls/wall-clock per story).
- A **`bmad-agenticledger` companion module built with BMB** (installable via BMAD's own module system: wires the proxy at install time, adds a "ledger" skill so any persona can query spend mid-run) + `agenticledger bmad annotate` writing actual cost into story frontmatter and sprint-status — the ledger's numbers land in artifacts users already commit.

**Claude Code / Ralph** (ease 5 × audience 5 — first kit to ship): the one-liner recipe (`ANTHROPIC_BASE_URL` + optional OTel env vars), the hooks plugin on the marketplace, the `agenticledger run` wrapper, and upstream PRs adding `AGENTICLEDGER_URL` support to snarktank/ralph and frankbria/ralph-claude-code (ingest their `.ralph/status.json`/`prd.json` as run metadata).

**The rest, in priority order** (ranked by ease × audience): opencode → OpenAI Agents SDK (base_url + a pip `Agentic LedgerTracingProcessor`) → LangGraph/LangChain (recipe + optional callback handler) → CrewAI → Pydantic AI → Gemini CLI (OTLP) → Codex CLI → AutoGen/AG2 → Vercel AI SDK (`@agenticledger/otel` npm SpanProcessor) → Mastra (`Agentic LedgerExporter` class PR'd into their docs — the Braintrust playbook) → Cursor (MCP server only; don't chase base_url).

**Non-negotiable across all kits: pass-through fidelity.** Streaming SSE, tool_use blocks, caching headers, and provider errors must transit unmodified — frameworks blaming the proxy for breakage is the fastest way to lose everything. Build a fidelity test suite (golden request/response pairs per provider) before the adoption push.

---

## Pillar 3 — The shiny app (React SPA)

The current dashboard is one 54KB Python string — 1,380 lines of inline vanilla JS with two hand-rolled SVG layout engines, global-variable state, full re-render on every WS ping, zero tests (coverage explicitly omits it). It was the right call for a flat session→calls world; runs → loops → iterations → calls multiplies UI state (collapse, filters, diff views, live tails) past what innerHTML string-concat can carry.

**Decision: rebuild as a Vite + React SPA, shipped as static assets inside the wheel.** The reader confirmed the REST + WS contract needs zero changes — the WS is already a pure cache-invalidation signal. Plan:

1. Keep the API stable; the old dashboard keeps working until parity.
2. `dashboard/` (Vite + React + TypeScript). Serve `index.html` via `importlib.resources` + a StaticFiles mount behind the existing viewer gate (`app.py:410-413`).
3. Packaging: hatchling `artifacts = ['agenticledger/proxy/static/']` force-includes the gitignored `dist/` (the `mascot.jpg` precedent, `pyproject.toml:65-67`); release pipeline runs `npm ci && npm run build` before `python -m build`; sdist gets the same treatment so pip-from-source works without Node.
4. Fix the auth gaps a SPA inherits: authenticate `/ws`, and give the client a real token-passing story (today the dashboard's own fetches carry no credential and 401 under auth).
5. Pagination/incremental endpoints — full-session re-fetch is O(session) per event and long loop runs will break it regardless of frontend.

**New views that sell the story:**
- **Loop Lens** — iteration ribbon (cost, tokens, duration, files touched, commit SHA, plan items closed), cost-per-iteration sparkline, plan burn-down chart, stuck-loop indicator (similarity score, repeated-error clusters), iteration N vs N−1 diff of state files.
- **Step timeline** — per-thread ReAct accordions ("Step 4: reason → act(search) → observe"), delta-only message rendering (never re-render the shared prefix), repeat-count badges.
- **Flow graph, dual-mode** — Aggregated (repeated steps merge into one node: "search_web (7×)", cycles as back-edges — the existing `↩ N×` rendering is the anchor) vs Expanded (unrolled execution DAG). This is the Langfuse pattern, applied to inferred structure.
- **Session header strip** — steps, turns, retries, loop flags, failures-only toggle (the Honeycomb Agent Timeline pattern).
- **Framework modes** — BMAD story board (state machine × cost), OpenClaw channel/idle-burn cards.

---

## Pillar 4 — Distribution (features don't win; friction does)

Every incumbent analysis agrees: adoption is decided by friction, not features. The playbook:

1. **MCP registry first** — the official registry drives ~78% of MCP installs; also mcp.so, Smithery, Glama; PR listings into `punkpeye/awesome-mcp-servers` and `awesome-claude-code`. The MCP server doubles as a distribution channel *and* the self-introspection differentiator.
2. **One-liners everywhere** — `npx agenticledger` / `uvx agentic-ledger` / `brew install agenticledger`, alongside Docker/PyPI. Target: **time-to-first-trace under 5 minutes**.
3. **One integration kit per week** — each kit = a <10-line snippet + runnable example repo + docs page + **an upstream PR to the framework's own observability docs** (exactly how Langfuse/AgentOps/Braintrust got framework-README presence) + a blog post.
4. **Claude Code plugin marketplace** (`claude plugin install agenticledger`) and the ClawHub plugin for OpenClaw.
5. **"Observed by Agentic Ledger" badge** offered to Ralph/BMAD/OpenClaw template repos; the BMAD dashboard screenshots are Discord/YouTube-native content (that community loves token-savings screenshots).
6. **The spec play** — publish the loop-inference mapping (thread → `invoke_agent`, step → `chat`+`execute_tool`) as a documented spec page; reference implementations attract frameworks, not the other way around.

---

## Sequencing

**Phase A — True numbers + first wow (≈2 weeks).**
Pillar 0 fixes (cache tokens, Responses SSE bug, interrupted streams, parse-once) + ROADMAP quick wins; schema columns + Claude Code auto-detection (session UUID from `metadata.user_id` kills the `auto-<date>` bucket); the Claude Code/Ralph one-liner recipe. *Demo: point a real Ralph loop at the proxy, see correctly-priced per-iteration costs.*

**Phase B — Loop Engine (≈4 weeks).**
`loops.py` inference + `runs`/`tool_executions` tables; loop-health detectors + circuit breakers + per-run budgets; completion-promise detection + run status API; `agenticledger run` wrapper + hooks pack; Loop Lens v1 (in the current dashboard if the SPA isn't ready — the tab switcher at `dashboard.py:906-917` is the seam). *Demo: overnight run with a budget kill-switch and a morning report.*

**Phase C — Shiny app + reach (≈4-6 weeks, overlaps B).**
React SPA to parity then past it (Loop Lens, step timeline, dual-mode flow graph); OTLP ingest; BMAD mode (detection + story ledger + BMB module); OpenClaw kit (init command, spend caps, ClawHub plugin). *Demo: BMAD story board and OpenClaw budget guardrails.*

**Phase D — Adoption machine (ongoing).**
Kit-per-week cadence down the ranked list; MCP registry + package one-liners + badges; upstream PRs (Ralph runners, BMAD docs, framework observability pages); the spec page.

**Success metrics:** time-to-first-trace < 5 min; listings in ≥5 framework docs; MCP-registry installs; a Ralph/BMAD/OpenClaw user each publicly demoing the dashboard; cost accuracy validated against provider invoices (the trust metric that compounds).

---

## How this composes with ROADMAP.md

The ROADMAP's Phase 1–2 work (ingest auth, tokens/roles, redaction, retention) is not displaced — it's *load-bearing* for this plan: the new positioning is unattended agents with real spend and full prompts on disk, which makes trustworthy capture and a closed relay more urgent, not less. The pragmatic merge: ROADMAP quick wins ride along in Phase A; token/RBAC and redaction land with Phase C when the SPA needs a real auth story anyway; multi-tenancy and enterprise stay downstream — framework adoption (this plan) is what creates the fleet operators the enterprise edition later monetizes.

## 0.10 — "Everywhere, live" (locked 2026-08-19)

Two pillars, one story: the ledger records every provider, and shows you
the loop while it runs. Milestone:
https://github.com/ShekharBhardwaj/AgenticLedger/milestone/1

**Phase A.-1 — Design first (#99).** The cycle's fifteen findings were
four bug classes; the design doc retires the classes: one
canonicalization contract, one attribution pipeline, written UI honesty
rules, and a per-provider attribution story in the adapter contract
(Bedrock signs outbound, so tags ride inbound). User-reviewed before
any Phase A code.

**Phase A.0 — Harden the ground (#97, #98).** Before the refactor: a
wire-truth corpus of real captured traffic (quirks intact) plus a
parity harness — old and new pipelines must produce identical records,
enforced in CI forever after. Loadtest re-measured as Phase A's exit
criterion; a dashboard smoke test lands before Phase C.

**Phase A — Provider adapters (#94), the foundation.** One architecture
for every wire format; OpenAI/Anthropic move into adapters behind shims;
Azure OpenAI priced correctly as the first proof. Gate: zero behavior
change against the full suite and replayed fixtures.

**Phase B — Direct AWS Bedrock (#95), the flagship. ✅ SHIPPED,
E2E-verified 2026-08-22.** The ledger holds AWS credentials and re-signs
each call (SigV4 via botocore, optional [bedrock] extra); boto3 agents
and Claude Code in Bedrock mode recorded, budgeted, and kill-switched
under the user's hands on live IAM-signed traffic. The E2E also
delivered: per-agent fallback sessions (#103), one-word run naming +
--project (#104, #105 project-grouped sidebar), the Bedrock provider
mark, amber blocked bars, and last-activity session ordering. It
promoted restart-proof run signatures to the TOP of Phase T: in-memory
inference amnesia broke the wall test three times.

**Phase C — Live Loop view (#96), the wow.** Watch a run while it runs:
calls arriving, cost ticking, flags firing, on the existing websocket.
The ten-second demo.

**Phase T — Trusted application (#100), woven through.** First item,
promoted by the Phase B E2E: store-backed run signatures, so inferred
runs and their walls survive a proxy restart. Then the enterprise
posture by design, not patches: multi-replica-correct enforcement
(designed in #99 with the attribution pipeline), retention as product,
/metrics + structured logs + backup/restore, SECURITY.md + threat
model. SSO/RBAC/multi-tenancy deferred to 0.11 deliberately.

**Phase D — Companions.** #65 DeepSeek/Mistral packs, #67 Gemini CLI
fingerprint, #70 re-detect breakdown, #90 first-call stitch residue,
#91 session run chips, #92 self-upgrade, #93 httpx 1.x migration.

Deliberately deferred to 0.11: CrewAI/LangGraph detection, the semantic
report card, settings editing from the UI.

Build order A → B → C → D, user-zero hands on every seam, tag on the
word — the 0.9.2 retest discipline is the permanent process now.
