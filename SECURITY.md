# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately** — do not open a public issue.

- Preferred: use GitHub's [private vulnerability reporting](https://github.com/ShekharBhardwaj/AgenticLedger/security/advisories/new)
  ("Report a vulnerability" under the repository's **Security** tab).
- Alternatively, email **shekhar.nik@gmail.com** with the details.

Please include a description, reproduction steps, affected version/commit, and the
impact you believe it has. We aim to acknowledge reports within 5 business days and
to provide a remediation timeline after triage.

## Supported versions

Agentic Ledger is pre-1.0 and ships fixes on the latest released version. Please upgrade
to the most recent release before reporting, and test against `main` if you can.

## Handling sensitive data — read this before deploying

Agentic Ledger is an observability proxy: **by design it captures the full content of
every LLM request and response**, including system prompts, user messages, tool
definitions, and tool results. Treat the Agentic Ledger datastore and dashboard as
containing the same sensitivity as your most sensitive prompts.

Recommendations for any non-local deployment:

- **Restrict network access.** Run the proxy on a private network; do not expose the
  dashboard or API to the public internet.
- **Set `AGENTICLEDGER_API_KEY`.** This gates the dashboard, `/api/*`, `/session/*`,
  `/export/*`, `/ws`, and `/mcp` endpoints. The proxy path itself fails open so that
  observability never blocks your agent — so the proxy port must be network-restricted.
- **Secure the database.** Captured traffic is stored in SQLite or Postgres. Apply
  the same access controls, encryption-at-rest, and retention policy you would to any
  store of sensitive prompt data.
- **Compliance exports are signed but not encrypted.** The JSON export includes a
  SHA-256 integrity hash for tamper-evidence; it does not encrypt the contents.

## Scope

In-scope: the proxy server, dashboard, API endpoints, MCP server, export, and the
storage layer in this repository. Out-of-scope: vulnerabilities in upstream LLM
providers, and misconfigurations of your own deployment environment.
