"""The compressor chain (docs/chaining.md): agent -> ledger -> second
proxy -> upstream. The docs claim the chain mechanics are proven by
test; this is that test. The back proxy runs as a REAL subprocess
server and the hops speak real HTTP, because two in-process ASGI test
clients share event loops in ways loop-bound database drivers reject
(the Postgres CI matrix caught exactly that).
"""

import json
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx2 as httpx
import pytest

REPLY = {"id": "c", "object": "chat.completion", "model": "gpt-4o",
         "choices": [{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": "through both hops"}}],
         "usage": {"prompt_tokens": 900, "completion_tokens": 30}}


class _Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        body = json.dumps(REPLY).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(os.name != "posix", reason="subprocess server test is POSIX-only")
def test_chain_records_on_both_hops_and_keeps_attribution(proxy, tmp_path):
    provider_port, back_port = _free_port(), _free_port()

    provider = ThreadingHTTPServer(("127.0.0.1", provider_port), _Provider)
    Thread(target=provider.serve_forever, daemon=True).start()

    env = {**os.environ,
           "AGENTICLEDGER_PORT": str(back_port),
           "AGENTICLEDGER_DSN": f"sqlite:///{tmp_path}/back.db",
           "AGENTICLEDGER_UPSTREAM_URL": f"http://127.0.0.1:{provider_port}"}
    env.pop("AGENTICLEDGER_API_KEY", None)
    back = subprocess.Popen([sys.executable, "-m", "agenticledger.proxy"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            time.sleep(0.25)
            try:
                if httpx.get(f"http://127.0.0.1:{back_port}/health", timeout=2).status_code == 200:
                    break
            except Exception:
                continue
        else:
            pytest.fail("back proxy never came up")

        # The FRONT ledger: the standard fixture (suite's own store backend),
        # its upstream client swapped for a REAL one aimed at the back hop.
        front = proxy(upstream_url=f"http://127.0.0.1:{back_port}")
        front.app.state.client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{back_port}", timeout=httpx.Timeout(30.0))

        body = {"model": "gpt-4o",
                "messages": [{"role": "system", "content": "chain system prompt " * 40},
                             {"role": "user", "content": "prove the chain"}]}
        resp = front.post("/r/chain-proof/1/v1/chat/completions", json=body,
                          headers={"x-agenticledger-session-id": "chain-s"})
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == "through both hops"

        # Front hop: recorded, priced, run identity held.
        runs = {r["run_id"]: r for r in front.get("/api/runs").json()}
        assert "chain-proof" in runs and runs["chain-proof"]["call_count"] == 1
        rows = front.get("/session/chain-s").json()
        assert len(rows) == 1 and rows[0]["cost_usd"] and rows[0]["cost_usd"] > 0

        # Back hop: the second proxy captured the same traffic independently.
        back_rows = httpx.get(f"http://127.0.0.1:{back_port}/api/sessions", timeout=5).json()
        assert sum(s["call_count"] for s in back_rows) == 1
    finally:
        back.terminate()
        back.wait(timeout=10)
        provider.shutdown()
