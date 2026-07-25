# Agentic Ledger Roadmap

## Thesis

Agentic Ledger is a transparent LLM proxy with an unusually rich per-call data model (full request/response, tool I/O, cost, latency, and agent/handoff lineage) and an honest security posture — but today it is a single-tenant, single-shared-secret tool with an unauthenticated open relay at its core, and several correctness gaps that silently lose or commingle data. The enterprise wedge is **trustworthy cost + behavior observability for agent fleets**: every prompt an agent ever saw, captured completely, attributed correctly, governed properly, and provably accessed. The open-core line is a promise we make publicly and never walk back: **the proxy, capture pipeline, dashboard, SQLite/Postgres stores, OTel export, MCP server, budgets, single-secret auth, cross-session analytics, replay, and compliance export stay MIT-licensed forever.** Enterprise edition monetizes the *operational and governance scale* that only multi-team buyers need — SSO/RBAC, multi-tenancy, distributed limits, field-level encryption, managed pricing/key services, SIEM sinks, and air-gapped distribution — never by crippling a capability that already shipped free.

This roadmap sequences correctness and trust first, because every downstream feature (budgets, audit, multi-tenancy, evals) is built on data we currently capture incompletely or attribute via spoofable headers. We fix the foundation, then layer governance, then tenancy, then differentiation.

---

## Quick wins (next 2-4 weeks)

These are small, high-leverage, mostly self-contained changes that de-risk the project immediately. Ship them before the phased work.

- **Fix the Postgres auto-session UUID data-loss bug** (`store.py` `uuid.UUID(session_id)` at lines 347/348/365/373/421/448 vs `app.py:539` minting `auto-2026-06-19`). On the recommended production backend, *every* header-less call is silently dropped via the bare `except Exception: pass` in the proxy path. Change `session_id` to `TEXT` (matches SQLite) or coerce via `uuid.uuid5`. ~30 lines. **This is the single biggest credibility blocker.**
- **Stop swallowing capture failures** in `app.py` (the `except Exception: pass` at ~line 377 and the streaming path ~458). Log at WARNING with a `capture_dropped` counter so gaps are visible instead of invisible.
- **Harden the secret check**: switch `app.py:136` from `supplied != _api_key` to `hmac.compare_digest` (kills the timing oracle) and stop accepting `?api_key=` except for an explicit same-origin dashboard bootstrap (kills the log/Referer leak).
- **Authenticate `/ws`** (`app.py:159`): it currently broadcasts every live call event (action_id, session_id, status) to any connected client with no auth. Reuse the existing key. ~10 lines.
- **Add a required proxy-ingest key** (`AGENTLEDGER_INGEST_KEY`) checked at the top of `proxy()` (`app.py:243`), optional-but-warn-on-startup, to close the open LLM relay that currently spends the upstream provider key for anyone who can reach it.
- **Fix the false "signed" claim**: `export.build_export` embeds a bare `sha256` of the calls *inside the same document* (`export.py:21`) — re-verifiable after edits. Either implement a real detached HMAC or stop calling it "signed" in `README.md:547`. Near-zero effort, removes legal/compliance risk.
- **Bound the rate-limiter memory** (`ratelimit.py`): evict empty deques, cap key count, and correct the docstring (line 15) that overclaims "Single-process safe" as if it were a general guarantee.
- **Shallow `/health` + real `/readyz`**: `/health` (`app.py:141`) returns `{"status":"ok"}` unconditionally. Add `/readyz` that runs `SELECT 1` so k8s can gate traffic.
- **Stop `_QuietFilter` from suppressing `/export/` and `DELETE` lines** (`__main__.py:56`) and emit a one-line structured audit record on every delete/export, so a minimal access trail exists today.
- **DCO sign-off in `CONTRIBUTING.md`** + a CI `Signed-off-by` check. Trivial, but it legally unlocks every later monetization path and must land before the contributor base grows.

---

## Phase 1 — Production-hardening (correctness & trust)

