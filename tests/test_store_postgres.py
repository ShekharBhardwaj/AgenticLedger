"""Postgres-backend regression tests for agenticledger/proxy/store.py.

These exercise the production backend against a real Postgres server. They are
skipped unless ``AGENTICLEDGER_TEST_PG_DSN`` is set, e.g.::

    AGENTICLEDGER_TEST_PG_DSN=postgresql://postgres:postgres@127.0.0.1:5432/agenticledger_test \\
        pytest tests/test_store_postgres.py

CI provides this via a Postgres service container.

The headline regression: agent session ids are arbitrary strings (the proxy mints
``auto-<date>`` when no header is supplied, and users pass human-readable run
names). The Postgres backend previously typed ``session_id`` as ``UUID`` and cast
every id with ``uuid.UUID(...)``, so non-UUID ids raised and — because the proxy
save path is fail-open — were silently dropped. ``session_id`` is now ``TEXT``.
"""

import os
import time
import uuid

import pytest
import pytest_asyncio

from agenticledger.proxy.normalize import CanonicalRequest, CanonicalResponse
from agenticledger.proxy.store import Store

PG_DSN = os.environ.get("AGENTICLEDGER_TEST_PG_DSN")

pytestmark = pytest.mark.skipif(
    not PG_DSN, reason="set AGENTICLEDGER_TEST_PG_DSN to run Postgres backend tests"
)


def _req(content="hello", model="gpt-4o"):
    return CanonicalRequest(
        messages=[{"role": "user", "content": content}],
        model_id=model, provider="openai", timestamp=time.time(),
    )


def _resp(cost=0.0005):
    return CanonicalResponse(
        content="ok", tool_calls=None, stop_reason="stop",
        tokens_in=10, tokens_out=5, latency_ms=12.0, cost_usd=cost,
    )


@pytest_asyncio.fixture
async def pg_store():
    # Start every test from a pristine schema built by Store.connect itself, so
    # the migration test (which rebuilds the table) can't pollute its neighbors.
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    await conn.execute("DROP TABLE IF EXISTS llm_calls, api_tokens")
    await conn.close()

    store = await Store.connect(PG_DSN)
    try:
        yield store
    finally:
        await store.close()


# ── The regression: non-UUID session ids must round-trip ──────────────────────

@pytest.mark.parametrize("session_id", ["auto-2026-06-19", "my-human-readable-run", "プロジェクト"])
async def test_non_uuid_session_id_round_trips(pg_store, session_id):
    """A non-UUID session id saves and is retrievable (previously dropped on PG)."""
    action_id = str(uuid.uuid4())
    await pg_store.save(action_id, _req(), _resp(), session_id=session_id,
                        agent_name="A", status_code=200)

    record = await pg_store.get(action_id)
    assert record is not None, "save must persist; non-UUID session ids must not be dropped"
    assert record["session_id"] == session_id

    rows = await pg_store.get_session(session_id)
    assert [r["action_id"] for r in rows] == [action_id]


async def test_uuid_shaped_session_id_still_works(pg_store):
    """A UUID-shaped session id keeps working after the TEXT change."""
    session_id = str(uuid.uuid4())
    action_id = str(uuid.uuid4())
    await pg_store.save(action_id, _req(), _resp(), session_id=session_id, status_code=200)
    rows = await pg_store.get_session(session_id)
    assert len(rows) == 1


async def test_session_cost_and_delete_with_non_uuid_id(pg_store):
    """Cost aggregation and delete work for non-UUID session ids."""
    sid = "auto-2026-06-19"
    for _ in range(3):
        await pg_store.save(str(uuid.uuid4()), _req(), _resp(cost=0.001),
                            session_id=sid, status_code=200)
    # A failed call should not count toward cost.
    await pg_store.save(str(uuid.uuid4()), _req(), _resp(cost=0.001),
                        session_id=sid, status_code=500)

    assert round(await pg_store.get_session_cost(sid), 6) == 0.003
    assert await pg_store.delete_session(sid) == 4
    assert await pg_store.get_session(sid) == []


async def test_ping_succeeds(pg_store):
    """The Postgres readiness ping runs a trivial query without raising."""
    await pg_store.ping()


