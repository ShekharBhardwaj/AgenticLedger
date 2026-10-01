"""
OTLP/HTTP ingest — accept OpenTelemetry GenAI spans as ledger calls.

Many runtimes can't route through a base-URL proxy but speak OTel natively
(Gemini CLI, Codex CLI's [otel], AutoGen/AG2, Pydantic AI, Vercel AI SDK,
Mastra). Pointing OTEL_EXPORTER_OTLP_ENDPOINT at the ledger captures their
LLM calls with zero framework-side work.

Scope:
- POST /v1/traces  (OTLP JSON or protobuf encoding): spans that carry
  GenAI semantic-convention attributes become llm_calls rows.
- POST /v1/logs: tool_result events become tool_executions rows; other
  records acknowledged. /v1/metrics: acknowledged (2xx) so exporters
  don't buffer/retry, but not yet mapped.
- http/protobuf needs opentelemetry-proto, shipped by the [otel] extra
  (the Docker image includes it). Without it, protobuf payloads get a
  415 with an install hint (or switch the exporter to
  OTEL_EXPORTER_OTLP_PROTOCOL=http/json).

Idempotency: the action_id is derived deterministically from traceId+spanId,
so re-exported batches never duplicate rows. Token counts arrive as span
attributes; message bodies usually don't — OTLP-ingested calls are
metadata-level records (model, tokens, cost, latency, session, agent).
"""

import base64
import contextlib
import logging
import uuid
from typing import Any, Optional

from .normalize import CanonicalRequest, CanonicalResponse
from .pricing import compute_cost

logger = logging.getLogger(__name__)

_NS = uuid.UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c8")  # uuid NAMESPACE_OID

# gen_ai.system values → the pricing convention we already understand.
_SYSTEM_TO_PROVIDER = {
    "anthropic": "anthropic",
    "openai": "openai",
    "az.ai.openai": "openai",
    "azure.ai.openai": "openai",
}


def decode_protobuf(body: bytes, kind: str) -> Optional[dict]:
    """Decode an OTLP protobuf export request into the OTLP/JSON dict shape
    the extractors consume. Returns None when opentelemetry-proto is not
    installed (the [otel] extra provides it). Raises on malformed payloads.

    use_integers_for_enums matches OTLP/JSON, which encodes enums as numbers
    (a deliberate deviation from proto3 JSON) — the extractors compare
    status.code against the integer.
    """
    try:
        from google.protobuf.json_format import MessageToDict
        if kind == "traces":
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                ExportTraceServiceRequest as _Request,
            )
        else:
            from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
                ExportLogsServiceRequest as _Request,
            )
    except ImportError:
        return None
    msg = _Request()
    msg.ParseFromString(body)
    payload = MessageToDict(msg, use_integers_for_enums=True)
    if kind == "traces":
        _hexlify_span_ids(payload)
    return payload


def _hexlify_span_ids(payload: dict) -> None:
    """OTLP/JSON carries traceId/spanId as hex; the proto3 JSON mapping of
    bytes fields gives base64. Convert in place so a batch exported over
    protobuf produces the same deterministic action_ids as the identical
    batch over JSON."""
    for rs in payload.get("resourceSpans") or []:
        for ss in rs.get("scopeSpans") or []:
            for span in ss.get("spans") or []:
                for key in ("traceId", "spanId", "parentSpanId"):
                    val = span.get(key)
                    if isinstance(val, str) and val:
                        with contextlib.suppress(Exception):
                            span[key] = base64.b64decode(val).hex()


def _attr_map(attributes: Optional[list]) -> dict[str, Any]:
    """Flatten OTLP [{key, value:{stringValue|intValue|...}}] to a dict."""
    out: dict[str, Any] = {}
    for attr in attributes or []:
        if not isinstance(attr, dict):
            continue
        key = attr.get("key")
        value = attr.get("value")
        if not key or not isinstance(value, dict):
            continue
        if "stringValue" in value:
            out[key] = value["stringValue"]
        elif "intValue" in value:
            with contextlib.suppress(TypeError, ValueError):
                out[key] = int(value["intValue"])
        elif "doubleValue" in value:
            out[key] = value["doubleValue"]
        elif "boolValue" in value:
            out[key] = value["boolValue"]
        elif "arrayValue" in value:
            out[key] = [
                v.get("stringValue", v.get("intValue"))
                for v in value["arrayValue"].get("values", [])
                if isinstance(v, dict)
            ]
    return out


def _first(attrs: dict, *keys: str) -> Any:
    for key in keys:
        if attrs.get(key) is not None:
            return attrs[key]
    return None


def _is_llm_span(attrs: dict) -> bool:
    return any(k.startswith("gen_ai.") for k in attrs)


