"""The anatomy endpoint: a run's calls in order, slim, with what the model
did next (tool names) and how much context it read, so the dashboard can
replay the loop's shape without message bodies."""

import httpx2 as httpx

from .conftest import anthropic_response


def _tool(name, call_id="toolu_1"):
    return {"id": call_id, "name": name, "input": {"path": "progress.md"}}


def test_run_calls_describe_the_loop_shape(proxy):
    scripted = iter([
        anthropic_response("", tool_uses=[_tool("read_file")], input_tokens=900,
                           output_tokens=40, stop_reason="tool_use"),
        anthropic_response("All tests pass. <promise>DONE</promise>", input_tokens=2100,
                           output_tokens=60),
    ])
    client = proxy(handler=lambda r: httpx.Response(200, json=next(scripted)))
    base = {"x-agenticledger-run-id": "shape", "x-agenticledger-session-id": "s1"}
    body = {"model": "claude-sonnet-5", "max_tokens": 64,
            "messages": [{"role": "user", "content": "fix the tests"}]}
    assert client.post("/v1/messages", json=body,
                       headers={**base, "x-agenticledger-iteration": "1"}).status_code == 200
    assert client.post("/v1/messages", json=body,
                       headers={**base, "x-agenticledger-iteration": "2"}).status_code == 200

    rows = client.get("/api/runs/shape/calls").json()
    assert [r["iteration"] for r in rows] == [1, 2]
    assert rows[0]["tools"] == ["read_file"] and rows[0]["has_text"] is False
    assert rows[0]["stop_reason"] == "tool_use" and rows[0]["tokens_in"] == 900
    assert rows[1]["tools"] == [] and rows[1]["has_text"] is True
    assert rows[1]["tokens_in"] == 2100 and rows[1]["session_id"] == "s1"
    assert rows[0]["wall"] is False
    assert rows[0]["paths"] == ["progress.md"] and rows[1]["paths"] == []
    # Slim: no message bodies ride along.
    assert "messages" not in rows[0] and "content" not in rows[0]
    assert client.get("/api/runs/nope/calls").status_code == 404
