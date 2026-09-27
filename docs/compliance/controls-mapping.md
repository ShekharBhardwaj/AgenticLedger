# Control mapping: SOC 2 and ISO 27001

What the software does, placed against the SOC 2 Trust Services Criteria
(2017, with 2022 points of focus) and ISO/IEC 27001:2022 Annex A, with
the evidence for each row.

**Read this before using the table.** The Agentic Ledger project holds
no SOC 2 report and no ISO 27001 certificate, and this document is not
one. Those attest to an organisation's controls over time; this maps a
piece of software's controls so that your auditor can place it inside
your own attested system. A row says "the software supports this
control in this way, and here is where to check"; it never says "this
control is audited".

Evidence points at the README (settings and endpoints), a test file
under `tests/` that exercises the behaviour on every commit, or a
workflow under `.github/workflows/`.

## Logical access

| Control | SOC 2 | ISO 27001 | How the ledger supports it | Evidence |
|---|---|---|---|---|
| Readers authenticate | CC6.1 | A.5.15, A.8.3 | With `AGENTICLEDGER_API_KEY` set, the dashboard, API, exports, websocket and MCP require a key. Without it, access is open only on the ledger's own machine; every other caller presents the auto-generated pairing key. | `tests/test_token_auth.py`, `tests/test_remote_guard.py` |
| Least privilege by role | CC6.1, CC6.3 | A.5.15, A.5.18, A.8.2 | Minted tokens carry `viewer`, `editor` or `admin`; `ingest` opens only the proxy path. Roles are enforced per endpoint; a forbidden attempt is a 403 and an audit row. | `tests/test_token_auth.py`, `agenticledger/proxy/auth.py` |
| Credentials are not stored in clear | CC6.1 | A.5.17 | Tokens are stored as SHA-256 hashes and shown once at creation. Provider keys pass through and are never stored. | `tests/test_token_auth.py` (`hash_token`) |
| Credential lifecycle | CC6.2, CC6.3 | A.5.16, A.5.18 | Tokens can expire (`expires_in_days`) and be revoked (`DELETE /api/tokens/{id}`); revocation is immediate and audited. | `tests/test_token_auth.py`, `tests/test_teams.py` |
| Credentials never in URLs | CC6.1, CC6.7 | A.5.17, A.8.24 | Keys are accepted in headers only; a key in a query string is refused. The pairing link carries its key in the URL fragment; the live socket uses a one-minute single-use ticket. | `tests/test_token_auth.py`, `tests/test_remote_guard.py` |
| Agents authenticate to the proxy | CC6.1, CC6.6 | A.8.20 | `AGENTICLEDGER_INGEST_KEY` closes the open relay; team cards are per-team credentials with their own budget and lists. A revoked card gets a final 403. | `tests/test_ingest_auth.py`, `tests/test_teams.py` |
| Network boundary | CC6.6 | A.8.20, A.8.22 | `AGENTICLEDGER_HOST=127.0.0.1` behind a TLS terminator; the container runs as a non-root user, and the deployment guide runs it with a read-only filesystem. | `Dockerfile`, `docs/deployment.md` |

## Change and operations

| Control | SOC 2 | ISO 27001 | How the ledger supports it | Evidence |
|---|---|---|---|---|
| Changes are tested before release | CC8.1 | A.8.29, A.8.32 | Every commit runs lint, the test suite on Python 3.10 to 3.12, the full suite on Postgres, a fresh install, a cold Docker build, a Docker-on-Postgres smoke and a browser smoke of the dashboard. A tag cannot publish unless the suite passes. | `.github/workflows/ci.yml`, `.github/workflows/release.yml` |
| Release integrity | CC8.1, CC6.8 | A.8.19, A.8.30 | Wheels publish to PyPI by trusted publishing (no long-lived secret). Images are signed with Sigstore cosign (keyless) and ship an SBOM and provenance attestation; the deployment guide shows verification. | `.github/workflows/release.yml`, `docs/deployment.md` |
| Dependency and code scanning | CC7.1 | A.8.8 | Dependabot for Python dependencies and GitHub Actions; CodeQL on the repository. | `.github/dependabot.yml`, `.github/workflows/codeql.yml` |
| Upgrade safety | CC8.1 | A.8.32 | Schema changes are additive and applied at startup on both backends; upgrades need no migration step. | `tests/test_upgrade_safety.py` |
| Vulnerability reporting | CC7.3, CC7.4 | A.5.24, A.5.26 | Private reporting channel, acknowledgement target, and the support window for fixes. | `SECURITY.md`, `docs/compliance/support-window.md` |

## Monitoring and audit

