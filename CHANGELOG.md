# Changelog

All notable changes to Agentic Ledger are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-06-19

### Upgrade notes
- **Postgres:** on first connect, `session_id` is migrated in place from `UUID` to `TEXT`
  (`ALTER COLUMN`). This is automatic and safe — no action required — but it is a schema change.
- **Audit log is on by default:** a new `audit_log` table is created and a row is written for
  each sensitive read/export/delete. Set `AGENTLEDGER_AUDIT_LOG=0` to disable.
- **Costs for `*-mini`/`*-nano` models are now correct** (previously over-reported, e.g.
  `gpt-4o-mini` was billed at `gpt-4o` rates). Newly captured costs will be lower/accurate.

### Security
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
  `/ws` dashboard-credential propagation is deliberately deferred (see ROADMAP.md).
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

[Unreleased]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ShekharBhardwaj/AgenticLedger/compare/v0.1.7...v0.2.0