**Theme:** Make the data complete, the proxy non-leaky, and the hot path fast. Nothing else is sellable until capture is trustworthy and the proxy stops failing open.

| Feature | Edition | Effort | Impact |
|---|---|---|---|
| Fix auto-session UUID handling on Postgres (data-loss bug) | OSS-core | S | Critical |
| Async buffered ingestion (decouple capture from the proxy hot path) | OSS-core | M | High |
| Self-observability: `/metrics` + real readiness/liveness probes | OSS-core | M | High |
| Required proxy-ingest auth + identity binding (close the fail-open relay) | Either | M | Critical |
| Scoped, hashed, revocable API tokens with roles (replace the single shared secret) | OSS-core | L | Critical |
| Batched, backpressure-aware persistence + pooled connection tuning | OSS-core | M | Medium |

**Detail:**

- **Auto-session UUID fix** — Make `session_id` storage type-agnostic. Prefer `TEXT` in `_PostgresStore._connect` (matches SQLite, removes every `uuid.UUID(session_id)` cast); add a regression test feeding `auto-2026-06-19` through both backends. Document the one-time `ALTER COLUMN session_id TYPE TEXT` migration.
- **Async buffered ingestion** — `store.save()`, `broadcaster.broadcast()`, and `check_and_fire()` (which itself runs `get_session` + `get_period_cost`) are all `await`ed inline before the agent's response returns (`app.py:357-380`, streaming `438-457`). Move all post-call work to a bounded `asyncio.Queue` + background consumer started in `lifespan()`; on `QueueFull`, increment `dropped_total` and shed load — never block the LLM call. Keep budget/rate checks inline (they must precede the call). Flush on shutdown. At-most-once delivery is acceptable for observability; document it (Enterprise can add a durable sink later).
- **Self-observability** — `prometheus_client` (optional extra). Expose `agentledger_captured_total{status}`, `dropped_total{reason}`, `save_seconds`, `upstream_seconds`, `upstream_errors_total`, `rate_limited_total`, `budget_blocked_total`, `ws_clients`. Increment at the exact sites that currently swallow exceptions. Guard `/metrics` behind auth or an internal port; keep labels low-cardinality (never label by `session_id`/`user_id`). Split `/health` (liveness) from `/readyz` (DB ping + buffer saturation → 503 when unhealthy).
- **Proxy-ingest auth + identity binding** — Require an ingest-scoped token at the top of `proxy()` *before* forwarding. Derive an authoritative producer id from the token and store it server-side; keep the caller-supplied `x-agentledger-*` values as untrusted "claimed" metadata (today `_extract_meta` at `app.py:536-549` trusts them verbatim, and budgets/rate limits key off these spoofable values at `ratelimit.py:61-66`). Use a *distinct* header so the ingest key isn't confused with the upstream provider key in `forward_headers` (`app.py:322-326`). Gate behind `AGENTLEDGER_REQUIRE_INGEST_AUTH` (default off in OSS for dev, with a loud startup warning; Enterprise defaults on) to preserve zero-code-change UX.
- **Scoped API tokens with roles** — Add a hashed `tokens` table (id, name, `token_hash`=sha256, role `[viewer|editor|admin]`, scopes, created/expires/revoked/last_used) to `store.py` (reuse the `_MIGRATION_COLUMNS` pattern). Replace `_check_auth` with `authenticate(request) -> Principal` (constant-time `hmac.compare_digest`); map the legacy `AGENTLEDGER_API_KEY` to an implicit admin Principal for backward compat. Apply `require(role)` per endpoint: `viewer` for reads/search/export, `editor`/`admin` for `DELETE /api/sessions` (`app.py:176`). `mcp.handle_mcp` (`mcp.py:128`) must authenticate too. This is the keystone for every governance and tenancy feature that follows.

**Exit criterion:** A default-configured Postgres deployment loses zero calls; the proxy rejects unauthenticated ingest; reads/deletes are role-gated with revocable tokens; capture adds no measurable latency to the proxied call under DB contention; `/metrics` and `/readyz` are wired and a multi-replica deploy can be observed (even if limits aren't yet correct across replicas — that's Phase 3).