| Control | SOC 2 | ISO 27001 | How the ledger supports it | Evidence |
|---|---|---|---|---|
| Audit trail of sensitive actions | CC7.2, CC4.1 | A.8.15 | Every read of captured content, every export, deletion, token action, label change, stop and lift, and every failed login is an audit row: actor, role, action, target, client address, time. | `tests/test_audit.py` |
| Audit trail integrity | CC7.2 | A.8.15, A.8.24 | Rows are hash-chained; with `AGENTICLEDGER_AUDIT_HMAC_KEY` the chain is HMAC-SHA256. `GET /api/audit/verify` walks it and names the first break. Strict mode refuses any audited action the log cannot record. | `tests/test_audit.py`, `agenticledger/proxy/auditchain.py` |
| Log forwarding | CC7.2 | A.8.15, A.8.16 | Audit rows leave the box as JSON lines on stdout or OTLP log records; metadata-only spans per call to any OTLP collector. | `tests/test_audit.py`, `tests/test_observability.py`, `tests/test_otlp.py` |
| Operational metrics | CC7.2 | A.8.16 | `/metrics` in Prometheus format: captures persisted and dropped, queue depth, audit rows dropped, refusals by reason, the stop-all state. | `tests/test_observability.py`, `tests/test_fleet_controls.py` |
| Alerting | CC7.2 | A.8.16 | Webhook alerts on cost, latency, error rate, daily spend, budget breach, loop flags, and run ceilings approaching. | `tests/test_alerts.py` |

## Data protection

| Control | SOC 2 | ISO 27001 | How the ledger supports it | Evidence |
|---|---|---|---|---|
| Data minimisation | CC6.5, C1.1, P4.1 | A.5.34, A.8.10 | `AGENTICLEDGER_CAPTURE_LEVEL=metadata` stores no content; `AGENTICLEDGER_REDACT` and `AGENTICLEDGER_REDACT_PATTERNS` scrub identifiers and secrets before storage, including tool arguments. | `tests/test_redact.py` |
| Retention and disposal | CC6.5, P4.2 | A.5.33, A.8.10 | `AGENTICLEDGER_RETENTION_DAYS` purges on a schedule; erasure by user, session, or project through audited endpoints. | `tests/test_retention.py`, `tests/test_labels.py`, `tests/test_hardening.py` |
| Integrity of exports | CC6.7, PI1.4 | A.8.24 | Exports carry a SHA-256 checksum by default and a keyed HMAC-SHA256 tag with `AGENTICLEDGER_EXPORT_HMAC_KEY`. | `tests/test_export.py` |
| Transmission security | CC6.7 | A.8.24, A.8.21 | TLS in front of the proxy, or the built-in https dashboard listener (`AGENTICLEDGER_TLS=1`). | `docs/deployment.md`, `tests/test_config.py` |
| Encryption at rest | CC6.7 | A.8.24 | **Not provided by the ledger.** Use disk, volume, or database encryption. | this document |
| No telemetry, no third-party processing | CC6.7, P6.1 | A.5.19, A.5.20 | The software sends nothing to the project; every outbound destination is configured by you and off by default. | `docs/compliance/data-flow.md`, `docs/compliance/subprocessors.md` |

## Processing integrity and availability

| Control | SOC 2 | ISO 27001 | How the ledger supports it | Evidence |
|---|---|---|---|---|
| Calls are never altered | PI1.1, PI1.3 | A.8.26 | The proxy forwards each call as sent; every control refuses or records, never rewrites. Wire-format parity is tested request by request. | `tests/test_wire_parity.py`, `tests/test_proxy.py` |
| Refusals are recorded and explained | PI1.3 | A.8.15 | Every refusal (stop, lists, rate limit, loop guard, kill switch, ceiling, budget) is a recorded row with the reason, counted by reason in `/metrics`. | `tests/test_fleet_controls.py`, `tests/test_kill_switch.py` |
| Spend limits hold under load | PI1.3 | A.8.6 | Budgets and run ceilings reserve an estimate per admitted call, so concurrent callers see each other; overshoot is bounded to one call. | `tests/test_budget_reservation.py` |
| Observability never blocks the agent | A1.2 | A.8.6, A.8.14 | Capture failures are counted and logged, never returned to the agent; a store outage fails open on the proxy path. Async capture with a bounded queue. | `tests/test_async_capture.py`, `tests/test_observability.py` |
| Liveness and readiness | A1.2 | A.8.14 | `/health` (process up) and `/readyz` (store reachable, 503 otherwise) for orchestrators. | `tests/test_observability.py` |
| Backups and recovery | A1.2, A1.3 | A.8.13 | **Yours to run.** The store is a SQLite file or a Postgres database; back it up as you back up any store of sensitive prompt data. | `docs/deployment.md` |

## Not covered

Controls that live in your organisation, not the software: personnel
security, physical security, vendor management, risk assessment,
business continuity beyond backups, identity-provider sign-in (planned
for 0.16), and encryption at rest. Placing the ledger inside those is the
work this document is meant to make short.