def extract_calls(payload: dict) -> list[dict]:
    """Yield save-ready call dicts from an OTLP/JSON ExportTraceServiceRequest."""
    calls: list[dict] = []
    for rs in payload.get("resourceSpans") or []:
        resource_attrs = _attr_map((rs.get("resource") or {}).get("attributes"))
        service = resource_attrs.get("service.name")
        for ss in rs.get("scopeSpans") or []:
            for span in ss.get("spans") or []:
                attrs = _attr_map(span.get("attributes"))
                if not _is_llm_span(attrs):
                    continue
                call = _span_to_call(span, attrs, service)
                if call is not None:
                    calls.append(call)
    return calls


def _span_to_call(span: dict, attrs: dict, service: Optional[str]) -> Optional[dict]:
    try:
        trace_id = span.get("traceId") or ""
        span_id = span.get("spanId") or ""
        if not span_id:
            return None
        action_id = str(uuid.uuid5(_NS, f"otlp:{trace_id}:{span_id}"))

        start_ns = int(span.get("startTimeUnixNano") or 0)
        end_ns = int(span.get("endTimeUnixNano") or start_ns)
        timestamp = start_ns / 1e9 if start_ns else 0.0
        latency_ms = max((end_ns - start_ns) / 1e6, 0.0)

        model = str(_first(attrs, "gen_ai.request.model", "gen_ai.response.model") or "unknown")
        system = str(attrs.get("gen_ai.system") or "").lower()
        provider = _SYSTEM_TO_PROVIDER.get(system, system or "otlp")

        tokens_in = _as_int(_first(
            attrs, "gen_ai.usage.input_tokens", "gen_ai.usage.prompt_tokens"))
        tokens_out = _as_int(_first(
            attrs, "gen_ai.usage.output_tokens", "gen_ai.usage.completion_tokens"))

        status = span.get("status") or {}
        errored = status.get("code") == 2  # STATUS_CODE_ERROR
        finish = attrs.get("gen_ai.response.finish_reasons")
        if isinstance(finish, list):
            finish = finish[0] if finish else None

        req = CanonicalRequest(
            messages=[], model_id=model, provider=provider, timestamp=timestamp,
            temperature=attrs.get("gen_ai.request.temperature"),
            max_tokens=_as_int(attrs.get("gen_ai.request.max_tokens")),
        )
        resp = CanonicalResponse(
            content=None, tool_calls=None,
            stop_reason=str(finish) if finish is not None else None,
            tokens_in=tokens_in, tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=compute_cost(
                model, tokens_in, tokens_out,
                provider=provider if provider in ("openai", "anthropic") else "",
            ),
        )
        meta = {
            "session_id": str(
                _first(attrs, "gen_ai.conversation.id", "session.id")
                or f"otlp-{trace_id[:12] or 'unknown'}"
            ),
            "agent_name": _opt_str(_first(attrs, "gen_ai.agent.name", "agent.name")),
            "user_id": _opt_str(attrs.get("user.id")),
            "framework": _opt_str(service),
            "app_id": _opt_str(service),
            "environment": str(attrs.get("deployment.environment", "development")),
            "status_code": 500 if errored else 200,
            "error_detail": _opt_str(status.get("message")) if errored else None,
        }
        return {"action_id": action_id, "req": req, "resp": resp, "meta": meta}
    except Exception:
        logger.warning("Failed to map OTLP span — skipped", exc_info=True)
        return None


def extract_api_requests(payload: dict) -> list[dict]:
    """Claude Code's own record of each model call, when the proxy cannot
    be in the path (a managed ANTHROPIC_BEDROCK_BASE_URL, a gateway the
    agent is pinned to). `claude_code.api_request` and `claude_code.api_error`
    log events carry model, tokens, cache tokens, cost and duration, never
    the prompt, so the call lands at metadata level: everything Loop Lens
    and the reports need, nothing the ledger can refuse. Opt-in through
    AGENTICLEDGER_TELEMETRY_CALLS: someone running the proxy as well would
    otherwise record every call twice."""
    calls: list[dict] = []
    for rl in payload.get("resourceLogs") or []:
        resource = _attr_map((rl.get("resource") or {}).get("attributes"))
        for sl in rl.get("scopeLogs") or []:
            for rec in sl.get("logRecords") or []:
                attrs = _attr_map(rec.get("attributes"))
                name = str(attrs.get("event.name")
                           or (rec.get("body") or {}).get("stringValue", ""))
                if name not in ("claude_code.api_request", "claude_code.api_error"):
                    continue
                call = _api_request_to_call(name, rec, attrs, resource)
                if call is not None:
                    calls.append(call)
    return calls


