# Deploying Agentic Ledger in production

Agentic Ledger runs fine on a laptop with zero configuration. This page is
for the other case: running it as a shared service that an audit, security,
or platform team will look at. It covers where to get the artifacts and how
to verify them, how to harden the runtime, and what the honest scaling
limits are today.

---

## Getting the artifacts

| Channel | Name | Notes |
|---|---|---|
| PyPI | [`agentic-ledger`](https://pypi.org/project/agentic-ledger/) | Published via trusted publishing (OIDC, no long-lived tokens) with PEP 740 attestations. |
| GHCR | `ghcr.io/shekharbhardwaj/agentic-ledger` | Multi-arch (`linux/amd64`, `linux/arm64`), signed, with SBOM + provenance attestations. |
| Docker Hub | `docker.io` mirror of the same image | Same digest as GHCR — pull from whichever your network prefers. |

**Enterprise mirrors (Artifactory, Nexus, AWS CodeArtifact, Azure
Artifacts):** no extra publishing step is needed. These products proxy the
public indexes — point a *remote repository* at PyPI for the package
(`pip install agentic-ledger` through your mirror URL) and a *remote Docker
repository* at GHCR or Docker Hub for the image. Your mirror caches and
scans the artifacts under your own policies.

## Verifying what you pull

Release images are signed with [Sigstore cosign](https://docs.sigstore.dev/)
(keyless — the signature is bound to the GitHub Actions release workflow
identity, not a key someone could lose):

```bash
cosign verify ghcr.io/shekharbhardwaj/agentic-ledger:latest \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp 'https://github\.com/ShekharBhardwaj/AgenticLedger/\.github/workflows/release\.yml@.*'
```

Each image also carries BuildKit **SBOM and SLSA provenance attestations**
(inspect with `docker buildx imagetools inspect <image> --format
'{{ json .SBOM }}'`), and every GitHub release attaches a standalone SPDX
SBOM (`sbom-agenticledger-image.spdx.json`) for ingestion into dependency
scanners.

Python packages on PyPI carry PEP 740 publish attestations — PyPI displays
the verified GitHub repository and workflow on the file details page.

## Building the image yourself

Cold builds work from a plain checkout — no wheel required:

```bash
docker build -t agenticledger .
```

With nothing in `dist/`, the build installs the latest published release
from PyPI (pin one with `--build-arg AGENTICLEDGER_VERSION=0.3.3`). If you
drop a locally built wheel into `dist/`, it takes precedence — that is the
path the release pipeline uses.

---

## Runtime hardening

### The container is non-root by default

The proxy runs as user `agenticledger` (uid 10001). Two consequences:

- **Named volumes** work out of the box — `/data` ownership is inherited on
  first mount.
- **Bind mounts on Linux hosts** need the directory handed over once:

  ```bash
  mkdir -p data && sudo chown 10001 data
  ```

  (Docker Desktop on macOS/Windows maps ownership automatically.)

You can tighten further; the image needs nothing beyond `/data`:

```bash
docker run --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  -v agenticledger-data:/data -p 8000:8000 \
  -e AGENTICLEDGER_UPSTREAM_URL=https://api.openai.com \
  ghcr.io/shekharbhardwaj/agentic-ledger:latest
```

### TLS: terminate in front of the proxy

The proxy serves plain HTTP and deliberately does not implement TLS —
terminate it at a reverse proxy or your ingress, like any other internal
service. Keep the proxy bound to a private interface and let only the
terminator reach it.

Caddy (automatic certificates):

```text
ledger.internal.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

nginx:

```nginx
server {
    listen 443 ssl;
    server_name ledger.internal.example.com;
    ssl_certificate     /etc/ssl/ledger.crt;
    ssl_certificate_key /etc/ssl/ledger.key;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        # streaming responses (SSE) pass through untouched
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

`proxy_buffering off` matters: agents stream, and buffering breaks
token-by-token passthrough.

### Lock the doors

Every deployment that leaves localhost should set:

| Variable | What it closes |
|---|---|
| `AGENTICLEDGER_API_KEY` | Dashboard, read, and management endpoints require the admin key; scoped tokens can then be minted for narrower roles. |
| `AGENTICLEDGER_INGEST_KEY` | The proxy forwards traffic only when the caller presents the matching `x-agenticledger-ingest-key` header — closes the open-relay hole where anyone who can reach the proxy can spend your LLM credits. |
| `AGENTICLEDGER_HOST=127.0.0.1` | When the proxy and its TLS terminator share a host, don't listen on all interfaces. |
| `AGENTICLEDGER_EXPORT_HMAC_KEY` | Compliance exports get a keyed tamper-evident integrity tag instead of a plain hash. |

### Protect the data you capture

Captured prompts are the most sensitive thing in this system. The controls,
in escalating order:

- `AGENTICLEDGER_REDACT=all` (or a list: `email,ssn,credit_card,ip,api_key`) —
  scrub PII/secrets before they are stored; `AGENTICLEDGER_REDACT_PATTERNS`
  adds your own regexes.
- `AGENTICLEDGER_CAPTURE_LEVEL=metadata` — store only metrics and metadata
  (model, tokens, cost, latency, agent, status), never prompt/response
  bodies.
- `AGENTICLEDGER_RETENTION_DAYS=30` — a background worker purges older calls.
- `AGENTICLEDGER_AUDIT_LOG` is on by default — who viewed, exported, or
  deleted what.

### Database

SQLite is the default and is genuinely fine for a single busy host. For a
shared service, use Postgres:

```bash
pip install "agentic-ledger[postgres]"
AGENTICLEDGER_DSN=postgresql://user:password@db-host/agenticledger
```

For SQLite backups, snapshot the file with the proxy stopped, or use
`sqlite3 /data/agenticledger.db ".backup /backup/agenticledger.db"` while
running. Postgres backups are your standard `pg_dump`.

---

## Scaling: the honest section

Run **one replica**. Budget enforcement, rate limiting, and the loop
engine's thread/run tracking keep working state in process memory — two
replicas behind a load balancer would each see half the traffic and enforce
half the truth. What that costs you in practice is small: the proxy is an
async passthrough, and a single instance comfortably handles the request
rates agent fleets generate (LLM calls are seconds-long; the proxy's added
work is milliseconds). Scale vertically first; shared state for
multi-replica deployments (Redis) is on the roadmap.

What you *can* do today:

- Point many machines' agents at one central proxy (that is the intended
  shared-service shape).
- Use Postgres so dashboards, the MCP server (`agenticledger mcp`), and API
  consumers read the ledger without touching the proxy's write path.
- Watch `/metrics` (Prometheus format) and probe `/health` and `/readyz` —
  the container image ships a `HEALTHCHECK` that hits `/health`.

## Deployment checklist

- [ ] Image pulled by digest or verified with `cosign verify`
- [ ] `AGENTICLEDGER_API_KEY` and `AGENTICLEDGER_INGEST_KEY` set
- [ ] TLS terminated in front; proxy not reachable directly
- [ ] Redaction/capture level/retention chosen deliberately
- [ ] Postgres DSN for shared deployments; backups scheduled
- [ ] `/metrics` scraped; `/health` wired to your orchestrator's probes
- [ ] Single replica (see above), sized with headroom