---

## Phase 2 — Governance, Access & Data Privacy

**Theme:** Make Agentic Ledger safe to point at regulated traffic. Capture-time redaction, retention/erasure, real tamper-evidence, and an access audit log — the controls a security reviewer and a SOC2 auditor demand of a system holding verbatim prompts + PII.

| Feature | Edition | Effort | Impact |
|---|---|---|---|
| Capture-time redaction pipeline (regex pack + pluggable detectors) | Either | M | Critical |
| Configurable capture levels (metadata / messages / full) | OSS-core | S | High |
| Data retention + scheduled purge + right-to-erasure by subject | Either | M | High |
| Tamper-evident audit ledger (hash-chained writes + genuinely signed exports) | Either | L | High |
| Access & export audit log (who viewed/searched/exported/deleted what) | Either | M | High |
| Field-level encryption at rest for sensitive columns | Enterprise | L | High |
| Managed/policy-based detectors (Presidio/NER), KMS key mgmt | Enterprise | — | High |

**Detail:**

- **Capture-time redaction** — New `agentledger/proxy/redact.py` (`redact_text`, `redact_obj`). Default `RegexRedactor` for email, US SSN, credit cards (Luhn-checked to cut false positives), phone, IP, and provider key shapes (`sk-…`, Anthropic, `AKIA…`, JWT/bearer). Apply in `normalize.py` *after* building `CanonicalRequest` and in `normalize_response`, so redaction happens **before** `store.save()` and **before** `emit_span()` — nothing sensitive is ever written. Critically, **redact only the stored copy, never the forwarded upstream body** (or you change model behavior). Pluggable via `AGENTLEDGER_REDACTORS=module:Class`; Enterprise drops in Presidio/NER. Gate with `AGENTLEDGER_REDACT`. Keep it sync and cheap — it runs on every call on the hot path.
- **Configurable capture levels** — `AGENTLEDGER_CAPTURE_LEVEL=full|messages|metadata`, read in `__main__.py`, applied in the `proxy()` handler before building content fields. `metadata` keeps tokens/cost/model/ids but nulls `messages/system_prompt/content/tool_calls/tool_results`; `messages` keeps prompts but drops `tool_results` (often the richest PII via retrieved docs). Supports per-environment override keyed off `meta['environment']`. Default stays `full`; docs steer prod users down. Composes with redaction (level applied first, then redact the residue).
- **Retention + erasure** — `AGENTLEDGER_RETENTION_DAYS` + `Store.purge_older_than(ts)` in both backends, run from a daily `lifespan` background task (no external cron). For GDPR DSAR: add `Store.delete_by_user(user_id)` and `DELETE /api/users/{user_id}` (mirrors `delete_session` at `app.py:176`) — `delete_session` is session-scoped, but `user_id` is the field that maps to a person. Return a deletion receipt written to the audit log so erasure is provable. Document the caveat that `auto-*` sessions have no `user_id` and rely on timestamp purge. Soft-delete by default (`deleted_at` column) with a separate hard-purge admin action, and add `deleted_at IS NULL` to every aggregate (`list_sessions`, `get_*_cost`) or costs will include purged data.
- **Tamper-evident ledger** — Two layers. (1) Append-only chaining at write time: `prev_hash` + `row_hash = sha256(prev_hash + canonical(record))` in `store.save()`; expose `verify_chain()`. (2) Replace the self-embedded hash in `export.build_export` with a **detached HMAC** (`AGENTLEDGER_EXPORT_HMAC_KEY`) for OSS and **Ed25519** signing for Enterprise (third party verifies with the public key only). Fix the `README.md:547` wording. *Risk to manage:* hash-chaining serializes writes — the Postgres pool (`max_size=10`, `store.py:293`) races on `prev_hash`, so this needs an advisory lock or single-writer chain head, which tensions with horizontal scaling (sequence the chain-head writer carefully relative to Phase 1 async ingestion).
- **Access audit log** — `audit_log` table (ts, principal_id/name, action `[view_session|search|export_json|export_report|delete_session|create_token|revoke_token]`, target id, source_ip, user_agent, result). Emit from every gated endpoint (`app.py:170-238`) and `mcp.py` tool dispatch via a small helper. Expose `GET /api/audit` (admin-gated). **Don't log raw search query strings** (they can themselves contain PII) — hash or truncate. Sample/exclude the continuous dashboard `/api/sessions` poll to avoid flooding the table.
- **Field-level encryption (Enterprise)** — Envelope encryption (AES-GCM via `cryptography`) on `messages/content/system_prompt/tool_results` in both backends' `save()`/row hydration, with a key-id prefix for rotation. Keep tokens/cost/model/ids/timestamps cleartext so aggregates and indexes still work. Honest tradeoff to message: `search()` `LIKE`/`ILIKE` (`store.py:212/403`) becomes meaningless over ciphertext — disable content search under encryption, keep metadata search. Order matters: redact first, then encrypt the residue. Lost key = unreadable history; KMS integration is the Enterprise value-add.