def _api_request_to_call(name: str, rec: dict, attrs: dict, resource: dict) -> Optional[dict]:
    try:
        ts_ns = int(rec.get("timeUnixNano") or rec.get("observedTimeUnixNano") or 0)
        timestamp = ts_ns / 1e9 if ts_ns else 0.0
        session_id = str(_first(attrs, "session.id") or resource.get("session.id")
                         or f"cc-{ts_ns // 10**9}")
        model = str(attrs.get("model") or "unknown")
        duration = attrs.get("duration_ms")
        # One id per event, stable across OTLP's at-least-once redelivery.
        action_id = str(uuid.uuid5(_NS, f"cc-event:{session_id}:{ts_ns}:{model}:{name}"))
        errored = name == "claude_code.api_error"
        tokens_in = _as_int(attrs.get("input_tokens"))
        tokens_out = _as_int(attrs.get("output_tokens"))
        cache_read = _as_int(attrs.get("cache_read_tokens"))
        cache_write = _as_int(attrs.get("cache_creation_tokens"))
        # Claude Code reports the price it was charged, cache discounts
        # included; the packs stand in when it does not.
        reported = attrs.get("cost_usd")
        cost = (float(reported) if isinstance(reported, (int, float)) and not errored
                else (compute_cost(model, tokens_in, tokens_out, provider="")
                      if not errored else 0.0))
        req = CanonicalRequest(messages=[], model_id=model, provider=_provider_for(model),
                               timestamp=timestamp)
        resp = CanonicalResponse(
            content=None, tool_calls=None, stop_reason=None,
            tokens_in=tokens_in, tokens_out=tokens_out,
            latency_ms=float(duration) if duration is not None else 0.0,
            cost_usd=cost, cache_read_tokens=cache_read, cache_write_tokens=cache_write,
        )
        status_code = _as_int(attrs.get("status_code")) if errored else 200
        meta = {
            "session_id": session_id,
            "agent_name": "claude-code",
            "user_id": _opt_str(_first(attrs, "user.email", "user.id")
                                or _first(resource, "user.email", "user.id")),
            "framework": "claude-code",
            "app_id": "claude-code",
            "environment": str(attrs.get("deployment.environment", "development")),
            "status_code": status_code or (500 if errored else 200),
            "error_detail": (_opt_str(attrs.get("error")) or "api_error") if errored else None,
        }
        return {"action_id": action_id, "req": req, "resp": resp, "meta": meta}
    except Exception:
        logger.warning("Failed to map a Claude Code api_request event; skipped", exc_info=True)
        return None


def _provider_for(model: str) -> str:
    low = model.lower()
    if "anthropic." in low and "claude" in low:
        return "bedrock"
    return "anthropic" if "claude" in low else "otlp"


def _as_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _opt_str(value) -> Optional[str]:
    return str(value) if value is not None and value != "" else None


def _as_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no")
    return None


def extract_tool_events(payload: dict) -> list[dict]:
    """Map OTLP log records for tool executions into tool_executions rows.

    Claude Code (OTEL_LOGS_EXPORTER=otlp) emits `claude_code.tool_result`
    events with the tool name, duration, and success — the on-machine audit
    trail the proxy can't see. api_request events are deliberately NOT mapped:
    users running both the proxy and OTel would double-count their calls, and
    the proxy's capture is strictly richer.
    """
    events: list[dict] = []
    for rl in payload.get("resourceLogs") or []:
        for sl in rl.get("scopeLogs") or []:
            for rec in sl.get("logRecords") or []:
                attrs = _attr_map(rec.get("attributes"))
                name = str(
                    attrs.get("event.name")
                    or rec.get("eventName")
                    or (rec.get("body") or {}).get("stringValue", "")
                )
                if "tool_result" not in name:
                    continue
                try:
                    ts = int(rec.get("timeUnixNano") or 0) / 1e9 or None
                except (TypeError, ValueError):
                    ts = None
                success = _as_bool(attrs.get("success"))
                events.append({
                    "tool_call_id": _opt_str(
                        attrs.get("tool_use_id") or attrs.get("gen_ai.tool.call.id")),
                    "tool_name": _opt_str(
                        attrs.get("tool_name") or attrs.get("name")),
                    "arguments": None,
                    "issued_by_action_id": None,
                    "resolved_by_action_id": None,
                    "session_id": _opt_str(
                        attrs.get("session.id") or attrs.get("session_id")),
                    "thread_id": None,
                    "latency_ms": _as_int(
                        attrs.get("duration_ms") or attrs.get("duration")),
                    "is_error": (not success) if success is not None else None,
                    "timestamp": ts,
                })
    return events
