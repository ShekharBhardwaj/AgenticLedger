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

Agentic Ledger is pre-1.0. The latest minor receives every fix; the previous
minor receives security fixes only, for 90 days after the next minor ships. The
full window, with how fixes ship, is in
[docs/compliance/support-window.md](docs/compliance/support-window.md). Please
upgrade to a supported version before reporting, and test against `main` if
you can.

## Handling sensitive data — read this before deploying

Credentials never ride in URLs. Keys are accepted in headers only
(`x-agenticledger-api-key`, `Authorization: Bearer`,
`x-agenticledger-token`); a key in a query string is refused. The pairing
link carries its key in the URL fragment, which browsers never send to a
server, and the dashboard's live socket uses a one-minute single-use
ticket minted over a header, so access logs, proxy logs and browser
history hold no reusable secret.

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
- **The audit log is hash-chained, and its strength depends on a key.** Every
  audit row carries the hash of the row before it; `GET /api/audit/verify`
  walks the chain and names the first break. Without `AGENTICLEDGER_AUDIT_HMAC_KEY`
  the chain is plain SHA-256: it catches accidental edits and unsophisticated
  tampering, but a writer with database access can recompute every later hash.
  With the key the chain is HMAC-SHA256 and a database writer without the key
  cannot re-chain. Forwarding rows off the box (`AGENTICLEDGER_AUDIT_STDOUT`
  JSON lines, or OTLP logs when OTel export is on) is the external anchor that
  closes the remaining gap. Audit writes fail open by default so a store hiccup
  never takes the dashboard down; `AGENTICLEDGER_AUDIT_STRICT=1` refuses any
  audited action the log cannot record.
- **Compliance exports are integrity tagged, not encrypted.** By default the JSON
  export carries a SHA-256 checksum over the calls array, which catches accidental
  corruption but is not a signature (anyone who edits the calls can recompute it).
  Set `AGENTICLEDGER_EXPORT_HMAC_KEY` for a keyed HMAC-SHA256 tag that is
  tamper-evident to anyone holding the key. Neither encrypts the contents.

## Compliance documents

The documents a security or privacy review asks for (data flow with a
diagram, a data-processing description and DPA annex, the subprocessor
statement, the HIPAA posture, a SOC 2 and ISO 27001 control mapping, and
the support window) live in
[docs/compliance](docs/compliance/README.md). They describe what the
software does and where the evidence is; they do not claim an
attestation the project does not hold.

## Scope

In-scope: the proxy server, dashboard, API endpoints, MCP server, export, and the
storage layer in this repository. Out-of-scope: vulnerabilities in upstream LLM
providers, and misconfigurations of your own deployment environment.
