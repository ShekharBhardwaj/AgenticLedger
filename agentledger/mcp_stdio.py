"""
Stdio transport for the Agentic Ledger MCP server.

Speaks newline-delimited JSON-RPC on stdin/stdout — the transport MCP
clients use when they launch a server as a subprocess (Claude Desktop
"command" configs, Glama's inspection harness, Cursor's stdio servers).
It shares the exact tool dispatch with the proxy's HTTP endpoint; the only
difference is the doorway.

Run it with:

    agentledger mcp

It reads the same database the proxy writes. Point it at the proxy's
database with AGENTLEDGER_DSN (default: sqlite:///agentledger.db in the
current directory). SQLite handles the concurrent reader alongside a
running proxy.

Protocol discipline: stdout carries ONLY JSON-RPC responses — anything
else corrupts the stream. Diagnostics go to stderr.
"""

import asyncio
import contextlib
import json
import os
import sys

from .proxy.mcp import dispatch_message
from .proxy.store import Store


async def serve() -> None:
    dsn = os.environ.get("AGENTLEDGER_DSN", "sqlite:///agentledger.db")
    store = await Store.connect(dsn)
    print(f"agentledger mcp: serving stdio (store: {dsn})", file=sys.stderr)
    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:  # EOF — client closed the pipe
                break
            line = line.strip()
            if not line:
                continue
            try:
                body = json.loads(line)
            except json.JSONDecodeError:
                sys.stdout.write(json.dumps(
                    {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "Parse error"}}) + "\n")
                sys.stdout.flush()
                continue
            try:
                response = await dispatch_message(
                    body if isinstance(body, dict) else {}, store)
            except Exception as exc:  # tool errors must not kill the transport
                response = {"jsonrpc": "2.0", "id": body.get("id") if isinstance(body, dict) else None,
                            "error": {"code": -32603, "message": f"Internal error: {exc}"}}
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
    finally:
        await store.close()


def main() -> int:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
