"""Tests for OTLP/HTTP ingest — GenAI spans becoming ledger calls."""

import uuid

from agentledger.proxy.otlp_ingest import _NS

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


def test_protobuf_payload_rejected_with_hint(proxy):
    client = proxy()
    resp = client.post("/v1/traces", content=b"\x0a\x00",
                       headers={"content-type": "application/x-protobuf"})
    assert resp.status_code == 415
    assert "http/json" in resp.json()["error"]


def test_logs_and_metrics_acked(proxy):
    client = proxy()
    for path in ("/v1/logs", "/v1/metrics"):
        resp = client.post(path, json={"resourceLogs": []},
                           headers={"content-type": "application/json"})
        assert resp.status_code == 200
        assert resp.json() == {"partialSuccess": {}}


def test_ingest_key_gates_otlp(proxy, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_INGEST_KEY", "sekrit")
    client = proxy()
    assert _post(client, _genai_span()).status_code == 401
    ok = client.post("/v1/traces", json=_genai_span(),
                     headers={"content-type": "application/json",
                              "x-agentledger-ingest-key": "sekrit"})
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
