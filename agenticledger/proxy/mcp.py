"""
MCP (Model Context Protocol) server — exposes Agentic Ledger traces as tools.

Implements the JSON-RPC 2.0 over HTTP transport (MCP spec 2024-11-05).

Mounted at POST /mcp in the proxy app. Any MCP-compatible client
(Claude Desktop, Cursor, custom agent) can point at this endpoint to call:

    list_sessions([limit])           → recent sessions with cost/token summaries
    explain(action_id)               → full trace for a single LLM call
    get_session(session_id)          → ordered decision chain for an agent run
    search(query[, limit])           → full-text search across all captured calls

Configure in claude_desktop_config.json:
    {
      "mcpServers": {
        "agenticledger": {
          "url": "http://localhost:8000/mcp"
        }
      }
    }

If AGENTICLEDGER_API_KEY is set, pass it as a request header:
    {
      "mcpServers": {
        "agenticledger": {
          "url": "http://localhost:8000/mcp",
          "headers": { "x-agenticledger-api-key": "your-key" }
        }
      }
    }
"""

import json
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("agentic-ledger")
except Exception:
    _VERSION = "0.0.0"

_TOOLS = [
    {
        "name": "list_sessions",
        "description": (
            "List recent agent sessions with aggregated stats — call count, "
            "total cost, token usage, and start time. Use this to find a "
            "session_id before calling get_session or to get a cost overview."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of sessions to return (default 20, max 100).",
                    "default": 20,
                }
            },
            "required": [],
        },
    },
    {
        "name": "explain",
        "description": (
            "Retrieve the full captured trace for a single LLM call. "
            "Returns the prompt, system prompt, tool calls, model response, "
            "token usage, cost, and latency."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_id": {
                    "type": "string",
                    "description": "The action ID from the x-agenticledger-action-id response header.",
                }
            },
            "required": ["action_id"],
        },
    },
    {
        "name": "get_session",
        "description": (
            "Retrieve the LLM calls of an agent session in chronological "
            "order. By default each call is a compact summary (index, model, "
            "status, error reason, tokens, cost, latency, tool names, sizes) "
            "— sessions can be megabytes, so full prompt/response bodies are "
            "returned only with include_messages=true, and single calls are "
            "better fetched via the explain tool using the action_id from a "
            "summary row."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID passed via x-agenticledger-session-id.",
                },
                "include_messages": {
                    "type": "boolean",
                    "description": "Return full message bodies for every call. "
                                   "Default false; can be very large.",
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "search",
        "description": (
            "Full-text search across all captured LLM calls. Searches prompts, "
            "outputs, system prompts, agent names, and user IDs. "
            "Use this to find calls related to a topic, error, or agent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term to look for across all captured calls.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 20, max 100).",
                    "default": 20,
                },
                "include_messages": {
                    "type": "boolean",
                    "description": "Return full message bodies for each hit. "
                                   "Default false — hits are compact summaries "
                                   "with an action_id to drill in via explain.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_runs",
        "description": (
            "List loop runs (explicit x-agenticledger-run-id or auto-inferred "
            "fresh-context loops, e.g. Ralph overnight runs) with iterations, "
            "sessions, cost, flagged-call counts, and status "
            "(running / flagged / complete)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of runs to return (default 20, max 100).",
                    "default": 20,
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_run_status",
        "description": (
            "Status of one loop run: iterations so far, total cost and tokens, "
            "flagged calls, and whether the completion promise was seen "
            "(status=complete). Loop runners and agents can use this to decide "
            "whether to continue iterating."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "The run ID (from x-agenticledger-run-id or /api/runs).",
                }
            },
            "required": ["run_id"],
        },
    },
]


def _run_status(run: dict, explicitly_ended: bool = False,
                stopped: bool = False) -> dict:
    """Mirror the /api/runs status derivation for MCP consumers (using the
    default run-gap window: MCP has no per-proxy config)."""
    import contextlib
    import datetime as _dt

    promise_seen = bool(run.pop("promise_seen", 0))
    if stopped:
        run["status"] = "stopped"
    elif promise_seen:
        run["status"] = "complete"
    elif run.get("flagged_calls"):
        run["status"] = "flagged"
    elif explicitly_ended:
        run["status"] = "ended"
    else:
        run["status"] = "running"
        with contextlib.suppress(Exception):
            last = _dt.datetime.fromisoformat(str(run.get("last_call_at")))
            if (_dt.datetime.now(_dt.timezone.utc) - last).total_seconds() > 900:
                run["status"] = "ended"
    return run


