"""
OpenTelemetry export for Agentic Ledger.

Each intercepted LLM call becomes an OTel span sent to any OTLP-compatible
collector: Grafana Tempo, Jaeger, Honeycomb, Datadog, Dynatrace, etc.

Install the optional dependency group:
    pip install "agentic-ledger[otel]"

Configure via environment variables (see __main__.py):
    AGENTLEDGER_OTEL_ENDPOINT      OTLP/HTTP base URL, e.g. http://localhost:4318
    AGENTLEDGER_OTEL_SERVICE_NAME  Reported service.name (default: agentledger)
    AGENTLEDGER_OTEL_HEADERS       Comma-separated key=value pairs for auth headers

Trace structure:
    All calls that share a session_id are grouped into a single trace.
    Parent-child relationships follow x-agentledger-parent-action-id.
    Follows GenAI semantic conventions (gen_ai.*) where applicable.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy-initialized tracer — None until init_otel() is called successfully.
_tracer = None

# Maps session_id → OTel trace_id (128-bit int) so all calls in a session
# share one trace.
_session_traces: dict[str, int] = {}

# Maps action_id → SpanContext so child calls can reference their parent span.
_span_contexts: dict[str, object] = {}


def init_otel(
    endpoint: str,
    service_name: str = "agentledger",
    headers: Optional[dict[str, str]] = None,
) -> None:
    """Initialize the OTLP exporter.  Safe to call multiple times (idempotent)."""
    global _tracer
    if _tracer is not None:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.error(
            "OpenTelemetry packages missing. "
            "Install with: pip install 'agentic-ledger[otel]'"
        )
        return

    # Surface OTLP export errors in logs
    import logging as _logging
    _logging.getLogger("opentelemetry.exporter.otlp").setLevel(_logging.WARNING)
    _logging.getLogger("opentelemetry.sdk.trace.export").setLevel(_logging.WARNING)

    resource = Resource({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=f"{endpoint.rstrip('/')}/v1/traces",
        headers=headers or {},
    )
    # BatchSpanProcessor exports in a background thread — does not block the async event loop
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("agentledger", schema_url="https://opentelemetry.io/schemas/1.24.0")
    logger.info("Agentic Ledger OTel export enabled → %s", endpoint)


def emit_span(
    action_id: str,
    req,   # CanonicalRequest
    resp,  # CanonicalResponse
    *,
    session_id: Optional[str] = None,
    parent_action_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    user_id: Optional[str] = None,
    environment: str = "development",
    handoff_from: Optional[str] = None,
    handoff_to: Optional[str] = None,
    status_code: int = 200,
    **_: object,  # absorb extra meta fields (app_id, etc.) without error
) -> None:
    """Emit one OTel span for an intercepted LLM call.  Never raises."""
    if _tracer is None:
        return

    try:

        from opentelemetry import context as otel_context
        from opentelemetry import trace
        from opentelemetry.trace import NonRecordingSpan, SpanContext, StatusCode, TraceFlags

        # ── Determine trace_id (one trace per session) ────────────────────────
        if session_id:
            if session_id not in _session_traces:
                _session_traces[session_id] = _uuid_to_trace_id(session_id)
            trace_id = _session_traces[session_id]
        else:
            trace_id = _uuid_to_trace_id(action_id)

        # ── Establish parent context ──────────────────────────────────────────
        parent_ctx: object | None = None
        if parent_action_id and parent_action_id in _span_contexts:
            parent_ctx = otel_context.Context()
            parent_ctx = trace.set_span_in_context(
                NonRecordingSpan(_span_contexts[parent_action_id]), parent_ctx
            )
        else:
            # No real parent, but we still need to anchor to our trace_id.
            # Use a synthetic remote span as the trace root — it won't be exported.
            root_sc = SpanContext(
                trace_id=trace_id,
                span_id=_uuid_to_span_id(action_id + "_root"),
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
            )
            parent_ctx = trace.set_span_in_context(NonRecordingSpan(root_sc))

        # ── Build span attributes (GenAI semantic conventions) ────────────────
        attrs: dict[str, object] = {
            "gen_ai.system":          req.provider,
            "gen_ai.operation.name":  "chat",
            "gen_ai.request.model":   req.model_id,
            "agentledger.action_id":  action_id,
            "agentledger.environment": environment,
            "agentledger.latency_ms":  resp.latency_ms,
            "http.status_code":        status_code,
        }
        if resp.tokens_in is not None:
            attrs["gen_ai.usage.input_tokens"] = resp.tokens_in
        if resp.tokens_out is not None:
            attrs["gen_ai.usage.output_tokens"] = resp.tokens_out
        if resp.stop_reason:
            attrs["gen_ai.response.finish_reasons"] = [resp.stop_reason]
        if resp.cost_usd is not None:
            attrs["agentledger.cost_usd"] = resp.cost_usd
        if session_id:
            attrs["agentledger.session_id"] = session_id
        if agent_name:
            attrs["agentledger.agent_name"] = agent_name
        if user_id:
            attrs["agentledger.user_id"] = user_id
        if handoff_from:
            attrs["agentledger.handoff_from"] = handoff_from
        if handoff_to:
            attrs["agentledger.handoff_to"] = handoff_to
        if req.temperature is not None:
            attrs["gen_ai.request.temperature"] = req.temperature
        if req.max_tokens is not None:
            attrs["gen_ai.request.max_tokens"] = req.max_tokens

        # ── Create and close span (synchronous SDK — no async needed) ─────────
        span_name = f"llm.chat {req.provider}/{req.model_id}"
        start_ns = int(req.timestamp * 1e9)
        end_ns = start_ns + int(resp.latency_ms * 1e6)

        span = _tracer.start_span(
            span_name,
            context=parent_ctx,
            start_time=start_ns,
            attributes=attrs,
        )

        # Store this span's context so children can reference it.
        _span_contexts[action_id] = span.get_span_context()

        if status_code != 200:
            span.set_status(StatusCode.ERROR, f"HTTP {status_code}")

        span.end(end_time=end_ns)

    except Exception as exc:
        logger.warning("OTel emit failed: %s", exc)


def _uuid_to_trace_id(uid: str) -> int:
    """Convert any string to a 128-bit int for use as an OTel trace ID."""
    import hashlib
    digest = hashlib.sha256(uid.encode()).hexdigest()
    return int(digest[:32], 16) or 1  # OTel requires non-zero


def _uuid_to_span_id(uid: str) -> int:
    """Convert a UUID-like string to a 64-bit int for use as an OTel span ID."""
    import hashlib
    digest = hashlib.sha256(uid.encode()).hexdigest()
    return int(digest[:16], 16) or 1  # OTel requires non-zero
