"""
python -m agenticledger.proxy

Prefer the CLI: `agenticledger init` writes agenticledger.toml (one file
instead of these env vars), `agenticledger start` runs this in the
background, `agenticledger serve` in the foreground. Environment variables
always override the config file.

Reads config from environment variables:

  Core:
    AGENTICLEDGER_UPSTREAM_URL          LLM endpoint to proxy (default: https://api.openai.com)
    AGENTICLEDGER_DSN                   Database URL (default: sqlite:///agenticledger.db)
    AGENTICLEDGER_HOST                  Bind host (default: 0.0.0.0)
    AGENTICLEDGER_PORT                  Bind port (default: 8000)
    AGENTICLEDGER_API_KEY               Master admin key; protects dashboard/API/management
                                      endpoints and bootstraps scoped API tokens (default: none)
    AGENTICLEDGER_INGEST_KEY            Require x-agenticledger-ingest-key on the proxy path,
                                      closing the open relay (default: none — open)
    AGENTICLEDGER_EXPORT_HMAC_KEY       Sign compliance exports with a tamper-evident keyed
                                      hmac-sha256 tag instead of a sha256 checksum (default: none)
    AGENTICLEDGER_EXTRA_PATHS           Extra comma-separated paths to capture (default: none)

  Performance (capture off the request hot path):
    AGENTICLEDGER_ASYNC_CAPTURE         Persist captures on a background worker so they never add
                                      latency to the call — eventually consistent (default: off)
    AGENTICLEDGER_CAPTURE_QUEUE_MAX     Max queued captures before shedding load (default: 10000)

  Data governance (applies to the stored copy only — the agent's response is untouched):
    AGENTICLEDGER_CAPTURE_LEVEL         full (default) | metadata (drop prompts/responses, keep metrics)
    AGENTICLEDGER_REDACT                Redact PII/secrets: "all" or a comma list of categories
                                      (email,ssn,credit_card,ip,api_key) (default: off)
    AGENTICLEDGER_REDACT_PATTERNS       Optional JSON of extra regexes — {"label": "regex", ...} or ["regex", ...]
    AGENTICLEDGER_RETENTION_DAYS        Delete captured calls older than N days via a background purge;
                                      unset = keep forever (default: none)
    AGENTICLEDGER_AUDIT_LOG             Record an audit trail of who viewed/exported/deleted what and
                                      token/erasure actions; set 0 to disable (default: on)

  Budgets (returns HTTP 429 when exceeded, or warns — see AGENTICLEDGER_BUDGET_ACTION):
    AGENTICLEDGER_BUDGET_SESSION        Max USD per session_id (default: none)
    AGENTICLEDGER_BUDGET_AGENT          Max USD per agent_name per calendar day (default: none)
    AGENTICLEDGER_BUDGET_DAILY          Max USD total per calendar day (default: none)
    AGENTICLEDGER_BUDGET_USER           Max USD per user_id per calendar day (default: none)
    AGENTICLEDGER_BUDGET_ACTION         block (default) | warn | both
    AGENTICLEDGER_BUDGET_STATUS         HTTP status for budget blocks: 429 (default, sent with
                                        Retry-After) or 402 — clients never retry a 402

  Rate limits (returns HTTP 429, sliding 60-second window):
    AGENTICLEDGER_RATE_LIMIT_RPM        Max requests per minute globally (default: none)
    AGENTICLEDGER_RATE_LIMIT_SESSION_RPM  Max requests per minute per session_id (default: none)
    AGENTICLEDGER_RATE_LIMIT_AGENT_RPM  Max requests per minute per agent_name (default: none)
    AGENTICLEDGER_RATE_LIMIT_USER_RPM   Max requests per minute per user_id (default: none)

  Alerts (POST to webhook on threshold breach — does not block calls):
    AGENTICLEDGER_ALERT_WEBHOOK_URL     Webhook URL for alerts (default: none)
    AGENTICLEDGER_DIGEST_HOUR           UTC hour (0-23) to POST a daily spend digest for the
                                        last 24h to the alert webhook (default: off)
    AGENTICLEDGER_ALERT_COST_PER_CALL   Alert if single call costs more than $X (default: none)
    AGENTICLEDGER_ALERT_LATENCY_MS      Alert if single call takes longer than Xms (default: none)
    AGENTICLEDGER_ALERT_ERROR_RATE      Alert if session error rate exceeds X, e.g. 0.5 (default: none)
    AGENTICLEDGER_ALERT_DAILY_SPEND     Alert when daily spend crosses $X (default: none)

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
    AGENTICLEDGER_PRICING               Inline JSON — e.g. '{"gpt-4o": [2.50, 10.00], "my-model": [1.00, 2.00]}'
    AGENTICLEDGER_PRICING_FILE          Path to a JSON file with the same format

  Secrets from files (keeps keys out of shell history — the Docker-secrets
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
from .otel import init_otel
from .ratelimit import RateLimitConfig
from .redact import build_redactor

# The config file (agenticledger.toml) fills the environment FIRST — via
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
    budget_session=_float_env("AGENTICLEDGER_BUDGET_SESSION"),
    budget_user=_float_env("AGENTICLEDGER_BUDGET_USER"),
    budget_status=int(os.environ.get("AGENTICLEDGER_BUDGET_STATUS", "429")),
    budget_agent=_float_env("AGENTICLEDGER_BUDGET_AGENT"),
    budget_daily=_float_env("AGENTICLEDGER_BUDGET_DAILY"),
    budget_action=os.environ.get("AGENTICLEDGER_BUDGET_ACTION", "block"),
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
    async_capture=os.environ.get("AGENTICLEDGER_ASYNC_CAPTURE", "").lower() in ("1", "true", "yes", "on"),
    capture_queue_max=int(os.environ.get("AGENTICLEDGER_CAPTURE_QUEUE_MAX", "10000")),
    capture_level=os.environ.get("AGENTICLEDGER_CAPTURE_LEVEL", "full"),
    redactor=build_redactor(
        os.environ.get("AGENTICLEDGER_REDACT", ""),
        os.environ.get("AGENTICLEDGER_REDACT_PATTERNS", ""),
    ),
    retention_days=_float_env("AGENTICLEDGER_RETENTION_DAYS"),
    audit_enabled=os.environ.get("AGENTICLEDGER_AUDIT_LOG", "1").lower() not in ("0", "false", "no", "off"),
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
# Version banner so testers can see at a glance what they are running —
# a stale venv silently serving an old release looks identical otherwise.
print(
    f"Agentic Ledger v{_version} — proxying "
    + ("by call format (anthropic → api.anthropic.com, openai → api.openai.com)"
       if upstream_auto else upstream_url) + " — "
    f"dashboard: http://{'localhost' if host == '0.0.0.0' else host}:{port}"
    + (f" — config: {_config_path}" if _config_path else ""),
    file=sys.stderr,
    flush=True,
)

_logger = logging.getLogger("agenticledger")
if not _secret_env("AGENTICLEDGER_INGEST_KEY"):
    _logger.warning(
        "AGENTICLEDGER_INGEST_KEY is not set — the proxy will forward requests from "
        "ANYONE who can reach it (open relay). Set it to require x-agenticledger-ingest-key "
        "before exposing the proxy beyond localhost."
    )
if not _secret_env("AGENTICLEDGER_API_KEY"):
    _logger.warning(
        "AGENTICLEDGER_API_KEY is not set — the dashboard, API, and MCP endpoints are "
        "UNAUTHENTICATED. Set it (or configure API tokens) before exposing Agentic Ledger "
        "beyond localhost."
    )

uvicorn.run(app, host=host, port=port)
