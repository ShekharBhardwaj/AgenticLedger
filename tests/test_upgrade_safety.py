"""Upgrade safety: a database created by a past release must open, migrate,
and read correctly under current code, forever. A recorder that can eat its
own history on upgrade is not a recorder.

The DDL here is FROZEN from the named release tags (extracted with
`git show <tag>:agenticledger/proxy/store.py`). Do not modernize it; its
age is the point. When a future release changes the schema, add a new
frozen era rather than editing an old one.
"""

import json
import os
import sqlite3
import time

import pytest

from agenticledger.proxy.normalize import CanonicalRequest, CanonicalResponse
from agenticledger.proxy.store import Store

# ── Era: v0.6.0 SQLite (base table only; every later column arrived via
#    ALTER TABLE migrations) ─────────────────────────────────────────────────
SQLITE_0_6_0_DDL = """
CREATE TABLE llm_calls (
    action_id   TEXT PRIMARY KEY,
    session_id  TEXT,
    timestamp   REAL NOT NULL,
    model_id    TEXT NOT NULL,
    provider    TEXT NOT NULL,
    messages    TEXT NOT NULL,
    tools       TEXT,
    content     TEXT,
    tool_calls  TEXT,
    stop_reason TEXT,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    latency_ms  INTEGER
)
"""

# ── Era: v0.8.2 Postgres (action_id was UUID-typed; REAL migration columns
#    were float4; both were migrated in 0.9) ────────────────────────────────
PG_0_8_2_DDL = """
CREATE TABLE llm_calls (
    action_id   UUID        PRIMARY KEY,
    session_id  TEXT,
    timestamp   TIMESTAMPTZ NOT NULL,
    model_id    TEXT        NOT NULL,
    provider    TEXT        NOT NULL,
    messages    JSONB       NOT NULL,
    tools       JSONB,
    content     TEXT,
    tool_calls  JSONB,
    stop_reason TEXT,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    latency_ms  INTEGER,
    cost_usd    REAL
)
"""

PG_DSN = os.environ.get("AGENTICLEDGER_TEST_PG_DSN") or os.environ.get(
    "AGENTICLEDGER_TEST_FULL_PG_DSN")


async def test_a_0_6_0_sqlite_db_opens_and_keeps_its_history(tmp_path):
    db_path = tmp_path / "era-0.6.0.db"
    conn = sqlite3.connect(db_path)
    conn.execute(SQLITE_0_6_0_DDL)
    conn.execute(
        "INSERT INTO llm_calls (action_id, session_id, timestamp, model_id,"
        " provider, messages, content, tokens_in, tokens_out, latency_ms)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("legacy-0001", "old-session", 1750000000.0, "gpt-4o", "openai",
         json.dumps([{"role": "user", "content": "from the past"}]),
         "an answer from 0.6.0", 100, 20, 900),
    )
    conn.commit()
    conn.close()

    store = await Store.connect(f"sqlite:///{db_path}")
    try:
        # History survives, with post-0.6 columns present and empty.
        old = await store.get("legacy-0001")
        assert old is not None
        assert old["content"] == "an answer from 0.6.0"
        assert old["messages"][0]["content"] == "from the past"
        assert old["cost_usd"] is None          # column added by migration
        assert "cache_read_tokens" in old       # ditto

        sessions = await store.list_sessions()
        assert any(s["session_id"] == "old-session" for s in sessions)

        # New captures land beside the old ones.
        await store.save(
            "modern-0001",
            CanonicalRequest(messages=[{"role": "user", "content": "today"}],
                             model_id="gpt-4o", provider="openai",
                             timestamp=time.time()),
            CanonicalResponse(content="hi", tool_calls=None,
                              stop_reason="stop", tokens_in=10, tokens_out=5,
                              latency_ms=100.0, cost_usd=0.001),
            session_id="old-session",
        )
        both = await store.get_session("old-session")
        assert [r["action_id"] for r in both] == ["legacy-0001", "modern-0001"]
    finally:
        await store.close()


@pytest.mark.skipif(not PG_DSN, reason="set AGENTICLEDGER_TEST_PG_DSN")
async def test_a_0_8_2_postgres_db_migrates_ids_and_precision(tmp_path):
    import asyncpg

    conn = await asyncpg.connect(PG_DSN)
    await conn.execute(
        "DROP TABLE IF EXISTS llm_calls, api_tokens, audit_log,"
        " tool_executions, run_markers, labels")
    await conn.execute(PG_0_8_2_DDL)
    await conn.execute(
        "INSERT INTO llm_calls (action_id, session_id, timestamp, model_id,"
        " provider, messages, content, tokens_in, tokens_out, cost_usd)"
        " VALUES ($1::uuid, $2, now(), $3, $4, $5::jsonb, $6, $7, $8, $9)",
        "9e107d9d-3721-4a41-8451-2ee12c58cadb", "pg-old-session", "gpt-4o",
        "openai", json.dumps([{"role": "user", "content": "uuid era"}]),
        "answer", 100, 20, 0.006)
    await conn.close()

    store = await Store.connect(PG_DSN)
    try:
        # The UUID row is still readable, by its string id.
        old = await store.get("9e107d9d-3721-4a41-8451-2ee12c58cadb")
        assert old is not None and old["content"] == "answer"
        # float4 precision noise is gone after the DOUBLE PRECISION migration.
        assert old["cost_usd"] == 0.006

        # A non-UUID id: unfindable, not an exception (and savable).
        assert await store.get("not-a-uuid-at-all") is None
        await store.save(
            "plain-string-id",
            CanonicalRequest(messages=[{"role": "user", "content": "now"}],
                             model_id="gpt-4o", provider="openai",
                             timestamp=time.time()),
            CanonicalResponse(content="ok", tool_calls=None,
                              stop_reason="stop", tokens_in=1, tokens_out=1,
                              latency_ms=5.0, cost_usd=0.0001),
            session_id="pg-old-session",
        )
        assert (await store.get("plain-string-id"))["content"] == "ok"

        # The column types really migrated.
        check = await Store.connect(PG_DSN)
        await check.close()
        conn = await asyncpg.connect(PG_DSN)
        types = {r["column_name"]: r["data_type"] for r in await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_name = 'llm_calls'")}
        await conn.close()
        assert types["action_id"] == "text"
        assert types["cost_usd"] == "double precision"
    finally:
        await store.close()