**Exit criterion:** A buyer can run Agentic Ledger with redaction on, a retention window enforced, every read/export/delete recorded in a tamper-evident audit log, exports verifiable by a third party with a public key, and (Enterprise) sensitive columns encrypted at rest — and can fulfill a GDPR erasure request with a provable receipt.

---

## Phase 3 — Multi-tenancy & Enterprise edition

**Theme:** Host multiple teams/customers/environments behind one deployment, correctly, at scale. This is where the open-core seam and the distributed-state correctness land together — and where the Enterprise edition becomes a real product.

| Feature | Edition | Effort | Impact |
|---|---|---|---|
| Tenant/Project dimension on the ledger (data model foundation) | OSS-core | M | Critical |
| Edition boundary + offline license-key gate (the open-core seam) | Either | L | Critical |
| Dual-license: MIT core + BSL/commercial EE | Either | S | Critical |
| Per-tenant API keys with tenant binding + scoped reads | Enterprise | L | Critical |
| Redis-backed distributed, tenant-scoped rate limiter + budget enforcement | Enterprise | L | High |
| Per-tenant budgets/quotas + usage metering & chargeback export | Enterprise | L | High |
| SSO / OIDC dashboard login with secure sessions | Enterprise | XL | High |
| Service accounts with key rotation & expiry for ingest pipelines | Enterprise | M | Medium |
| Environment as a first-class isolation boundary | OSS-core | S | Medium |
| Production K8s packaging: Helm chart + Terraform; air-gapped bundle | Mixed | L/M | High |

**Detail:**

