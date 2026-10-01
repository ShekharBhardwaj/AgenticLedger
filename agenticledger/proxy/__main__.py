"""
python -m agenticledger.proxy

Prefer the CLI: `agenticledger init` writes agenticledger.toml (one file
instead of these env vars), `agenticledger start` runs this in the
background, `agenticledger serve` in the foreground. Environment variables
always override the config file.

Reads config from environment variables:

  Core:
    AGENTICLEDGER_UPSTREAM_URL          LLM endpoint to proxy. Default: none, so each
                                        call is routed by its wire format (Anthropic-style
                                        calls to Anthropic, OpenAI-style calls to OpenAI).
                                        Set it to pin one upstream (a gateway or provider).
    AGENTICLEDGER_DSN                   Database URL (default: sqlite:///agenticledger.db,
                                      relative to the cwd; `agenticledger start` defaults to
                                      ~/.agenticledger/agenticledger.db instead)
    AGENTICLEDGER_HOST                  Bind host (default: 0.0.0.0)
    AGENTICLEDGER_PORT                  Bind port (default: 8000)
    AGENTICLEDGER_API_KEY               Master admin key; protects dashboard/API/management
                                      endpoints and bootstraps scoped API tokens (default: none)
    AGENTICLEDGER_OIDC_ISSUER           Sign in with an identity provider (OpenID Connect, code
                                        flow with PKCE): the issuer URL (default: off)
    AGENTICLEDGER_OIDC_CLIENT_ID        The client id registered with the provider
    AGENTICLEDGER_OIDC_CLIENT_SECRET    The client secret, if the provider issued one; _FILE accepted
    AGENTICLEDGER_OIDC_ROLE_MAP         group=role pairs, comma separated; a person whose groups
                                        map to nothing is refused (default: none, so everyone is)
    AGENTICLEDGER_OIDC_SCOPE_MAP        group=project pairs, comma separated (a group may repeat):
                                        a person in a mapped group sees only those projects,
                                        unfiled work included in nothing; a person in no mapped
                                        group sees everything their role allows (default: none)
    AGENTICLEDGER_OIDC_GROUPS_CLAIM     The claim carrying groups (default: groups)
    AGENTICLEDGER_OIDC_SCOPES           Scopes requested (default: openid profile email)
    AGENTICLEDGER_OIDC_PROVIDER_NAME    What the sign-in button says (default: the issuer host)
    AGENTICLEDGER_SESSION_IDLE_HOURS    A sign-in ends after this idle time (default: 12)
    AGENTICLEDGER_SESSION_MAX_HOURS     And no later than this after sign-in (default: 168)
    The redirect back from the provider uses AGENTICLEDGER_PUBLIC_URL.
    `agenticledger idp` runs a local test provider for trying this.
    AGENTICLEDGER_BEDROCK_GATEWAY_URL   Your company's Bedrock gateway (the host Claude Code's
                                        ANTHROPIC_BEDROCK_BASE_URL pointed at). Bedrock-shaped calls
                                        are forwarded there as sent, headers included; the ledger
                                        signs nothing and needs no AWS credentials (default: none)
    AGENTICLEDGER_INGEST_KEY            Require x-agenticledger-ingest-key on the proxy path,
                                      closing the open relay (default: none; open)
    AGENTICLEDGER_EXPORT_HMAC_KEY       Sign compliance exports with a tamper-evident keyed
                                      hmac-sha256 tag instead of a sha256 checksum (default: none)
    AGENTICLEDGER_EXTRA_PATHS           Extra comma-separated paths to capture (default: none)

  Performance (capture off the request hot path):
    AGENTICLEDGER_ASYNC_CAPTURE         Persist captures on a background worker so they never add
                                      latency to the call; eventually consistent (default: off)
    AGENTICLEDGER_CAPTURE_QUEUE_MAX     Max queued captures before shedding load (default: 10000)

  Data governance (applies to the stored copy only; the agent's response is untouched):
    AGENTICLEDGER_CAPTURE_LEVEL         full (default) | metadata (drop prompts/responses, keep metrics)
    AGENTICLEDGER_REDACT                Redact PII/secrets: "all" or a comma list of categories
                                      (email,ssn,credit_card,ip,api_key) (default: off)
    AGENTICLEDGER_REDACT_PATTERNS       Optional JSON of extra regexes: {"label": "regex", ...} or ["regex", ...]
    AGENTICLEDGER_RETENTION_DAYS        Delete captured calls older than N days via a background purge;
                                      unset = keep forever (default: none)
    AGENTICLEDGER_AUDIT_LOG             Record an audit trail of who viewed/exported/deleted what and
                                      token/erasure actions; set 0 to disable (default: on)
    AGENTICLEDGER_AUDIT_STRICT          Refuse (503) any audited action the log cannot record
                                      (default: off, the write is counted and logged instead)
    AGENTICLEDGER_AUDIT_HMAC_KEY        Key the audit hash chain with HMAC-SHA256 so a database
                                      writer without the key cannot re-chain (default: none,
                                      plain sha256 chain); _FILE variant accepted
    AGENTICLEDGER_AUDIT_STDOUT          Also print each audit row as one JSON line on stdout for
                                      log scrapers and SIEM agents (default: off)

  Budgets (returns HTTP 429 when exceeded, or warns; see AGENTICLEDGER_BUDGET_ACTION):
    AGENTICLEDGER_BUDGET_SESSION        Max USD per session_id (default: none)
    AGENTICLEDGER_BUDGET_AGENT          Max USD per agent_name per calendar day (default: none)
    AGENTICLEDGER_BUDGET_DAILY          Max USD total per calendar day (default: none)
    AGENTICLEDGER_BUDGET_USER           Max USD per user_id per calendar day (default: none)
    AGENTICLEDGER_BUDGET_ACTION         block (default) | warn | both
    AGENTICLEDGER_BUDGET_STATUS         HTTP status for budget blocks: 429 (default, sent with
                                        Retry-After) or 402; clients never retry a 402
    AGENTICLEDGER_BUDGET_UNPRICED       allow (default) | refuse: what a budget does with a model
                                        that has no price, since it cannot be counted

  Rate limits (returns HTTP 429, sliding 60-second window):
    AGENTICLEDGER_RATE_LIMIT_RPM        Max requests per minute globally (default: none)
    AGENTICLEDGER_RATE_LIMIT_SESSION_RPM  Max requests per minute per session_id (default: none)
    AGENTICLEDGER_RATE_LIMIT_AGENT_RPM  Max requests per minute per agent_name (default: none)
    AGENTICLEDGER_RATE_LIMIT_USER_RPM   Max requests per minute per user_id (default: none)

  Allow and deny lists (refuse only, the rule named; every refusal is recorded):
    AGENTICLEDGER_ALLOW_MODELS          Comma-separated model patterns; when set, only these pass
                                        (globs: claude-*, gpt-4o) (default: none)
    AGENTICLEDGER_DENY_MODELS           Model patterns refused outright; deny wins (default: none)
    AGENTICLEDGER_ALLOW_PROVIDERS       Provider names or patterns; when set, only these pass
                                        (openai, anthropic, bedrock, azure-openai) (default: none)
    AGENTICLEDGER_DENY_PROVIDERS        Providers refused outright (default: none)
    Team cards carry their own four lists (POST /api/tokens); the fleet lists
    always apply and a card can only narrow them. Stop all calls, the
    fleet-wide emergency stop, is POST /api/stop (lift: DELETE /api/stop).

  Alerts (POST to webhook on threshold breach; does not block calls):
    AGENTICLEDGER_ALERT_WEBHOOK_URL     Webhook URL for alerts (default: none)
    AGENTICLEDGER_DIGEST_HOUR           UTC hour (0-23) to POST a daily spend digest for the
                                        last 24h to the alert webhook (default: off)
    AGENTICLEDGER_ALERT_COST_PER_CALL   Alert if single call costs more than $X (default: none)
    AGENTICLEDGER_ALERT_LATENCY_MS      Alert if single call takes longer than Xms (default: none)
    AGENTICLEDGER_ALERT_ERROR_RATE      Alert if session error rate exceeds X, e.g. 0.5 (default: none)
    AGENTICLEDGER_ALERT_DAILY_SPEND     Alert when daily spend crosses $X (default: none)
    AGENTICLEDGER_ALERT_FORMAT          auto (default) | generic | slack | discord | pagerduty:
                                        the payload shape; auto reads the webhook host
    AGENTICLEDGER_ALERT_PAGERDUTY_KEY   PagerDuty Events v2 integration key, needed when the
                                        webhook is events.pagerduty.com; _FILE variant accepted
    AGENTICLEDGER_PUBLIC_URL            Where the dashboard is reachable (https://ledger.example),
                                        so notifications about a run or session link to it
    Every notification is retried three times with backoff, deduplicated
    per window, and recorded (GET /api/notifications, the Settings page).

  OpenTelemetry (requires pip install 'agentic-ledger[otel]'):
    AGENTICLEDGER_OTEL_ENDPOINT         OTLP/HTTP base URL, e.g. http://localhost:4318 (default: none)
    AGENTICLEDGER_OTEL_SERVICE_NAME     service.name reported to collector (default: agenticledger)
    AGENTICLEDGER_OTEL_HEADERS          Comma-separated key=value auth headers (default: none)

  Replay (re-execute captured calls from the dashboard/API):
    AGENTICLEDGER_REPLAY_API_KEY      Key for same-provider replay through the proxy's own
                                      upstream. The proxy never stores agent credentials,
                                      so replay needs its own. Unset = off (default)
    AGENTICLEDGER_REPLAY_OPENAI_KEY   Cross-provider targets: replay any capture on this
    AGENTICLEDGER_REPLAY_OPENAI_URL   provider. URL defaults to the provider's API; point
    AGENTICLEDGER_REPLAY_ANTHROPIC_KEY  it at LM Studio (http://localhost:1234, any key)
    AGENTICLEDGER_REPLAY_ANTHROPIC_URL  for free local replay

  Pricing overrides (merged over the built-in table at startup):
    AGENTICLEDGER_PRICING               Inline JSON, e.g. '{"gpt-4o": [2.50, 10.00], "my-model": [1.00, 2.00]}'
    AGENTICLEDGER_PRICING_FILE          Path to a JSON file with the same format

  Secrets from files (keeps keys out of shell history; the Docker-secrets
  pattern): every key above also accepts a _FILE variant naming a file whose
  contents are the key. AGENTICLEDGER_API_KEY_FILE, AGENTICLEDGER_INGEST_KEY_FILE,
  AGENTICLEDGER_REPLAY_API_KEY_FILE, AGENTICLEDGER_REPLAY_OPENAI_KEY_FILE, …
"""

