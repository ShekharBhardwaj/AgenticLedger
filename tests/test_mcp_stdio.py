"""End-to-end test of the stdio MCP transport (`agenticledger mcp`)."""

import json
import subprocess
import sys


def test_stdio_transport_answers_initialize_and_tools(tmp_path):
    """The exact conversation a subprocess MCP client (or Glama's harness)
    has with the server: initialize → initialized → tools/list, over
    newline-delimited JSON on stdin/stdout."""
    messages = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "list_sessions", "arguments": {}}}),
    ]) + "\n"

    proc = subprocess.run(
        [sys.executable, "-m", "agenticledger.mcp_stdio"],
        input=messages, capture_output=True, text=True, timeout=30,
        env={"AGENTICLEDGER_DSN": f"sqlite:///{tmp_path}/mcp.db", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, proc.stderr

    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    by_id = {r.get("id"): r for r in lines}

    # stdout carries ONLY JSON-RPC — three responses (the notification is silent)
    assert len(lines) == 3
    assert by_id[1]["result"]["serverInfo"]["name"] == "agenticledger"
    tool_names = {t["name"] for t in by_id[2]["result"]["tools"]}
    assert tool_names == {"list_sessions", "explain", "get_session", "search",
                          "list_runs", "get_run_status"}
    # Empty fresh DB — list_sessions answers cleanly with no data
    assert by_id[3]["result"]["content"][0]["type"] == "text"


def test_stdio_transport_survives_garbage(tmp_path):
    messages = "not json at all\n" + json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    proc = subprocess.run(
        [sys.executable, "-m", "agenticledger.mcp_stdio"],
        input=messages, capture_output=True, text=True, timeout=30,
        env={"AGENTICLEDGER_DSN": f"sqlite:///{tmp_path}/mcp.db", "PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert lines[0]["error"]["code"] == -32700  # parse error reported, not fatal
    assert "tools" in lines[1]["result"]        # stream keeps working


def test_serve_loop_in_process(tmp_path, monkeypatch, capsys):
    """The same conversation, run IN-PROCESS so the loop's edge cases are
    exercised where coverage can see them: blank lines skipped, parse
    errors answered without killing the transport, tool exceptions
    contained, EOF closing the store."""
    import asyncio
    import io

    from agenticledger import mcp_stdio

    lines = [
        "",                                                    # blank — skipped
        "this is not json",                                    # parse error
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "list_sessions", "arguments": {}}}),
        json.dumps(["not", "an", "object"]),                   # wrong shape
    ]
    monkeypatch.setenv("AGENTICLEDGER_DSN", f"sqlite:///{tmp_path}/mcp.db")
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)

    asyncio.run(mcp_stdio.serve())

    responses = [json.loads(ln) for ln in out.getvalue().splitlines() if ln.strip()]
    by_id = {r.get("id"): r for r in responses}
    anon_errors = {r["error"]["code"] for r in responses if r.get("id") is None}
    assert -32700 in anon_errors                # parse error answered
    assert -32601 in anon_errors                # wrong-shape body answered too
    assert by_id[1]["result"]["serverInfo"]["name"]
    assert len(by_id[2]["result"]["tools"]) == 6
    assert by_id[3]["result"]                                  # tool ran on empty db
    # The transport survived every malformed input and exited cleanly on EOF.


def test_main_returns_zero_on_keyboard_interrupt(monkeypatch):
    from agenticledger import mcp_stdio

    def boom():
        raise KeyboardInterrupt

    monkeypatch.setattr(mcp_stdio.asyncio, "run", lambda coro: (coro.close(), boom()))
    assert mcp_stdio.main() == 0