async def handle_mcp(request: Request) -> JSONResponse:
    """HTTP transport: POST /mcp on the proxy."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_err(None, -32700, "Parse error"), status_code=400)
    response = await dispatch_message(body, request.app.state.store)
    return JSONResponse(response if response is not None else {})


def _call_summary(index: int, r: dict) -> dict:
    """One call as a model-friendly row: everything about the call except
    the conversation itself, plus the sizes of what was withheld — so the
    reader knows what a follow-up explain(action_id) would return."""
    tools = r.get("tool_calls") or []
    tool_names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
    def _size(v) -> int:
        try:
            return len(v) if isinstance(v, str) else len(json.dumps(v)) if v else 0
        except (TypeError, ValueError):
            return 0
    return {
        "index": index,
        "action_id": r.get("action_id"),
        "timestamp": r.get("timestamp"),
        "model_id": r.get("model_id"),
        "status_code": r.get("status_code"),
        "error_detail": r.get("error_detail"),
        "tokens_in": r.get("tokens_in"),
        "tokens_out": r.get("tokens_out"),
        "cache_read_tokens": r.get("cache_read_tokens"),
        "cache_write_tokens": r.get("cache_write_tokens"),
        "cost_usd": r.get("cost_usd"),
        "latency_ms": r.get("latency_ms"),
        "agent_name": r.get("agent_name"),
        "framework": r.get("framework"),
        "run_id": r.get("run_id"),
        "iteration": r.get("iteration"),
        "loop_flags": r.get("loop_flags"),
        "tool_names": tool_names,
        "content_preview": (r.get("content") or "")[:200] or None,
        "withheld_bytes": {"messages": _size(r.get("messages")),
                           "content": _size(r.get("content")),
                           "system_prompt": _size(r.get("system_prompt"))},
    }


async def dispatch_message(body: dict, store) -> Any:
    """Transport-neutral JSON-RPC dispatch — shared by the HTTP endpoint and
    the stdio server (`agenticledger mcp`). Returns a response dict, or None
    for notifications."""
    method = body.get("method")
    id_ = body.get("id")
    params = body.get("params") or {}

    if method == "initialize":
        return _ok(id_, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "agenticledger", "version": _VERSION},
        })

    if method == "notifications/initialized":
        return None  # notification — no response

    if method == "tools/list":
        return _ok(id_, {"tools": _TOOLS})

    if method == "tools/call":
        return await _call_tool(id_, params, store)

    return _err(id_, -32601, f"Method not found: {method!r}")


async def _call_tool(id_: Any, params: dict, store) -> dict:
    name = params.get("name")
    args = params.get("arguments") or {}

    if name == "list_sessions":
        limit = max(1, min(int(args.get("limit", 20)), 100))
        sessions = await store.list_sessions(limit=limit)
        return (_ok(id_, _text_content(json.dumps(sessions, indent=2, default=str))))

    if name == "explain":
        action_id = args.get("action_id", "").strip()
        if not action_id:
            return (_err(id_, -32602, "action_id is required"))
        record = await store.get(action_id)
        if record is None:
            return (_err(id_, -32602, f"No record found for action_id {action_id!r}"))
        return (_ok(id_, _text_content(json.dumps(record, indent=2, default=str))))

    if name == "get_session":
        session_id = args.get("session_id", "").strip()
        if not session_id:
            return (_err(id_, -32602, "session_id is required"))
        records = await store.get_session(session_id)
        if not records:
            return (_err(id_, -32602, f"No records found for session_id {session_id!r}"))
        if not args.get("include_messages"):
            records = [_call_summary(i + 1, r) for i, r in enumerate(records)]
        return (_ok(id_, _text_content(json.dumps(records, indent=2, default=str))))

    if name == "search":
        query = args.get("query", "").strip()
        if not query:
            return (_err(id_, -32602, "query is required"))
        limit = max(1, min(int(args.get("limit", 20)), 100))
        results = await store.search(query, limit=limit)
        if not results:
            return (_ok(id_, _text_content(f"No results found for query {query!r}")))
        if not args.get("include_messages"):
            results = [_call_summary(i + 1, r) for i, r in enumerate(results)]
        return (_ok(id_, _text_content(json.dumps(results, indent=2, default=str))))

    if name == "list_runs":
        limit = max(1, min(int(args.get("limit", 20)), 100))
        raw = await store.list_runs(limit=limit)
        ended = await store.get_run_end_markers([r["run_id"] for r in raw])
        stopped = set((await store.get_labels("stopped")).keys())
        runs = [_run_status(r, explicitly_ended=r["run_id"] in ended,
                            stopped=r["run_id"] in stopped) for r in raw]
        return (_ok(id_, _text_content(json.dumps(runs, indent=2, default=str))))

    if name == "get_run_status":
        run_id = args.get("run_id", "").strip()
        if not run_id:
            return (_err(id_, -32602, "run_id is required"))
        run = await store.get_run(run_id)
        if run is None:
            return (_err(id_, -32602, f"No run found for run_id {run_id!r}"))
        ended = await store.get_run_end_markers([run_id])
        stopped = set((await store.get_labels("stopped")).keys())
        return (_ok(id_, _text_content(json.dumps(
            _run_status(run, explicitly_ended=run_id in ended,
                        stopped=run_id in stopped), indent=2, default=str))))

    return (_err(id_, -32601, f"Unknown tool: {name!r}"))


# ── JSON-RPC helpers ─────────────────────────────────────────────────────────

def _ok(id_: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _err(id_: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _text_content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}