import logging
import os
import sys

import uvicorn

from ..config import apply_config
from .alerts import AlertConfig
from .app import _secret_env, create_app
from .notify import NotifyConfig
from .oidc import OIDCConfig, parse_role_map, parse_scope_map
from .otel import init_otel
from .policy import Policy
from .ratelimit import RateLimitConfig
from .redact import build_redactor

# The config file (agenticledger.toml) fills the environment FIRST, via
# setdefault, so anything already exported still wins. Every read below
# stays a plain env read.
_config_path = apply_config()


class _QuietFilter(logging.Filter):
    """Suppress dashboard polling from uvicorn access logs."""
    _NOISY = ("/api/sessions", "/api/search", "/session/", "/export/", "GET / ", "GET /ws")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._NOISY)


logging.getLogger("uvicorn.access").addFilter(_QuietFilter())


def _float_env(key: str):
    val = os.environ.get(key)
    return float(val) if val else None


_otel_endpoint = os.environ.get("AGENTICLEDGER_OTEL_ENDPOINT")
if _otel_endpoint:
    _otel_headers: dict[str, str] = {}
    for pair in os.environ.get("AGENTICLEDGER_OTEL_HEADERS", "").split(","):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            _otel_headers[k.strip()] = v.strip()
    init_otel(
        endpoint=_otel_endpoint,
        service_name=os.environ.get("AGENTICLEDGER_OTEL_SERVICE_NAME", "agenticledger"),
        headers=_otel_headers or None,
    )