async def test_purge_older_than(pg_store):
    """Retention purge deletes only rows older than the cutoff."""
    now = time.time()
    old = CanonicalRequest(messages=[{"role": "user", "content": "x"}],
                           model_id="gpt-4o", provider="openai", timestamp=now - 10 * 86400)
    new = CanonicalRequest(messages=[{"role": "user", "content": "y"}],
                           model_id="gpt-4o", provider="openai", timestamp=now - 3600)
    old_id, new_id = str(uuid.uuid4()), str(uuid.uuid4())
    await pg_store.save(old_id, old, _resp(), session_id="s")
    await pg_store.save(new_id, new, _resp(), session_id="s")

    assert await pg_store.purge_older_than(now - 7 * 86400) == 1
    assert [r["action_id"] for r in await pg_store.get_session("s")] == [new_id]


async def test_delete_user(pg_store):
    """Right-to-erasure deletes only the target user's calls."""
    d1, d2 = str(uuid.uuid4()), str(uuid.uuid4())
    await pg_store.save(d1, _req(), _resp(), session_id="s1", user_id="u1", status_code=200)
    await pg_store.save(d2, _req(), _resp(), session_id="s2", user_id="u2", status_code=200)
    assert await pg_store.delete_user("u1") == 1
    assert await pg_store.get(d1) is None
    assert await pg_store.get(d2) is not None


async def test_audit_crud(pg_store):
    """Audit entries append and list newest-first."""
    await pg_store.add_audit({
        "id": "au1", "timestamp": time.time(), "action": "export_session", "target": "s1",
        "actor_role": "admin", "actor_source": "master", "actor": None, "details": None,
        "client": "10.0.0.1",
    })
    rows = await pg_store.list_audit(limit=10)
    assert rows[0]["action"] == "export_session" and rows[0]["target"] == "s1"
    assert rows[0]["actor_source"] == "master"


async def test_token_crud(pg_store):
    """API token create/get/list/revoke round-trips on Postgres."""
    from agenticledger.proxy.auth import generate_token

    raw, token_hash = generate_token()
    await pg_store.create_token("pt1", "ci", token_hash, "editor", time.time(), None)

    row = await pg_store.get_token_by_hash(token_hash)
    assert row["token_id"] == "pt1" and row["role"] == "editor" and row["revoked_at"] is None

    listed = await pg_store.list_tokens()
    assert [t["token_id"] for t in listed] == ["pt1"]
    assert "token_hash" not in listed[0]

    assert await pg_store.revoke_token("pt1", time.time()) == 1
    assert (await pg_store.get_token_by_hash(token_hash))["revoked_at"] is not None
    assert await pg_store.revoke_token("pt1", time.time()) == 0


async def test_list_sessions_aggregates_non_uuid_ids(pg_store):
    """list_sessions groups and counts non-UUID session ids."""
    await pg_store.save(str(uuid.uuid4()), _req(), _resp(), session_id="run-a", status_code=200)
    await pg_store.save(str(uuid.uuid4()), _req(), _resp(), session_id="run-a", status_code=200)
    await pg_store.save(str(uuid.uuid4()), _req(), _resp(), session_id="run-b", status_code=200)

    sessions = {s["session_id"]: s["call_count"] for s in await pg_store.list_sessions()}
    assert sessions == {"run-a": 2, "run-b": 1}


# ── The migration path: legacy UUID column is converted in place ──────────────

async def test_legacy_uuid_column_is_migrated_to_text():
    """Connecting to a DB whose session_id is still UUID migrates it to TEXT."""
    import asyncpg

    # Rebuild the table with the OLD base schema — identical to the original
    # CREATE TABLE except session_id is still UUID. The later migration columns
    # (user_id, agent_name, …) are added by Store.connect via ADD COLUMN IF NOT EXISTS.
    conn = await asyncpg.connect(PG_DSN)
    await conn.execute("DROP TABLE IF EXISTS llm_calls")
    await conn.execute(
        "CREATE TABLE llm_calls ("
        " action_id UUID PRIMARY KEY, session_id UUID, timestamp TIMESTAMPTZ NOT NULL,"
        " model_id TEXT NOT NULL, provider TEXT NOT NULL, messages JSONB NOT NULL,"
        " tools JSONB, content TEXT, tool_calls JSONB, stop_reason TEXT,"
        " tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER)"
    )
    await conn.close()

    # Store.connect must detect uuid and ALTER the column to TEXT.
    store = await Store.connect(PG_DSN)
    try:
        async with store._pool.acquire() as c:
            col_type = await c.fetchval(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'llm_calls' AND column_name = 'session_id'"
            )
        assert col_type == "text"

        # And a non-UUID id now persists on the migrated table.
        action_id = str(uuid.uuid4())
        await store.save(action_id, _req(), _resp(), session_id="auto-2026-06-19", status_code=200)
        assert (await store.get(action_id))["session_id"] == "auto-2026-06-19"
    finally:
        await store.close()
