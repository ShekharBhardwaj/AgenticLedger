# Subprocessor statement

**The Agentic Ledger project engages no subprocessors, because it
processes no data on your behalf.** The software runs where you install
it. Nothing you capture is transmitted to the project or to anyone the
project has engaged. There is no hosted service, no telemetry, and no
support channel that receives your data.

## Destinations you may configure

A deployment can send data to third parties, but only to ones you name.
Each is off until you turn it on, and each is your own vendor under your
own agreement. For your subprocessor register, they are:

| Destination | Turned on by | Receives |
|---|---|---|
| Your LLM providers (OpenAI, Anthropic, Azure OpenAI, AWS Bedrock, or a local model server) | Pointing agents at the proxy; the proxy forwards to `AGENTICLEDGER_UPSTREAM_URL` or by wire format | Every forwarded call, exactly as the agent sent it. The same data these providers received before the ledger existed. |
| An alert webhook (Slack, PagerDuty, your own relay) | `AGENTICLEDGER_ALERT_WEBHOOK_URL` | Alert payloads and daily digests: thresholds, values, ids, totals. No prompt content. |
| An OpenTelemetry collector or vendor | `AGENTICLEDGER_OTEL_ENDPOINT` | Spans carrying call metadata only (model, tokens, cost, ids), never prompt or response content, and audit rows as logs. |
| Replay targets | `AGENTICLEDGER_REPLAY_API_KEY` or `AGENTICLEDGER_REPLAY_*` | A captured prompt, when an operator replays it there. |
| Cloudflare (quick tunnel) | An operator runs `agenticledger share` | Dashboard traffic in transit, for as long as the tunnel is open. |
| GitHub | An operator runs `agenticledger pricing update` | Nothing; a read-only download of pricing packs. |

## Distribution

Installing and upgrading downloads from PyPI (`agentic-ledger`),
GitHub Container Registry (`ghcr.io/shekharbhardwaj/agentic-ledger`) or
Docker Hub. Images are signed with Sigstore cosign and ship an SBOM and
provenance attestation; the [deployment guide](../deployment.md)
shows how to verify what you pull. Downloads send nothing about your
deployment.

## Changes

This statement changes only when the software gains a new outbound
destination. Any such change is named in the changelog for the release
that introduces it, and the destination is off by default.