# No configured upstream: route each call by its wire format instead of
# defaulting everyone to OpenAI (an Anthropic agent would just bounce).
_upstream_env = os.environ.get("AGENTICLEDGER_UPSTREAM_URL")
upstream_auto = _upstream_env is None
upstream_url  = _upstream_env or "https://api.openai.com"
dsn          = os.environ.get("AGENTICLEDGER_DSN", "sqlite:///agenticledger.db")
host         = os.environ.get("AGENTICLEDGER_HOST", "0.0.0.0")
port         = int(os.environ.get("AGENTICLEDGER_PORT", "8000"))

app = create_app(
    upstream_url=upstream_url,
    dsn=dsn,
    upstream_auto=upstream_auto,
    bedrock_gateway_url=os.environ.get("AGENTICLEDGER_BEDROCK_GATEWAY_URL") or None,
    budget_session=_float_env("AGENTICLEDGER_BUDGET_SESSION"),
    budget_user=_float_env("AGENTICLEDGER_BUDGET_USER"),
    budget_status=int(os.environ.get("AGENTICLEDGER_BUDGET_STATUS", "429")),
    budget_agent=_float_env("AGENTICLEDGER_BUDGET_AGENT"),
    budget_daily=_float_env("AGENTICLEDGER_BUDGET_DAILY"),
    budget_action=os.environ.get("AGENTICLEDGER_BUDGET_ACTION", "block"),
    budget_unpriced=os.environ.get("AGENTICLEDGER_BUDGET_UNPRICED", "allow"),
    policy=Policy.from_values(
        allow_models=os.environ.get("AGENTICLEDGER_ALLOW_MODELS"),
        deny_models=os.environ.get("AGENTICLEDGER_DENY_MODELS"),
        allow_providers=os.environ.get("AGENTICLEDGER_ALLOW_PROVIDERS"),
        deny_providers=os.environ.get("AGENTICLEDGER_DENY_PROVIDERS"),
    ),
    rate_limit_config=RateLimitConfig(
        global_rpm=  int(os.environ["AGENTICLEDGER_RATE_LIMIT_RPM"])          if os.environ.get("AGENTICLEDGER_RATE_LIMIT_RPM")          else None,
        session_rpm= int(os.environ["AGENTICLEDGER_RATE_LIMIT_SESSION_RPM"])  if os.environ.get("AGENTICLEDGER_RATE_LIMIT_SESSION_RPM")  else None,
        agent_rpm=   int(os.environ["AGENTICLEDGER_RATE_LIMIT_AGENT_RPM"])    if os.environ.get("AGENTICLEDGER_RATE_LIMIT_AGENT_RPM")    else None,
        user_rpm=    int(os.environ["AGENTICLEDGER_RATE_LIMIT_USER_RPM"])     if os.environ.get("AGENTICLEDGER_RATE_LIMIT_USER_RPM")     else None,
    ),
    alert_config=AlertConfig(
        webhook_url=os.environ.get("AGENTICLEDGER_ALERT_WEBHOOK_URL"),
        cost_per_call=_float_env("AGENTICLEDGER_ALERT_COST_PER_CALL"),
        latency_ms=_float_env("AGENTICLEDGER_ALERT_LATENCY_MS"),
        error_rate=_float_env("AGENTICLEDGER_ALERT_ERROR_RATE"),
        daily_spend=_float_env("AGENTICLEDGER_ALERT_DAILY_SPEND"),
    ),
    oidc_config=OIDCConfig(
        issuer=os.environ.get("AGENTICLEDGER_OIDC_ISSUER") or None,
        client_id=os.environ.get("AGENTICLEDGER_OIDC_CLIENT_ID") or None,
        client_secret=_secret_env("AGENTICLEDGER_OIDC_CLIENT_SECRET"),
        scopes=os.environ.get("AGENTICLEDGER_OIDC_SCOPES") or "openid profile email",
        groups_claim=os.environ.get("AGENTICLEDGER_OIDC_GROUPS_CLAIM") or "groups",
        role_map=parse_role_map(os.environ.get("AGENTICLEDGER_OIDC_ROLE_MAP")),
        scope_map=parse_scope_map(os.environ.get("AGENTICLEDGER_OIDC_SCOPE_MAP")),
        public_url=os.environ.get("AGENTICLEDGER_PUBLIC_URL") or None,
        idle_seconds=float(os.environ.get("AGENTICLEDGER_SESSION_IDLE_HOURS", "12")) * 3600,
        max_seconds=float(os.environ.get("AGENTICLEDGER_SESSION_MAX_HOURS", "168")) * 3600,
        provider_name=os.environ.get("AGENTICLEDGER_OIDC_PROVIDER_NAME") or None,
    ),
    notify_config=NotifyConfig(
        webhook_url=os.environ.get("AGENTICLEDGER_ALERT_WEBHOOK_URL"),
        format=os.environ.get("AGENTICLEDGER_ALERT_FORMAT", "auto"),
        pagerduty_key=_secret_env("AGENTICLEDGER_ALERT_PAGERDUTY_KEY"),
        public_url=os.environ.get("AGENTICLEDGER_PUBLIC_URL") or None,
    ),
    async_capture=os.environ.get("AGENTICLEDGER_ASYNC_CAPTURE", "").lower() in ("1", "true", "yes", "on"),
    capture_queue_max=int(os.environ.get("AGENTICLEDGER_CAPTURE_QUEUE_MAX", "10000")),
    capture_level=os.environ.get("AGENTICLEDGER_CAPTURE_LEVEL", "full"),
    redactor=build_redactor(
        os.environ.get("AGENTICLEDGER_REDACT", ""),
        os.environ.get("AGENTICLEDGER_REDACT_PATTERNS", ""),
    ),
    retention_days=_float_env("AGENTICLEDGER_RETENTION_DAYS"),
    audit_enabled=os.environ.get("AGENTICLEDGER_AUDIT_LOG", "1").lower() not in ("0", "false", "no", "off"),
    audit_strict=os.environ.get("AGENTICLEDGER_AUDIT_STRICT", "").lower() in ("1", "true", "yes", "on"),
    audit_hmac_key=_secret_env("AGENTICLEDGER_AUDIT_HMAC_KEY"),
    audit_stdout=os.environ.get("AGENTICLEDGER_AUDIT_STDOUT", "").lower() in ("1", "true", "yes", "on"),
    loop_action=os.environ.get("AGENTICLEDGER_LOOP_ACTION", "warn"),
    loop_max_steps=int(os.environ["AGENTICLEDGER_LOOP_MAX_STEPS"]) if os.environ.get("AGENTICLEDGER_LOOP_MAX_STEPS") else None,
    loop_repeat_threshold=int(os.environ.get("AGENTICLEDGER_LOOP_REPEAT_THRESHOLD", "3")),
    loop_run_gap_seconds=float(os.environ.get("AGENTICLEDGER_LOOP_RUN_GAP_SECONDS", "900")),
    completion_promise=os.environ.get("AGENTICLEDGER_COMPLETION_PROMISE") or None,
    digest_hour=int(os.environ["AGENTICLEDGER_DIGEST_HOUR"]) if os.environ.get("AGENTICLEDGER_DIGEST_HOUR") else None,
    replay_api_key=_secret_env("AGENTICLEDGER_REPLAY_API_KEY"),
    replay_targets={
        prov: {
            "url": os.environ.get(f"AGENTICLEDGER_REPLAY_{prov.upper()}_URL", default_url),
            "key": _secret_env(f"AGENTICLEDGER_REPLAY_{prov.upper()}_KEY"),
        }
        for prov, default_url in (
            ("openai", "https://api.openai.com"),
            ("anthropic", "https://api.anthropic.com"),
        )
        if _secret_env(f"AGENTICLEDGER_REPLAY_{prov.upper()}_KEY")
    } or None,
)

