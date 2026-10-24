"""Seed a small, deterministic ledger for the dashboard smoke tests (#98).

    python scripts/seed_smoke_ledger.py /path/to/smoke.db

Creates two runs (one renamed, one long finished), sessions with tool
calls, a flagged call, and a blocked call, so every surface the smoke
test visits has something real to render. Rewrites the file each time.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agenticledger.proxy.hooks_ingest import event_from_hook  # noqa: E402
from agenticledger.proxy.normalize import CanonicalRequest, CanonicalResponse  # noqa: E402
from agenticledger.proxy.store import Store  # noqa: E402


async def seed(path: Path) -> None:
    if path.exists():
        path.unlink()
    store = await Store.connect(f"sqlite:///{path}")
    now = time.time()
    n = 0

    async def call(run_id, iteration, session, ts, *, tool=None, flags=None,
                   status=200, error=None, cost=0.0012):
        nonlocal n
        n += 1
        tool_calls = [{"name": tool, "arguments": '{"command": "date"}'}] if tool else None
        await store.save(
            f"{n:032x}",
            CanonicalRequest(
                messages=[{"role": "user", "content": f"{run_id} step {iteration}"}],
                model_id="claude-sonnet-5", provider="anthropic", timestamp=ts,
                system_prompt="You are the smoke-test worker.",
            ),
            CanonicalResponse(
                content=None if tool else "ok", tool_calls=tool_calls,
                stop_reason="tool_use" if tool else "stop",
                tokens_in=120, tokens_out=30, latency_ms=640.0, cost_usd=cost,
            ),
            session_id=session, run_id=run_id, iteration=iteration,
            framework="claude-code", agent_name="claude-code",
            loop_flags=flags, status_code=status, error_detail=error,
        )

    # A live-looking named run with three iterations and a tool round.
    for it in (1, 2, 3):
        base = now - 300 + it * 60
        await call("smoke-loop", it, f"smoke-loop-s{it}", base, tool="Bash")
        await call("smoke-loop", it, f"smoke-loop-s{it}", base + 5)
    await call("smoke-loop", 3, "smoke-loop-s3", now - 100,
               flags='["repeat_tool_call"]', tool="Bash")
    await store.set_label("run", "smoke-loop", name="Smoke loop")

    # The agent's own record for the third session, as Claude Code hooks
    # would post it: a tool run, a denied one, a sub-agent, the end.
    t0 = now - 180
    hooks = [
        {"hook_event_name": "SessionStart", "session_id": "smoke-loop-s3", "source": "startup", "cwd": "/work/app"},
        {"hook_event_name": "UserPromptSubmit", "session_id": "smoke-loop-s3", "prompt": "fix the failing date test"},
        {"hook_event_name": "PostToolUse", "session_id": "smoke-loop-s3", "tool_name": "Bash",
         "tool_use_id": "tu_1", "tool_input": {"command": "date"}, "tool_response": "Thu Oct 23 09:00:00 2026",
         "duration_ms": 412},
        {"hook_event_name": "PermissionDenied", "session_id": "smoke-loop-s3", "tool_name": "Bash",
         "tool_use_id": "tu_2", "tool_input": {"command": "rm -rf build"},
         "reason": "Permission rule deny: Bash(rm -rf*)"},
        {"hook_event_name": "SubagentStart", "session_id": "smoke-loop-s3", "agent_id": "sub-1", "agent_type": "Explore"},
        {"hook_event_name": "SubagentStop", "session_id": "smoke-loop-s3", "agent_id": "sub-1", "agent_type": "Explore"},
        {"hook_event_name": "Stop", "session_id": "smoke-loop-s3", "last_assistant_message": "Done: the test passes."},
        {"hook_event_name": "SessionEnd", "session_id": "smoke-loop-s3", "reason": "prompt_input_exit"},
    ]
    events = [event_from_hook(body, t0 + i * 7) for i, body in enumerate(hooks)]
    # And a session the proxy never saw: Claude Code behind a managed
    # gateway, where only the hooks reach the ledger.
    gateway = [
        {"hook_event_name": "SessionStart", "session_id": "gateway-only-s1", "source": "startup"},
        {"hook_event_name": "PostToolUse", "session_id": "gateway-only-s1", "tool_name": "Read",
         "tool_use_id": "tu_9", "tool_input": {"file_path": "/work/app/README.md"},
         "tool_response": "# App\n", "duration_ms": 12},
        {"hook_event_name": "Stop", "session_id": "gateway-only-s1", "last_assistant_message": "Read it."},
    ]
    events += [event_from_hook(body, now - 40 + i * 5) for i, body in enumerate(gateway)]
    await store.save_agent_events([e for e in events if e])

    # A long-finished run with a blocked refusal on its record.
    for it in (1, 2):
        await call("old-batch", it, f"old-batch-s{it}", now - 86400 * 2 + it * 30)
    await call("old-batch", None, "old-batch-s3", now - 86400 * 2 + 90,
               status=402, error="blocked: calls under run 'old-batch' are blocked by the operator",
               cost=0.0)
    await store.close()
    print(f"seeded {n} calls into {path}")


if __name__ == "__main__":
    asyncio.run(seed(Path(sys.argv[1])))
