# HIPAA posture

## The short version

**No business associate agreement is offered, because none is needed.**
Agentic Ledger is software you run inside your own environment. The
project never receives, stores, or has access to protected health
information (PHI), so it is not a business associate. The people who
touch PHI through the ledger are your own workforce, on your own
systems, under your own policies.

**The ledger can run inside a HIPAA programme.** Whether it should hold
PHI is a decision for your privacy officer, and the settings below are
what that decision turns on.

## Where PHI would be

If your agents put PHI in prompts, the ledger sees it in transit on
every call and, at the default capture level, stores it. That is the
whole question. Three settings change the answer:

| Setting | Effect on PHI |
|---|---|
| `AGENTICLEDGER_CAPTURE_LEVEL=metadata` | No prompt or response content is stored. The ledger keeps cost, tokens, latency, model, status, attribution and the audit trail: everything the operational controls need, none of the content. Recommended where PHI may appear in prompts and the content is not needed for investigation. |
| `AGENTICLEDGER_REDACT=all` and `AGENTICLEDGER_REDACT_PATTERNS` | Content is stored with identifiers scrubbed (email, national id, card, IP, API key) plus your own patterns (medical record number formats, patient name lists). Pattern-based redaction is a reduction, not a guarantee: free text can still identify a person. |
| `AGENTICLEDGER_RETENTION_DAYS` | Bounds how long any stored content lives. |

In transit, the ledger forwards the call to your LLM provider exactly as
the agent sent it. Your agreement with that provider (its BAA, if any)
governs that hop, as it did before the ledger was in the path.

## Safeguards the ledger provides

Mapped to the Security Rule's families; the
[control mapping](controls-mapping.md) has the evidence for each.

**Access control (164.312(a)).** Every remote reader presents a key;
minted tokens carry a role (`viewer`, `editor`, `admin`) and can expire
and be revoked; team cards open only the proxy path. Keys are accepted
in headers only. The dashboard is open only on the ledger's own machine.

**Audit controls (164.312(b)).** Every read of captured content and
every change is an audit row naming the actor, the action, the target
and the client address. Rows are hash-chained; with
`AGENTICLEDGER_AUDIT_HMAC_KEY` the chain is keyed, and rows can be
forwarded off the box as JSON lines or OTLP logs. `GET /api/audit/verify`
names the first break.

**Integrity (164.312(c)).** The ledger never modifies a call. Exports
carry an integrity tag, keyed under `AGENTICLEDGER_EXPORT_HMAC_KEY`.

**Transmission security (164.312(e)).** Terminate TLS in front of the
proxy (the deployment guide shows nginx and Caddy), or enable the
built-in https listener for the dashboard with `AGENTICLEDGER_TLS=1`.
Credentials never ride in URLs.

**Workforce and minimum necessary.** Give each person the least role;
use `metadata` capture for teams who need spend and control but not
content; scope what a team card can call with allow and deny lists.

## What the ledger does not do

- It does not encrypt the database at rest. Use full-disk encryption,
  an encrypted volume, or Postgres with encryption at rest.
- It does not authenticate people by identity provider. Keys and tokens
  are the credential; tie them to people through your own issuance and
  the token name. (Identity provider sign-in is planned for 0.16.)
- It does not prove redaction is complete. Treat pattern redaction as
  reducing exposure, and choose `metadata` capture when exposure must be
  zero.
- It does not sign a BAA, hold a HITRUST certification, or provide a
  third-party assessment. What it provides is the documented behaviour
  above, testable in the open.

## A deployment that a privacy officer can sign off

```bash
AGENTICLEDGER_API_KEY=...            # every reader authenticates
AGENTICLEDGER_INGEST_KEY=...         # every agent authenticates
AGENTICLEDGER_CAPTURE_LEVEL=metadata # no content at rest
AGENTICLEDGER_RETENTION_DAYS=90
AGENTICLEDGER_AUDIT_HMAC_KEY_FILE=/run/secrets/audit.key
AGENTICLEDGER_AUDIT_STDOUT=1         # audit rows to your log pipeline
AGENTICLEDGER_HOST=127.0.0.1         # TLS terminator in front
```

With content capture required for investigation, replace the capture
line with `AGENTICLEDGER_REDACT=all` plus your patterns, and add
database encryption at rest and a documented access list for the
database itself.
