# Data processing description

The facts your privacy team needs to place Agentic Ledger in an Article
30 record of processing, a data protection impact assessment, or the
annex of a data processing agreement. The project is not a party to any
of those: you run the software, so you are the controller (or the
processor acting for your customer), and the ledger is a tool inside
your processing. The template at the end is for the agreement you sign
with whoever you run the ledger for.

## Nature and purpose of the processing

The ledger sits between your AI agents and the LLM providers you already
use. It records each call (a flight recorder), refuses calls that break
a rule you set (a wall), and shows the recording to the people you allow
(a dashboard, an API, an MCP server, exports). Purposes:

- cost and usage accounting per agent, session, team, user and run;
- operational control: budgets, rate limits, loop detection, a kill
  switch per run, a fleet-wide stop, model and provider allow and deny
  lists;
- audit and investigation: what an agent was told, what it answered,
  what tools it ran, with an integrity-tagged export;
- replay and what-if analysis on captured prompts, when configured.

The ledger never changes the content of a call.

## Categories of data

| Category | Held when | Notes |
|---|---|---|
| Prompt and response content: system prompts, messages, tool definitions, tool results, model output | `AGENTICLEDGER_CAPTURE_LEVEL=full` (the default) | Whatever your agents send: this may contain personal data, confidential business data, or special-category data depending on your use. Redaction (`AGENTICLEDGER_REDACT`, `AGENTICLEDGER_REDACT_PATTERNS`) scrubs email addresses, national id numbers, card numbers, IP addresses, API keys and your own patterns before storage. `metadata` capture level stores none of this category. |
| Call metadata | Always | Model, provider, timestamps, token counts, cost, latency, HTTP status, error detail, cache usage. |
| Attribution | When the agent sends it | Session id, run id, iteration, agent name, `user_id`, environment, team (from a team card), app id, framework detected from the request shape. `user_id` is whatever your agents put in the header: an employee id, a customer id, or nothing. |
| Operator identity | When operators use the dashboard or API | The audit trail records the acting key (token name and role, or "master", or "open" on the local machine), the client address (forwarded address behind a proxy), the action and its target. |
| Credentials | Never in clear | API tokens are stored as SHA-256 hashes. Provider keys pass through and are not stored. The master key and the pairing key live in configuration and a 0600 file, not in the database. |

## Data subjects

Whoever your agents talk about or on behalf of (customers, employees,
end users of the agent), and your own operators (through the audit
trail). The ledger has no data subjects of its own.

## Storage and location

Wherever you run it. The store is a SQLite file (default
`agenticledger.db` in the working directory, or the service state
directory) or a Postgres database you name in `AGENTICLEDGER_DSN`. No
copy exists anywhere else unless you configure an export destination
(see [data flow](data-flow.md)). The software makes no cross-border
transfer; your LLM providers may, under your agreements with them.

## Retention and deletion

| Mechanism | What it does | Who |
|---|---|---|
| `AGENTICLEDGER_RETENTION_DAYS` | A background worker deletes captured calls older than N days. Unset means keep forever. | Configuration |
| `DELETE /api/users/{user_id}` | Right to erasure: deletes every captured call carrying that `user_id`. Audited. | Admin |
| `DELETE /api/sessions/{session_id}` | Deletes one session's calls. Audited. | Editor |
| `DELETE /api/projects/{name}?purge=true` | Deletes a project and everything filed under it, including sessions inherited from its runs. Audited. | Editor |
| Database controls | Backups, encryption at rest, and access to the database file or server are yours to manage; the ledger does not encrypt at rest by itself. | You |

The audit trail is not deleted by these operations: it records that the
deletion happened, not the content that was deleted.

## Security measures (summary)

Access control by key and role, keys accepted in headers only; an
audited, hash-chained log of every sensitive read and change; redaction
and metadata-only capture; TLS terminated in front of the proxy or by
the built-in dashboard listener; signed release artifacts with SBOM and
provenance; a non-root container. The full list, with evidence, is in
the [control mapping](controls-mapping.md).

## Subprocessors

None engaged by the project. See the
[subprocessor statement](subprocessors.md) for the destinations you may
configure.

---

## Annex: DPA template for a ledger you operate for others

Use this when you run the ledger as a shared service (a platform team
for internal customers, a consultancy for a client) and need to describe
the processing in your agreement. Replace the bracketed parts. The
project is not a party.

**Subject matter.** [Operator] records, controls and displays LLM calls
made by [Customer]'s agents through an Agentic Ledger deployment that
[Operator] runs.

**Duration.** For the term of [agreement], and until the retention
period below has elapsed for the last captured call.

**Nature and purpose.** Cost accounting, operational control (budgets,
rate limits, loop detection, stops, allow and deny lists), audit and
investigation of agent behaviour, and, if enabled, replay of captured
prompts to [named replay targets].

**Categories of data.** [Choose: full prompt and response content; or
metadata only]. Call metadata. Attribution fields supplied by
[Customer]'s agents: [list the headers in use, e.g. session id, run id,
user_id]. Operator identity in the audit trail.

**Data subjects.** [Customer]'s [customers / employees / end users], as
present in prompts, and [Customer]'s operators.

**Retention.** Captured calls are deleted after [N] days
(`AGENTICLEDGER_RETENTION_DAYS=[N]`). [Customer] may request erasure of
a user's calls at any time; [Operator] executes it with
`DELETE /api/users/{user_id}` within [X] business days and the audit
trail records the erasure.

**Security measures.** [Operator] runs the ledger with
`AGENTICLEDGER_API_KEY` set, minted tokens per person with the least
role needed, `AGENTICLEDGER_INGEST_KEY` or team cards on the proxy
path, TLS in front of every listener, [redaction settings], the audit
chain keyed with `AGENTICLEDGER_AUDIT_HMAC_KEY` and forwarded to
[log destination], database encryption at rest by [mechanism], and
backups [schedule and location].

**Sub-processors.** [Operator]'s LLM providers: [list]. [Any alert,
OTel or replay destination]. No others.

**Location.** The ledger and its database run in [region / facility].

**Audit.** [Customer] may request the audit trail for its data
(`GET /api/audit`, filterable by target) and a compliance export of any
session with an integrity tag keyed under
`AGENTICLEDGER_EXPORT_HMAC_KEY`.

**Return and deletion at the end.** On termination [Operator] provides
exports of [Customer]'s sessions on request and deletes the remaining
captured calls within [X] days by [project purge / user erasure /
database deletion], confirming in writing.
