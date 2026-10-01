"""Tests for OTLP/HTTP ingest — GenAI spans becoming ledger calls."""

import uuid

import pytest

from agenticledger.proxy.otlp_ingest import _NS

TRACE = "0af7651916cd43dd8448eb211c80319c"
SPAN = "b7ad6b7169203331"


def _genai_span(*, errored: bool = False, extra_attrs: list | None = None) -> dict:
    attrs = [
        {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
        {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
        {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1200"}},
        {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "300"}},
        {"key": "gen_ai.conversation.id", "value": {"stringValue": "otlp-conv-1"}},
        {"key": "gen_ai.agent.name", "value": {"stringValue": "researcher"}},
        {"key": "gen_ai.response.finish_reasons",
         "value": {"arrayValue": {"values": [{"stringValue": "stop"}]}}},
    ] + (extra_attrs or [])
    span = {
        "traceId": TRACE,
        "spanId": SPAN,
        "name": "chat gpt-4o",
        "startTimeUnixNano": "1753500000000000000",
        "endTimeUnixNano": "1753500002500000000",
        "attributes": attrs,
    }
    if errored:
        span["status"] = {"code": 2, "message": "rate limited"}
    return {
        "resourceSpans": [{
            "resource": {"attributes": [
                {"key": "service.name", "value": {"stringValue": "gemini-cli"}},
            ]},
            "scopeSpans": [{"spans": [span]}],
        }]
    }


def _post(client, payload):
    return client.post("/v1/traces", json=payload,
                       headers={"content-type": "application/json"})


def test_genai_span_becomes_a_call(proxy):
    client = proxy()
    resp = _post(client, _genai_span())
    assert resp.status_code == 200

    rows = client.get("/session/otlp-conv-1").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["model_id"] == "gpt-4o"
    assert row["tokens_in"] == 1200
    assert row["tokens_out"] == 300
    assert row["agent_name"] == "researcher"
    assert row["framework"] == "gemini-cli"
    assert row["latency_ms"] == 2500
    assert row["cost_usd"] == (1200 * 2.50 + 300 * 10.00) / 1_000_000
    assert row["stop_reason"] == "stop"
    # Deterministic id from trace+span ids
    assert row["action_id"] == str(uuid.uuid5(_NS, f"otlp:{TRACE}:{SPAN}"))


def test_reexported_batch_is_idempotent(proxy):
    client = proxy()
    _post(client, _genai_span())
    _post(client, _genai_span())
    assert len(client.get("/session/otlp-conv-1").json()) == 1


def test_error_span_recorded_with_status(proxy):
    client = proxy()
    _post(client, _genai_span(errored=True))
    row = client.get("/session/otlp-conv-1").json()[0]
    assert row["status_code"] == 500
    assert row["error_detail"] == "rate limited"


def test_non_genai_spans_skipped(proxy):
    client = proxy()
    payload = {
        "resourceSpans": [{
            "scopeSpans": [{"spans": [{
                "traceId": TRACE, "spanId": "aaaaaaaaaaaaaaaa",
                "name": "http GET /health",
                "attributes": [{"key": "http.method", "value": {"stringValue": "GET"}}],
            }]}],
        }]
    }
    assert _post(client, payload).status_code == 200
    assert client.get("/session/otlp-unknown").status_code == 404


def _genai_span_pb() -> bytes:
    """The same GenAI span as _genai_span(), but as a serialized protobuf
    ExportTraceServiceRequest (session id differs so tests don't collide)."""
    pytest.importorskip("opentelemetry.proto")
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, ArrayValue

    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    rs.resource.attributes.add(key="service.name", value=AnyValue(string_value="gemini-cli"))
    span = rs.scope_spans.add().spans.add()
    span.trace_id = bytes.fromhex(TRACE)
    span.span_id = bytes.fromhex(SPAN)
    span.name = "chat gpt-4o"
    span.start_time_unix_nano = 1753500000000000000
    span.end_time_unix_nano = 1753500002500000000
    span.attributes.add(key="gen_ai.system", value=AnyValue(string_value="openai"))
    span.attributes.add(key="gen_ai.request.model", value=AnyValue(string_value="gpt-4o"))
    span.attributes.add(key="gen_ai.usage.input_tokens", value=AnyValue(int_value=1200))
    span.attributes.add(key="gen_ai.usage.output_tokens", value=AnyValue(int_value=300))
    span.attributes.add(key="gen_ai.conversation.id", value=AnyValue(string_value="otlp-conv-pb"))
    span.attributes.add(key="gen_ai.agent.name", value=AnyValue(string_value="researcher"))
    span.attributes.add(
        key="gen_ai.response.finish_reasons",
        value=AnyValue(array_value=ArrayValue(values=[AnyValue(string_value="stop")])),
    )
    return req.SerializeToString()


def test_protobuf_span_ingested_with_same_action_id(proxy):
    """http/protobuf batches land identically to their http/json twins —
    including the deterministic action_id, which requires normalizing the
    proto3-JSON base64 trace/span ids back to OTLP/JSON hex."""
    client = proxy()
    resp = client.post("/v1/traces", content=_genai_span_pb(),
                       headers={"content-type": "application/x-protobuf"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-protobuf"

    rows = client.get("/session/otlp-conv-pb").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["model_id"] == "gpt-4o"
    assert row["tokens_in"] == 1200
    assert row["tokens_out"] == 300
    assert row["framework"] == "gemini-cli"
    assert row["action_id"] == str(uuid.uuid5(_NS, f"otlp:{TRACE}:{SPAN}"))


def test_protobuf_malformed_payload_rejected(proxy):
    pytest.importorskip("opentelemetry.proto")
    client = proxy()
    resp = client.post("/v1/traces", content=b"\xff\xff\xff",
                       headers={"content-type": "application/x-protobuf"})
    assert resp.status_code == 400


def test_unsupported_content_type_rejected(proxy):
    client = proxy()
    resp = client.post("/v1/traces", content=b"whatever",
                       headers={"content-type": "text/plain"})
    assert resp.status_code == 415
    assert "protobuf" in resp.json()["error"]


def test_protobuf_tool_result_logs_become_tool_executions(proxy):
    pytest.importorskip("opentelemetry.proto")
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
        ExportLogsServiceRequest,
    )
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue

    req = ExportLogsServiceRequest()
    rec = req.resource_logs.add().scope_logs.add().log_records.add()
    rec.time_unix_nano = 1753500001000000000
    rec.attributes.add(key="event.name", value=AnyValue(string_value="claude_code.tool_result"))
    rec.attributes.add(key="tool_name", value=AnyValue(string_value="Bash"))
    rec.attributes.add(key="duration_ms", value=AnyValue(int_value=742))
    rec.attributes.add(key="success", value=AnyValue(string_value="false"))
    rec.attributes.add(key="session.id", value=AnyValue(string_value="cc-otel-pb"))

    client = proxy()
    resp = client.post("/v1/logs", content=req.SerializeToString(),
                       headers={"content-type": "application/x-protobuf"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-protobuf"

    tools = client.get("/api/sessions/cc-otel-pb/tools").json()
    assert len(tools) == 1
    assert tools[0]["tool_name"] == "Bash"
    assert tools[0]["latency_ms"] == 742
    assert tools[0]["is_error"] == 1


def test_protobuf_metrics_acked_in_kind(proxy):
    client = proxy()
    resp = client.post("/v1/metrics", content=b"",
                       headers={"content-type": "application/x-protobuf"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-protobuf"


def test_logs_and_metrics_acked(proxy):
    client = proxy()
    for path in ("/v1/logs", "/v1/metrics"):
        resp = client.post(path, json={"resourceLogs": []},
                           headers={"content-type": "application/json"})
        assert resp.status_code == 200
        assert resp.json() == {"partialSuccess": {}}


def test_ingest_key_gates_otlp(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_INGEST_KEY", "sekrit")
    client = proxy()
    assert _post(client, _genai_span()).status_code == 401
    ok = client.post("/v1/traces", json=_genai_span(),
                     headers={"content-type": "application/json",
                              "x-agenticledger-ingest-key": "sekrit"})
    assert ok.status_code == 200


def test_tool_result_log_events_become_tool_executions(proxy):
    """claude_code.tool_result log records land in the tool_executions table —
    the on-machine audit trail the proxy can't see."""
    payload = {
        "resourceLogs": [{
            "scopeLogs": [{
                "logRecords": [
                    {
                        "timeUnixNano": "1753500001000000000",
                        "attributes": [
                            {"key": "event.name", "value": {"stringValue": "claude_code.tool_result"}},
                            {"key": "tool_name", "value": {"stringValue": "Bash"}},
                            {"key": "duration_ms", "value": {"intValue": "742"}},
                            {"key": "success", "value": {"stringValue": "false"}},
                            {"key": "session.id", "value": {"stringValue": "cc-otel-sess"}},
                        ],
                    },
                    {  # a non-tool event — ignored
                        "timeUnixNano": "1753500002000000000",
                        "attributes": [
                            {"key": "event.name", "value": {"stringValue": "claude_code.user_prompt"}},
                        ],
                    },
                ],
            }],
        }]
    }
    client = proxy()
    resp = client.post("/v1/logs", json=payload,
                       headers={"content-type": "application/json"})
    assert resp.status_code == 200

    tools = client.get("/api/sessions/cc-otel-sess/tools").json()
    assert len(tools) == 1
    assert tools[0]["tool_name"] == "Bash"
    assert tools[0]["latency_ms"] == 742
    assert tools[0]["is_error"] == 1


def _cc_event(name, ts, **attrs):
    def val(v):
        if isinstance(v, bool):
            return {"boolValue": v}
        if isinstance(v, int):
            return {"intValue": str(v)}
        if isinstance(v, float):
            return {"doubleValue": v}
        return {"stringValue": str(v)}
    return {"timeUnixNano": str(ts), "attributes": [
        {"key": "event.name", "value": {"stringValue": name}},
        *({"key": k, "value": val(v)} for k, v in attrs.items()),
    ]}


def _cc_payload(*records):
    return {"resourceLogs": [{"scopeLogs": [{"logRecords": list(records)}]}]}


def test_claude_code_api_request_telemetry_becomes_calls_when_asked(proxy):
    """Telemetry-only mode: on a laptop where the proxy cannot sit in the
    path (a managed base URL), Claude Code's own api_request events are
    the record. Metadata level, Claude Code's own cost, Bedrock ids
    recognised, redelivery deduplicated."""
    payload = _cc_payload(
        _cc_event("claude_code.api_request", 1753500001000000000,
                  model="us.anthropic.claude-sonnet-4-5-20250929-v1:0", cost_usd=0.0123,
                  duration_ms=1840, input_tokens=1200, output_tokens=85,
                  cache_read_tokens=900, cache_creation_tokens=0, **{"session.id": "cc-managed-1"}),
        _cc_event("claude_code.api_error", 1753500005000000000,
                  model="us.anthropic.claude-sonnet-4-5-20250929-v1:0", error="rate limited",
                  status_code=429, duration_ms=300, **{"session.id": "cc-managed-1"}),
        _cc_event("claude_code.user_prompt", 1753500006000000000, prompt_length=42,
                  **{"session.id": "cc-managed-1"}),
    )
    client = proxy(telemetry_calls=True)
    assert client.post("/v1/logs", json=payload).status_code == 200
    assert client.post("/v1/logs", json=payload).status_code == 200   # redelivered batch
    rows = client.get("/session/cc-managed-1").json()
    assert len(rows) == 2, [r["error_detail"] for r in rows]
    ok = [r for r in rows if r["status_code"] == 200][0]
    assert ok["provider"] == "bedrock" and ok["model_id"].startswith("us.anthropic.")
    assert ok["tokens_in"] == 1200 and ok["tokens_out"] == 85
    assert ok["cache_read_tokens"] == 900 and abs(ok["cost_usd"] - 0.0123) < 1e-9
    assert ok["latency_ms"] == 1840 and ok["agent_name"] == "claude-code"
    assert ok["messages"] in ([], None) and ok.get("content") in (None, "")
    err = [r for r in rows if r["status_code"] != 200][0]
    assert err["status_code"] == 429 and "rate limited" in err["error_detail"]
    sessions = {s["session_id"]: s for s in client.get("/api/sessions").json()}
    assert sessions["cc-managed-1"]["call_count"] == 2
    rows_text = client.get("/api/settings").json()["rows"]
    assert any(r["label"] == "telemetry calls" and r["value"] == "on" for r in rows_text)


def test_claude_code_api_request_telemetry_is_ignored_by_default(proxy):
    payload = _cc_payload(_cc_event("claude_code.api_request", 1753500001000000000,
                                    model="claude-sonnet-4-5", cost_usd=0.01, duration_ms=10,
                                    input_tokens=10, output_tokens=5,
                                    **{"session.id": "cc-quiet"}))
    client = proxy()
    assert client.post("/v1/logs", json=payload).status_code == 200
    assert client.get("/session/cc-quiet").status_code == 404