- **Tenant/Project dimension (the keystone)** — Add `('tenant_id','TEXT')` and `('project_id','TEXT')` to `_MIGRATION_COLUMNS` (non-destructive, both backends). Capture via `x-agentledger-tenant-id`/`x-agentledger-project-id` in `_extract_meta`, defaulting to `AGENTLEDGER_DEFAULT_TENANT` so single-tenant deploys are unchanged. Composite index `(tenant_id, timestamp)`. **Keep them TEXT, not UUID**, to avoid repeating the `session_id` cast bug. Header-asserted `tenant_id` is *not* a security boundary until bound to a credential — ship it with per-tenant keys or document loudly.
- **Edition gate + dual license** — `agentledger/edition.py` resolves the active edition from an optional `AGENTLEDGER_LICENSE_KEY`, verified **offline** against an embedded Ed25519 public key (no phone-home — required for air-gap). `require_feature('rbac')` guards EE-only routers. All paid code lives in a separate top-level `agentledger_ee/` tree under **BSL 1.1** (converts to Apache-2.0 after 4 years, with a non-compete additional-use grant); the MIT wheel never contains it. `create_app()` stays OSS and conditionally mounts EE routers if importable *and* licensed. **The gate must fail-open to full OSS and only ever guard NEW capabilities** — never retroactively gate something that shipped free. Keep MIT on the entire current codebase; do not relicense existing files.
- **Per-tenant API keys + scoped reads (Enterprise)** — `api_keys` table (key_hash, tenant_id, scopes). Resolver replaces the Principal lookup to return the bound `tenant_id`; on the proxy path, *override* `meta['tenant_id']` with the key's binding rather than trusting the header. Thread a **non-optional** `tenant_id` scope into every store read (`list_sessions`, `get_session`, `get`, `search`, `get_*_cost`, `delete_session`) and into `mcp.py` tool handlers (`mcp.py:157`) — a forgotten filter must be a type error, not a silent cross-tenant leak. Use a centralized scoped-query helper; one missing `WHERE tenant_id = ANY($scope)` is a breach. Admin tokens carry a wildcard scope.
- **Redis-backed distributed limiter + budgets (Enterprise)** — `ratelimit.py` `RateLimiter._windows` is a per-process `defaultdict(deque)` (correct for one worker only); across replicas the effective limit is N× configured, and `_check_budgets` reads a per-DB `SELECT SUM(cost_usd)` with no reservation (TOCTOU overshoot). Add a `RateLimiterBackend` abstraction: keep `InMemoryBackend` as the OSS default, add `RedisBackend` (sliding-window sorted-set + Lua for atomicity, TTL'd keys that also fix the current unbounded-key leak) when `AGENTLEDGER_REDIS_URL` is set. Budgets become atomic `INCRBYFLOAT` counters with daily-key TTL, reserved on admission and reconciled when the worker persists actual `cost_usd`. **Preserve fail-open**: a Redis outage must not block agent calls. Add a tenant dimension to the limiter key throughout.
- **Per-tenant budgets + metering (Enterprise)** — `tenant_limits` table (daily/monthly budget, rpm) with the env scalars as fallback. Refactor `_check_budgets` to compare against the resolved tenant's row. Add `usage_by_tenant`/`usage_by_project` GROUP BY rollups and `GET /api/usage?from=&to=&group_by=tenant|project|model|environment` with CSV/JSON download. Produce a signed monthly usage statement per tenant (reuse the Phase-2 signing). Record the `pricing.py` table version used, since stale prices produce wrong invoices. A daily materialized rollup is needed at volume.
- **SSO / OIDC (Enterprise)** — OIDC Authorization Code + PKCE (`/auth/login`, `/auth/callback`, `/auth/logout`), mapping IdP group claims to viewer/editor/admin from the Phase-1 role model. Issue an httponly/secure/samesite cookie; add a cookie path to `authenticate()` so the dashboard works without exposing tokens to JS (today `dashboard.py` calls `/api/*` with no credential and only works when auth is unset). Cookie auth introduces CSRF surface — add state/nonce and CSRF tokens on `DELETE /api/sessions`. Keep `authlib` an optional extra so OSS stays dependency-light.
- **Service accounts (Enterprise)** — Model as a `tokens` subtype (`kind=service_account`, `role=ingest`, `expires_at`, optional source-CIDR). Stamp the resolved service-account id as the authoritative producer on `store.save`. Support overlapping active keys for zero-downtime rotation; `last_used_at` to find stale keys. Expiry must fail closed for ingest but return a clear 401.
- **Environment isolation (OSS-core)** — `environment` is captured and defaulted to `development` but never appears in a `WHERE` clause. Add `?environment=` filters to `/api/sessions`/`/api/search` + store methods, make it part of the budget/rate-limit key, and normalize/allowlist values (`prod` vs `production` typos fragment rollups).
- **K8s + air-gap packaging (Mixed)** — `deploy/helm/agentledger/` (Deployment with `/health`+`/readyz` probes, Service, Ingress, HPA, PodDisruptionBudget, Secret for keys/DSN/`REDIS_URL`, PVC, opt-in Postgres subchart, ServiceMonitor for `/metrics`). **The chart must refuse SQLite when `replicaCount>1`** (shared-volume corruption), and `replicaCount>1` is only honest once the Redis limiter ships — gate the docs. Add a Postgres-capable Docker image variant so the prod path isn't "fall back to pip" (`README:65`). For air-gap: cosign-sign the GHCR image, emit SBOM (syft) + SLSA provenance, vendor a pinned wheelhouse from `uv.lock`, and pair with offline license activation. `deploy/terraform/` modules for AWS/GCP.

**Exit criterion:** Two tenants run behind one deployment with no cross-tenant data access (proven by per-endpoint scope tests), correct rate limits and budgets across ≥3 replicas, per-tenant usage statements, SSO login, and a `helm install` that refuses unsafe configs. The MIT wheel contains zero EE code; the offline license gate fails open to OSS.

---

## Phase 4 — Differentiation (Evals, Analytics & Ecosystem)

**Theme:** Move from "show me what this run did" to "is quality regressing, which prompt is better, what's my p95 by model" — the questions that make teams pick a platform over a logging proxy — plus the provider breadth and integrations that let Agentic Ledger sit in front of a whole fleet.

| Feature | Edition | Effort | Impact |
|---|---|---|---|
| Cross-session analytics dashboard (cost/latency breakdowns + percentiles) | OSS-core | L | Critical |
| Online evals: scorer pipeline on the live capture stream | Either | L | Critical |
| Offline evals + datasets curated from production traces | Either | L | High |
| Prompt replay + diff (playground over captured calls) | Either | M | High |
| Pluggable provider adapters (Azure/Gemini/Vertex/Bedrock/Cohere/Mistral) | OSS-core | L | Critical |
| Refreshable, normalized pricing source | OSS-core | M | High |
| GenAI semantic-convention enrichment for OTel | Either | M | High |
| Deploy-aware regression detection | Enterprise | L | High |
| First-class alert channels (Slack/PagerDuty/Teams) with signing, retries, dedup | Either | M | Medium |
| Ingest API + framework SDKs (LangChain/LlamaIndex/CrewAI) | OSS-core | L | High |
| Multi-provider gateway mode with credential injection | Enterprise | XL | High |
| Datadog/Splunk/SIEM native export sink | Enterprise | L | Medium |
| Live anomaly & quality detection; SLO tracking & violation feed | Mixed | M | Medium |

**Detail:**

- **Cross-session analytics (the highest-leverage single addition)** — Everything today is locked to one `session_id`; the only aggregation is `SUM(cost_usd)`. Add `aggregate(group_by, since, until, filters)` (COUNT, SUM cost/tokens, AVG/p50/p95/p99 latency, error-rate; Postgres `percentile_cont`, SQLite app-side on a capped sample) and `timeseries(bucket, metric)`. Expose `GET /api/analytics`; add an Analytics tab to `dashboard.py` rendered as dependency-free SVG (matching the existing `renderFlowDAG`/`renderTraceDAG` style). Requires a `timestamp` index (only `session_id` is indexed today). This is the dashboard Helicone/Langfuse lead with, and we have the richest underlying data.
- **Online evals** — New `evals.py` with a `Scorer` protocol: heuristic scorers (`json_valid`, `regex_match`, `contains`, `response_length`, `refusal_detect`, `empty_response`, `tool_error`) and an LLM-as-judge scorer via `app.state.client`. Add a nullable `eval_scores` (JSON) column. Fire-and-forget `run_scorers` after `store.save` in both proxy branches, **off the request path and sampled** so it adds no latency. Judge calls loop back through the proxy — exclude them by an internal header or they self-capture. Render scores in `renderCall` and `export.build_export`.
- **Offline evals + datasets** — `datasets` + `dataset_items(action_id, expected)` tables; endpoints to create, add by `action_id`, and run the `evals.py` scorers across a set. "Save to dataset" buttons in `renderCall` and search results. Builds regression suites from real traffic. Handle orphaning when `delete_session` removes a referenced `action_id`.
- **Prompt replay + diff** — Add `denormalize_request` to `normalize.py` (normalization is currently one-way), then `POST /replay/{action_id}`: reconstruct the native request from `canonical_req`, apply overrides (model/system_prompt/temperature/messages), send via `app.state.client`, and return original-vs-new side by side with cost/latency/token deltas. **Read-only — never auto-execute captured `tool_calls`.** Diff panel in the dashboard.
- **Provider adapters** — `detect_provider()` keys off URL path alone (anything with `messages` → anthropic, else openai), so Azure is mis-priced and Gemini/Vertex/Bedrock/Cohere/Mistral capture nothing useful. Introduce `agentledger/proxy/providers/` with a `Provider` protocol (`matches`, `normalize_request`, `normalize_response`, `reconstruct_stream`); move existing OpenAI/Anthropic/Responses logic into adapters and add the rest. `detect_provider` becomes a registry walk; keep current functions as shims so callers are unchanged. Per-adapter `_extract_tool_results` (the current shared helper assumes OpenAI/Anthropic shapes).
- **Refreshable pricing** — `pricing.py` is a static substring table that silently returns `None` (zeroing cost and disabling budgets/alerts) for unknown ids, and Bedrock/Vertex-prefixed ids (`anthropic.claude-…-v2:0`, `publishers/google/…`) don't match. Add `normalize_model_id()` (strip region/version prefixes before longest-substring match), extend the table, support an optional `AGENTLEDGER_PRICING_URL` layered over built-ins (fail closed to built-ins, never block startup), and add cached/reasoning-token pricing. WARN-log unpriced models so silent $0 is visible.
- **OTel enrichment** — `emit_span()` sets only scalar `gen_ai.*` attrs, so Datadog/Grafana LLM views render empty. Behind `AGENTLEDGER_OTEL_CAPTURE_CONTENT` (off by default, redaction-gated), add `gen_ai.prompt.*`/`gen_ai.completion.*` and child spans per tool call; model handoffs as span links. Replace the unbounded `_session_traces`/`_span_contexts` dicts with LRU+TTL and offer a private `TracerProvider` so it stops colliding with host apps (currently set globally at import in `__main__.py`).
- **Regression detection (Enterprise)** — Capture `x-agentledger-version`; add `compare_versions(a, b, metrics)` joining on `eval_scores`; `GET /api/regressions` auto-flags when the latest version breaches a delta (>20% latency, >5pt eval drop) vs the prior, with minimum-sample gating to avoid false alarms on low volume. Fire via `alerts.py` with `type='regression'`.
- **Alert channels** — Refactor `alerts.py` (one unsigned, un-retried, un-deduped POST today) into a channel abstraction: Slack (Block Kit), PagerDuty (Events v2), Teams (MessageCard), GenericWebhook (+ `X-Agentic Ledger-Signature` HMAC). Add exponential backoff on 5xx and a per-`(type, session_id)` cooldown so a noisy session can't storm the channel. Don't leak prompt content into alert bodies.
- **Ingest API + SDKs** — Extract the proxy's save/emit/broadcast/alert block into a shared `_record()` coroutine, then add `POST /ingest` (same pipeline, auth + size limits, compute cost server-side rather than trusting client `cost_usd`). Ship thin LangChain/LlamaIndex/CrewAI callback handlers that push to `/ingest` — backing the `langchain, crewai` keywords that currently have no implementation. This also enables the SIEM fan-out sinks (Enterprise) from the same `_record()` hook.
- **Gateway mode (Enterprise)** — `AGENTLEDGER_ROUTES` mapping route keys → `{upstream_url, auth}`, per-route cached `AsyncClient`, and credential injection (static key, Bedrock SigV4 via botocore, Vertex token refresh via google-auth) — turning the per-endpoint sidecar into one observability gateway in front of all model traffic. Sign SigV4 *after* the final body/headers are set; secrets move to KMS, not env.

**Exit criterion:** A team can answer "p95 latency by model this week," score production traces online and offline, replay a captured call against a new model with a diff, get alerted on a cross-deploy regression, and route Azure/Gemini/Bedrock/OpenAI/Anthropic traffic through one gateway with correct cost on every call.

---

## What stays OSS-core vs Enterprise

| Capability | OSS-core (MIT, free forever) | Enterprise (BSL/commercial) |
|---|---|---|
| Transparent proxy + capture pipeline | ✅ | — |
| SQLite + Postgres stores | ✅ | — |
| Dashboard (calls / flow DAG / trace DAG / analytics) | ✅ | — |
| Cross-session analytics, percentiles, time-series | ✅ | — |
| Budgets, alerts, in-process rate limiting | ✅ | — |
| OTel export + MCP server | ✅ | — |
| Compliance export (HMAC-signed) | ✅ | Ed25519 asymmetric signing |
| Scoped/revocable API tokens + roles | ✅ | — |
| Capture-time redaction (regex) | ✅ | Policy/NER detectors (Presidio) |
| Retention/TTL + erasure-by-subject | ✅ | Legal-hold exemptions |
| Tamper-evident hash-chain + access audit log (basic) | ✅ | Hash-chain + tamper-evidence guarantees |
| Provider adapters, refreshable pricing, replay, evals (engine) | ✅ | — |
| Ingest API + framework SDKs | ✅ | — |
| Tenant/project data dimension | ✅ | — |
| Helm chart / Terraform | ✅ | Air-gapped bundle + offline activation |
| **Multi-tenant isolation + per-tenant keys** | — | ✅ |
| **Distributed (Redis) limits + budget reservation** | — | ✅ |
| **Per-tenant budgets, quotas, chargeback statements** | — | ✅ |
| **SSO/OIDC/SAML + secure sessions** | — | ✅ |
| **Service accounts + key rotation** | — | ✅ |
| **Field-level encryption at rest + KMS** | — | ✅ |
| **Multi-provider gateway + credential injection** | — | ✅ |
| **Datadog/Splunk/SIEM native sinks** | — | ✅ |
| **Deploy-aware regression detection, SLO tracking** | — | ✅ |
| **Signed/SBOM/provenance air-gap distribution** | — | ✅ |

**Open-core promise:** No capability listed under OSS-core will ever be moved behind the license gate. The gate guards only *new* Enterprise capabilities, and it fails open to the full OSS feature set when no license is present.

---

## Competitive positioning

- **vs Langfuse / LangSmith:** They win today on evals, datasets, and prompt playground. Our Phase-4 online/offline evals + replay close that gap, but our durable wedge is the **transparent-proxy zero-code-change capture** (no SDK to thread through every call) plus a **richer per-row lineage model** (handoff_from/to, parent_action_id, agent/app/env) that most logging-first tools don't capture natively.
- **vs Helicone:** Helicone leads on the cost/latency dashboard we're missing until Phase 4. But Helicone is SaaS-first; our differentiation is **self-hosted, air-gapped, BSL-tier on-prem** with field-level encryption and a real access audit log — the regulated/on-prem buyer Helicone underserves.
- **vs Arize Phoenix:** Phoenix is strong on OTel-native tracing and drift/eval analytics. We lean into **OTel GenAI semantic-convention enrichment** (Phase 4) to interoperate rather than compete on their turf, while owning the **proxy + budget/governance control plane** Phoenix doesn't do.
- **vs all four:** Our **compliance posture is the moat** — capture-time redaction, tamper-evident hash-chained exports, retention + erasure-by-subject, and a tamper-evident access audit log give us a "governed observability" story none of them lead with. The honest `SECURITY.md` (which states plainly that the store holds your most sensitive prompts verbatim) becomes a trust asset once these controls ship.
- **Pricing/packaging edge:** A genuinely complete MIT core (analytics, evals, replay, redaction, retention all free) plus a clean BSL Enterprise tier for tenancy/scale/governance — versus competitors whose free tiers are deliberately analytics- or volume-limited. We monetize operational scale, not table-stakes capability.
