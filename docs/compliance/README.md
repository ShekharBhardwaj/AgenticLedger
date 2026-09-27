# Compliance document set

The documents a security or privacy review asks for before Agentic Ledger
is allowed inside a company, written so they can be attached as they are.

Read this first, because it frames every document in the set:

**Agentic Ledger is software you run, not a service you send data to.**
You install it from PyPI or pull a signed image, and it runs on your
machine, your server, or your cluster, against your database. The project
never receives your prompts, your responses, your keys, or any telemetry.
That is why the set contains a data-processing description for your own
records rather than a contract with the project, a subprocessor statement
that lists none, and a control mapping that describes what the software
does so your auditor can place it in your programme.

**What the project does not have, stated plainly:** no SOC 2 report, no
ISO 27001 certificate, no HIPAA business associate agreement, no
penetration test report. The control mapping says which controls the
software supports and where the evidence is in the code and the release
pipeline; it does not claim an attestation that does not exist.

| Document | What it answers |
|---|---|
| [Data flow](data-flow.md) | Where data goes: every hop in and out of the proxy, what is stored, what never leaves the box. With a diagram. |
| [Data processing description](data-processing.md) | The Article 30 record and DPA annex for your own paperwork: categories of data, purposes, retention, deletion, security measures. |
| [Subprocessor statement](subprocessors.md) | Which third parties receive data. None from the project; the destinations you configure, listed. |
| [HIPAA posture](hipaa.md) | Whether and how the ledger can run inside a HIPAA programme. No BAA is offered or needed; the settings that matter. |
| [Control mapping](controls-mapping.md) | SOC 2 Trust Services Criteria and ISO 27001:2022 Annex A, mapped to product controls with the evidence for each. |
| [Support and LTS window](support-window.md) | Which versions receive fixes, for how long, and how fixes ship. |

Every claim in these documents points at something you can check: a
setting in the README, an endpoint, a test file, or a workflow in
`.github/workflows`. When the software changes, these documents change in
the same release; the changelog names them.

Related: [SECURITY.md](../../SECURITY.md) for reporting a vulnerability
and the sensitive-data notes, and the
[deployment guide](../deployment.md) for hardening a deployment.
