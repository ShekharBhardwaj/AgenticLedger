"""Wire-truth recorder for the parity corpus (#97).

Sits between an agent and a ledger, forwards everything untouched, and
records each exchange as a fixture: request (method, path, headers,
body) and response (status, headers, body, streamed chunks as received).
Quirks stay (billing nonces, cache markers, session ids); secrets go.

    python scripts/wiretap.py --listen 8070 --forward http://127.0.0.1:8057 \
        --out tests/fixtures/wire --tag claude-code-main

Then point the agent at http://127.0.0.1:8070. Every request lands as
<out>/<tag>-<n>.json. Review before committing: the scrubber removes
credential headers and the home-directory username, nothing else.
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse

_SECRET_HEADERS = {"authorization", "x-api-key", "cookie", "set-cookie",
                   "x-agenticledger-api-key", "x-amz-security-token"}
_HOME = str(Path.home())


_USER = Path.home().name
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Claude Code injects the user's private memory index into its context
# companion call. The block's SHAPE is the quirk worth keeping; its prose
# is the user's. Replace the prose, keep the markers around it.
_MEMORY_RE = re.compile(r"(# claudeMd\\n).*?(?=\\n# userEmail)", re.S)


def _scrub_text(text: str) -> str:
    """Fixtures are public. Keep every wire quirk; remove every secret."""
    text = text.replace(_HOME, "/Users/user")
    # Username in any encoding (plain, dash-joined slugs, path fragments).
    text = re.sub(re.escape(_USER), "user", text, flags=re.I)
    text = _EMAIL_RE.sub("user@example.com", text)
    text = _MEMORY_RE.sub(r"\1[user memory notes removed for privacy]", text)
    # Belt and braces: anything that looks like an Anthropic/OpenAI key.
    text = re.sub(r"sk-(?:ant-)?[A-Za-z0-9_-]{16,}", "sk-REDACTED", text)
    return text


def _scrub_headers(headers) -> dict:
    return {k: ("REDACTED" if k.lower() in _SECRET_HEADERS else v)
            for k, v in headers.items()}


def build(forward: str, out: Path, tag: str, record_all: bool = False) -> Starlette:
    out.mkdir(parents=True, exist_ok=True)
    counter = {"n": 0}
    client = httpx.AsyncClient(base_url=forward, timeout=120.0)

    async def relay(request: Request):
        body = await request.body()
        # Record LLM exchanges only unless asked otherwise: a dashboard tab
        # pointed at the tap once swept 29 asset GETs into the corpus.
        record_this = record_all or request.url.path.startswith("/v1/")
        if record_this:
            counter["n"] += 1
        n = counter["n"]
        started = time.time()
        upstream = client.build_request(
            request.method, request.url.path + (f"?{request.url.query}" if request.url.query else ""),
            headers=[(k, v) for k, v in request.headers.items()
                     if k.lower() not in ("host", "accept-encoding")]
            + [("accept-encoding", "gzip, deflate")],  # same rule as the proxy (#101)
            content=body,
        )
        resp = await client.send(upstream, stream=True)
        chunks: list[bytes] = []

        async def gen():
            # aiter_bytes, not aiter_raw: upstreams gzip non-streaming
            # bodies, and a fixture must hold the bytes the pipeline reads.
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                yield chunk
            await resp.aclose()
            if not record_this:
                return
            record = {
                "tag": tag,
                "captured_at": started,
                "request": {
                    "method": request.method,
                    "path": request.url.path,
                    "query": request.url.query,
                    "headers": _scrub_headers(request.headers),
                    "body": _scrub_text(body.decode("utf-8", errors="replace")),
                },
                "response": {
                    "status": resp.status_code,
                    "headers": _scrub_headers(resp.headers),
                    "latency_ms": round((time.time() - started) * 1000, 1),
                    "chunks": [_scrub_text(c.decode("utf-8", errors="replace")) for c in chunks],
                },
            }
            path = out / f"{tag}-{n:02d}.json"
            path.write_text(json.dumps(record, indent=2) + "\n")
            print(f"recorded {path.name}: {request.method} {request.url.path} -> {resp.status_code}, {len(chunks)} chunks")

        passthrough = {k: v for k, v in resp.headers.items()
                       if k.lower() not in ("content-length", "transfer-encoding", "content-encoding")}
        return StreamingResponse(gen(), status_code=resp.status_code, headers=passthrough)

    app = Starlette()
    app.add_route("/{path:path}", relay, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--listen", type=int, default=8070)
    ap.add_argument("--forward", default="http://127.0.0.1:8057")
    ap.add_argument("--out", default="tests/fixtures/wire")
    ap.add_argument("--tag", default=os.environ.get("WIRETAP_TAG", "capture"))
    ap.add_argument("--all", action="store_true", help="record non-LLM paths too")
    args = ap.parse_args()
    uvicorn.run(build(args.forward, Path(args.out), args.tag, record_all=args.all),
                host="127.0.0.1", port=args.listen, log_level="warning")