try:
    from importlib.metadata import version as _pkg_version
    _version = _pkg_version("agentic-ledger")
except Exception:
    _version = "0.0.0"
# Version banner so testers can see at a glance what they are running -
# a stale venv silently serving an old release looks identical otherwise.
print(
    f"Agentic Ledger v{_version} - proxying "
    + ("by call format (anthropic → api.anthropic.com, openai → api.openai.com)"
       if upstream_auto else upstream_url) + " - "
    f"dashboard: http://{'localhost' if host == '0.0.0.0' else host}:{port}"
    + (f" - config: {_config_path}" if _config_path else ""),
    file=sys.stderr,
    flush=True,
)

_logger = logging.getLogger("agenticledger")
if not _secret_env("AGENTICLEDGER_INGEST_KEY"):
    _logger.warning(
        "AGENTICLEDGER_INGEST_KEY is not set: the proxy will forward requests from "
        "ANYONE who can reach it (open relay). Set it to require x-agenticledger-ingest-key "
        "before exposing the proxy beyond localhost."
    )
if not _secret_env("AGENTICLEDGER_API_KEY"):
    _logger.info(
        "AGENTICLEDGER_API_KEY is not set: the dashboard is open on this machine; "
        "visitors from other machines must present the auto-generated pairing key "
        "(`agenticledger share` prints the pairing link)."
    )

# Direct-LAN https (#118): AGENTICLEDGER_TLS=1 adds a DASHBOARD-ONLY
# https listener beside the plain-http agent port. Self-signed, so the
# phone warns once; SDK clients keep the http port and never see it.
if os.environ.get("AGENTICLEDGER_TLS", "").lower() in ("1", "true", "auto"):
    import asyncio
    from pathlib import Path as _Path

    from agenticledger.service import _lan_ip
    from agenticledger.tls import ensure_cert

    tls_port = int(os.environ.get("AGENTICLEDGER_TLS_PORT", "8443"))
    cert, key = ensure_cert(_Path.home() / ".agenticledger", _lan_ip())
    print(f"  https (dashboard, self-signed): https://localhost:{tls_port}/app",
          file=sys.stderr, flush=True)

    async def _serve_both() -> None:
        plain = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="info"))
        secure = uvicorn.Server(uvicorn.Config(
            app, host=host, port=tls_port, log_level="info",
            ssl_certfile=str(cert), ssl_keyfile=str(key)))
        # One app, two doors: agents through http, the phone through https.
        await asyncio.gather(plain.serve(), secure.serve())

    asyncio.run(_serve_both())
else:
    uvicorn.run(app, host=host, port=port)
