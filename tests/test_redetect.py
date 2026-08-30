"""POST /api/redetect: history catches up with what the detector has
learned. Gaps only, idempotent, audited. This endpoint is the shipped
path for attribution backfills — never a hand-run UPDATE."""

import asyncio
import time

import httpx2 as httpx

from agenticledger.proxy.normalize import CanonicalRequest, CanonicalResponse
from agenticledger.proxy.store import Store

from .conftest import openai_response

OPENCLAW_SYSTEM = ("You are a personal assistant running inside OpenClaw.\n"
                   "## Tooling\n- exec: Run shell commands")


def _seed_legacy_db(dsn: str) -> None:
    """Write calls the way a pre-detector release stored them: no
    framework, no agent_name — plus one hand-attributed row that redetect
    must never touch."""
    async def go():
        st = await Store.connect(dsn)
        try:
            async def save(action_id, system=None, framework=None, agent=None):
                await st.save(
                    action_id,
                    CanonicalRequest(
                        messages=[{"role": "user", "content": "hi"}],
                        model_id="claude-opus-5", provider="anthropic",
                        timestamp=time.time(), system_prompt=system),
                    CanonicalResponse(
                        content="ok", tool_calls=None, stop_reason="stop",
                        tokens_in=5, tokens_out=2, latency_ms=10.0,
                        cost_usd=0.0001),
                    session_id="legacy", framework=framework, agent_name=agent,
                )
            await save("legacy-openclaw", system=OPENCLAW_SYSTEM)
            await save("legacy-plain")  # nothing detectable: must stay bare
            await save("hand-named", system=OPENCLAW_SYSTEM,
                       framework="custom", agent="my-agent")
        finally:
            await st.close()
    asyncio.run(go())


def test_redetect_fills_gaps_and_only_gaps(proxy, tmp_path):
    dsn = f"sqlite:///{tmp_path / 'legacy.db'}"
    _seed_legacy_db(dsn)
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   dsn=dsn)

    result = client.post("/api/redetect").json()
    assert result["updated"] == 1  # only the detectable, unattributed row

    rows = {r["action_id"]: r for r in client.get("/session/legacy").json()}
    assert rows["legacy-openclaw"]["framework"] == "openclaw"
    assert rows["legacy-openclaw"]["agent_name"] == "openclaw"
    # Capture-time attribution is sacred.
    assert rows["hand-named"]["framework"] == "custom"
    assert rows["hand-named"]["agent_name"] == "my-agent"
    # Undetectable rows stay honestly bare.
    assert rows["legacy-plain"]["framework"] is None

    # Idempotent: a second pass finds nothing left to name.
    assert client.post("/api/redetect").json()["updated"] == 0

    actions = [row["action"] for row in client.get("/api/audit").json()]
    assert "redetect" in actions


def test_sweep_reaches_past_a_wall_of_undetectable_rows(proxy, tmp_path):
    """The bite this test forbids: >1 batch of undetectable rows in front
    must not hide detectable rows behind them."""
    dsn = f"sqlite:///{tmp_path / 'wall.db'}"

    async def seed():
        st = await Store.connect(dsn)
        try:
            base = time.time() - 10_000
            for i in range(505):   # a full batch page of nothing-to-find
                await st.save(
                    f"noise-{i:04d}",
                    CanonicalRequest(messages=[{"role": "user", "content": "x"}],
                                     model_id="gpt-4o", provider="openai",
                                     timestamp=base + i),
                    CanonicalResponse(content="ok", tool_calls=None,
                                      stop_reason="stop", tokens_in=1,
                                      tokens_out=1, latency_ms=1.0,
                                      cost_usd=0.0),
                    session_id="wall")
            for i in range(3):     # the rows hiding behind the wall
                await st.save(
                    f"treasure-{i}",
                    CanonicalRequest(messages=[{"role": "user", "content": "y"}],
                                     model_id="claude-opus-5",
                                     provider="anthropic",
                                     timestamp=base + 1000 + i,
                                     system_prompt=OPENCLAW_SYSTEM),
                    CanonicalResponse(content="ok", tool_calls=None,
                                      stop_reason="stop", tokens_in=1,
                                      tokens_out=1, latency_ms=1.0,
                                      cost_usd=0.0),
                    session_id="wall")
        finally:
            await st.close()
    asyncio.run(seed())

    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   dsn=dsn)
    result = client.post("/api/redetect").json()
    assert result["examined"] == 508
    assert result["updated"] == 3
