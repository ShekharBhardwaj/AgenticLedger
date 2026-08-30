"""Reproducible load test behind the README's published numbers.

Two questions an adopter asks, answered with measurements, not adjectives:

1. Capture throughput: how many proxied calls per second can the ledger
   sustain end to end (HTTP in, upstream forwarded, response captured)?
   Measured through the real ASGI app with an in-process mock upstream, so
   the number isolates the ledger's own overhead from provider latency.

2. Scale: what happens at a million captured calls? Database size on disk,
   and the latency of the dashboard's hot endpoints at that size.

Run it yourself (SQLite by default; pass a Postgres DSN to measure that):

    python scripts/loadtest.py --calls 2000 --seed 1000000
    python scripts/loadtest.py --dsn postgresql://... --seed 1000000

Numbers vary with hardware; the README states the machine used.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx2 as httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenticledger.proxy.app import create_app  # noqa: E402
from agenticledger.proxy.normalize import (  # noqa: E402
    CanonicalRequest,
    CanonicalResponse,
)
from agenticledger.proxy.store import Store  # noqa: E402

UPSTREAM = "http://upstream.loadtest"


def _openai_response() -> dict:
    return {
        "id": "chatcmpl-load", "object": "chat.completion", "model": "gpt-4o",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 40,
                  "total_tokens": 160},
    }


async def measure_throughput(dsn: str, calls: int, concurrency: int) -> dict:
    """Proxied calls/sec through the real app with a mock upstream."""
    app = create_app(upstream_url=UPSTREAM, dsn=dsn)
    payload = {"model": "gpt-4o",
               "messages": [{"role": "user", "content": "load test"}]}
    canned = _openai_response()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ledger.test"
    ) as client, app.router.lifespan_context(app):
        app.state.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, json=canned)),
            base_url=UPSTREAM,
        )
        # Warm up (schema, first-connection costs stay out of the number).
        await client.post("/v1/chat/completions", json=payload)

        latencies: list[float] = []
        sem = asyncio.Semaphore(concurrency)

        async def one(i: int) -> None:
            async with sem:
                t0 = time.perf_counter()
                r = await client.post(
                    "/v1/chat/completions", json=payload,
                    headers={"x-agenticledger-session-id": f"load-{i % 50}"},
                )
                r.raise_for_status()
                latencies.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        await asyncio.gather(*(one(i) for i in range(calls)))
        wall = time.perf_counter() - t0

    latencies.sort()
    return {
        "calls": calls,
        "concurrency": concurrency,
        "wall_seconds": round(wall, 2),
        "calls_per_second": round(calls / wall, 1),
        "p50_ms": round(1000 * statistics.median(latencies), 2),
        "p95_ms": round(1000 * latencies[int(0.95 * len(latencies)) - 1], 2),
    }


async def measure_scale(dsn: str, seed: int, db_path: Path | None) -> dict:
    """Seed `seed` calls directly through the store, then time the
    dashboard's hot endpoints at that size."""
    store = await Store.connect(dsn)
    try:
        t0 = time.perf_counter()
        batch = 1000
        for start in range(0, seed, batch):
            await asyncio.gather(*(
            store.save(
                f"{start + i:032x}",
                CanonicalRequest(
                    messages=[{"role": "user", "content": f"call {start + i}"}],
                    model_id="gpt-4o", provider="openai",
                    timestamp=time.time() - (seed - start - i) * 7.8,  # ~90 days of history
                ),
                CanonicalResponse(
                    content="ok", tool_calls=None, stop_reason="stop",
                    tokens_in=120, tokens_out=40, latency_ms=850.0,
                    cost_usd=0.0006,
                ),
                session_id=f"scale-{(start + i) // 40}",
                agent_name="loadtest",
            )
            for i in range(min(batch, seed - start))
        ))
        seed_wall = time.perf_counter() - t0
    finally:
        await store.close()

    # Dashboard hot paths, through the real app against the seeded store.
    app = create_app(upstream_url=UPSTREAM, dsn=dsn)
    timings: dict[str, float] = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://ledger.test"
    ) as client, app.router.lifespan_context(app):
        for name, url in (
            ("list_sessions", "/api/sessions"),
            ("reports_30d", "/api/reports?days=30"),
            ("one_session", "/session/scale-0"),
        ):
            t = time.perf_counter()
            r = await client.get(url)
            r.raise_for_status()
            timings[name] = round(1000 * (time.perf_counter() - t), 1)

    out = {
        "seeded_calls": seed,
        "seed_calls_per_second": round(seed / seed_wall, 0),
        "endpoint_ms": timings,
    }
    if db_path is not None and db_path.exists():
        out["db_size_mb"] = round(db_path.stat().st_size / 1_048_576, 1)
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--calls", type=int, default=2000)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--seed", type=int, default=1_000_000)
    ap.add_argument("--dsn", default=None,
                    help="Postgres DSN; default is a temp SQLite file")
    args = ap.parse_args()

    if args.dsn:
        dsn, db_path = args.dsn, None
    else:
        tmp = Path(tempfile.mkdtemp(prefix="al-loadtest-")) / "loadtest.db"
        dsn, db_path = f"sqlite:///{tmp}", tmp

    print(f"backend: {dsn.split(':', 1)[0]}", file=sys.stderr)
    results = {"throughput": await measure_throughput(
        dsn, args.calls, args.concurrency)}
    print(json.dumps(results["throughput"]), file=sys.stderr)
    if args.seed:
        results["scale"] = await measure_scale(dsn, args.seed, db_path)
        print(json.dumps(results["scale"]), file=sys.stderr)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("AGENTICLEDGER_LOOP_ACTION", "off")
    raise SystemExit(asyncio.run(main()))
