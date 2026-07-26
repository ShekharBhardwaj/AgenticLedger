"""
python -m agentledger.proxy

Reads config from environment variables:

  Core:
    AGENTLEDGER_UPSTREAM_URL          LLM endpoint to proxy (default: https://api.openai.com)
    AGENTLEDGER_DSN                   Database URL (default: sqlite:///agentledger.db)
    AGENTLEDGER_HOST                  Bind host (default: 0.0.0.0)
    AGENTLEDGER_PORT                  Bind port (default: 8000)
    AGENTLEDGER_API_KEY               Master admin key; protects dashboard/API/management
                                      endpoints and bootstraps scoped API tokens (default: none)
    AGENTLEDGER_INGEST_KEY            Require x-agentledger-ingest-key on the proxy path,
                                      closing the open relay (default: none — open)
    AGENTLEDGER_EXPORT_HMAC_KEY       Sign compliance exports with a tamper-evident keyed
                                      hmac-sha256 tag instead of a sha256 checksum (default: none)
    AGENTLEDGER_EXTRA_PATHS           Extra comma-separated paths to capture (default: none)

  Performance (capture off the request hot path):
    AGENTLEDGER_ASYNC_CAPTURE         Persist captures on a background worker so they never add
                                      latency to the call — eventually consistent (default: off)
    AGENTLEDGER_CAPTURE_QUEUE_MAX     Max queued captures before shedding load (default: 10000)

  Data governance (applies to the stored copy only — the agent's response is untouched):
    AGENTLEDGER_CAPTURE_LEVEL         full (default) | metadata (drop prompts/responses, keep metrics)
    AGENTLEDGER_REDACT                Redact PII/secrets: "all" or a comma list of categories
                                      (email,ssn,credit_card,ip,api_key) (default: off)
    AGENTLEDGER_REDACT_PATTERNS       Optional JSON of extra regexes — {"label": "regex", ...} or ["regex", ...]
    AGENTLEDGER_RETENTION_DAYS        Delete captured calls older than N days via a background purge;
                                      unset = keep forever (default: none)
    AGENTLEDGER_AUDIT_LOG             Record an audit trail of who viewed/exported/deleted what and
                                      token/erasure actions; set 0 to disable (default: on)

  Budgets (returns HTTP 429 when exceeded, or warns — see AGENTLEDGER_BUDGET_ACTION):
    AGENTLEDGER_BUDGET_SESSION        Max USD per session_id (default: none)
    AGENTLEDGER_BUDGET_AGENT          Max USD per agent_name per calendar day (default: none)
    AGENTLEDGER_BUDGET_DAILY          Max USD total per calendar day (default: none)
    AGENTLEDGER_BUDGET_ACTION         block (default) | warn | both

  Rate limits (returns HTTP 429, sliding 60-second window):
    AGENTLEDGER_RATE_LIMIT_RPM        Max requests per minute globally (default: none)
    AGENTLEDGER_RATE_LIMIT_SESSION_RPM  Max requests per minute per session_id (default: none)
    AGENTLEDGER_RATE_LIMIT_AGENT_RPM  Max requests per minute per agent_name (default: none)
    AGENTLEDGER_RATE_LIMIT_USER_RPM   Max requests per minute per user_id (default: none)

  Alerts (POST to webhook on threshold breach — does not block calls):
    AGENTLEDGER_ALERT_WEBHOOK_URL     Webhook URL for alerts (default: none)
    AGENTLEDGER_ALERT_COST_PER_CALL   Alert if single call costs more than $X (default: none)
    AGENTLEDGER_ALERT_LATENCY_MS      Alert if single call takes longer than Xms (default: none)
    AGENTLEDGER_ALERT_ERROR_RATE      Alert if session error rate exceeds X, e.g. 0.5 (default: none)
    AGENTLEDGER_ALERT_DAILY_SPEND     Alert when daily spend crosses $X (default: none)

  OpenTelemetry (requires pip install 'agentic-ledger[otel]'):
    AGENTLEDGER_OTEL_ENDPOINT         OTLP/HTTP base URL, e.g. http://localhost:4318 (default: none)
    AGENTLEDGER_OTEL_SERVICE_NAME     service.name reported to collector (default: agentledger)
    AGENTLEDGER_OTEL_HEADERS          Comma-separated key=value auth headers (default: none)

  Pricing overrides (merged over the built-in table at startup):
    AGENTLEDGER_PRICING               Inline JSON — e.g. '{"gpt-4o": [2.50, 10.00], "my-model": [1.00, 2.00]}'
    AGENTLEDGER_PRICING_FILE          Path to a JSON file with the same format
"""

import logging
import os

import uvicorn

from .alerts import AlertConfig
from .app import create_app
from .otel import init_otel
from .ratelimit import RateLimitConfig
from .redact import build_redactor


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


