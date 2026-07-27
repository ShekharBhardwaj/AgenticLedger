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