_otel_endpoint = os.environ.get("AGENTLEDGER_OTEL_ENDPOINT")
if _otel_endpoint:
    _otel_headers: dict[str, str] = {}
    for pair in os.environ.get("AGENTLEDGER_OTEL_HEADERS", "").split(","):
        pair = pair.strip()
        if "=" in pair:
            k, _, v = pair.partition("=")
            _otel_headers[k.strip()] = v.strip()
    init_otel(
        endpoint=_otel_endpoint,
        service_name=os.environ.get("AGENTLEDGER_OTEL_SERVICE_NAME", "agentledger"),
        headers=_otel_headers or None,
    )

upstream_url = os.environ.get("AGENTLEDGER_UPSTREAM_URL", "https://api.openai.com")
dsn          = os.environ.get("AGENTLEDGER_DSN", "sqlite:///agentledger.db")
host         = os.environ.get("AGENTLEDGER_HOST", "0.0.0.0")
port         = int(os.environ.get("AGENTLEDGER_PORT", "8000"))

app = create_app(
    upstream_url=upstream_url,
    dsn=dsn,
    budget_session=_float_env("AGENTLEDGER_BUDGET_SESSION"),
    budget_agent=_float_env("AGENTLEDGER_BUDGET_AGENT"),
    budget_daily=_float_env("AGENTLEDGER_BUDGET_DAILY"),
    budget_action=os.environ.get("AGENTLEDGER_BUDGET_ACTION", "block"),
    rate_limit_config=RateLimitConfig(
        global_rpm=  int(os.environ["AGENTLEDGER_RATE_LIMIT_RPM"])          if os.environ.get("AGENTLEDGER_RATE_LIMIT_RPM")          else None,
        session_rpm= int(os.environ["AGENTLEDGER_RATE_LIMIT_SESSION_RPM"])  if os.environ.get("AGENTLEDGER_RATE_LIMIT_SESSION_RPM")  else None,
        agent_rpm=   int(os.environ["AGENTLEDGER_RATE_LIMIT_AGENT_RPM"])    if os.environ.get("AGENTLEDGER_RATE_LIMIT_AGENT_RPM")    else None,
        user_rpm=    int(os.environ["AGENTLEDGER_RATE_LIMIT_USER_RPM"])     if os.environ.get("AGENTLEDGER_RATE_LIMIT_USER_RPM")     else None,
    ),
    alert_config=AlertConfig(
        webhook_url=os.environ.get("AGENTLEDGER_ALERT_WEBHOOK_URL"),
        cost_per_call=_float_env("AGENTLEDGER_ALERT_COST_PER_CALL"),
        latency_ms=_float_env("AGENTLEDGER_ALERT_LATENCY_MS"),
        error_rate=_float_env("AGENTLEDGER_ALERT_ERROR_RATE"),
        daily_spend=_float_env("AGENTLEDGER_ALERT_DAILY_SPEND"),
    ),
    async_capture=os.environ.get("AGENTLEDGER_ASYNC_CAPTURE", "").lower() in ("1", "true", "yes", "on"),
    capture_queue_max=int(os.environ.get("AGENTLEDGER_CAPTURE_QUEUE_MAX", "10000")),
    capture_level=os.environ.get("AGENTLEDGER_CAPTURE_LEVEL", "full"),
    redactor=build_redactor(
        os.environ.get("AGENTLEDGER_REDACT", ""),
        os.environ.get("AGENTLEDGER_REDACT_PATTERNS", ""),
    ),
    retention_days=_float_env("AGENTLEDGER_RETENTION_DAYS"),
    audit_enabled=os.environ.get("AGENTLEDGER_AUDIT_LOG", "1").lower() not in ("0", "false", "no", "off"),
    loop_action=os.environ.get("AGENTLEDGER_LOOP_ACTION", "warn"),
    loop_max_steps=int(os.environ["AGENTLEDGER_LOOP_MAX_STEPS"]) if os.environ.get("AGENTLEDGER_LOOP_MAX_STEPS") else None,
    loop_repeat_threshold=int(os.environ.get("AGENTLEDGER_LOOP_REPEAT_THRESHOLD", "3")),
    loop_run_gap_seconds=float(os.environ.get("AGENTLEDGER_LOOP_RUN_GAP_SECONDS", "900")),
    completion_promise=os.environ.get("AGENTLEDGER_COMPLETION_PROMISE") or None,
)

_logger = logging.getLogger("agentledger")
if not os.environ.get("AGENTLEDGER_INGEST_KEY"):
    _logger.warning(
        "AGENTLEDGER_INGEST_KEY is not set — the proxy will forward requests from "
        "ANYONE who can reach it (open relay). Set it to require x-agentledger-ingest-key "
        "before exposing the proxy beyond localhost."
    )
if not os.environ.get("AGENTLEDGER_API_KEY"):
    _logger.warning(
        "AGENTLEDGER_API_KEY is not set — the dashboard, API, and MCP endpoints are "
        "UNAUTHENTICATED. Set it (or configure API tokens) before exposing Agentic Ledger "
        "beyond localhost."
    )

uvicorn.run(app, host=host, port=port)
