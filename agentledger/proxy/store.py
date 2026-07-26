"""
Storage backends for Agentic Ledger.

Store.connect(dsn) returns the right backend based on the DSN prefix:

  sqlite:///agentledger.db   → SQLite  (zero setup, great for development)
  postgresql://...           → Postgres (recommended for production)

Schema is created automatically on first connect. New columns are added
non-destructively so existing databases survive upgrades.
"""

import contextlib
import datetime
import json
import uuid
from abc import ABC, abstractmethod
from typing import Any, Optional

from .normalize import CanonicalRequest, CanonicalResponse

_MIGRATION_COLUMNS = [
    ("user_id",          "TEXT"),
    ("agent_name",       "TEXT"),
    ("app_id",           "TEXT"),
    ("parent_action_id", "TEXT"),
    ("environment",      "TEXT"),
    ("system_prompt",    "TEXT"),
    ("temperature",      "REAL"),
    ("max_tokens",       "INTEGER"),
    ("tool_results",     "TEXT"),   # JSON
    ("cost_usd",         "REAL"),
    ("handoff_from",     "TEXT"),
    ("handoff_to",       "TEXT"),
    ("status_code",      "INTEGER"),
    ("error_detail",     "TEXT"),
    ("cache_read_tokens",  "INTEGER"),
    ("cache_write_tokens", "INTEGER"),
    ("thinking",           "TEXT"),
    ("framework",          "TEXT"),
    ("thread_id",          "TEXT"),
    ("step_index",         "INTEGER"),
    ("turn_index",         "INTEGER"),
    ("prev_action_id",     "TEXT"),
    ("run_id",             "TEXT"),
    ("iteration",          "INTEGER"),
    ("loop_flags",         "TEXT"),   # JSON list of flags raised by THIS call
]

# Shared run-aggregation projection — identical SQL in both dialects, so the
# backends can't drift apart. promise_seen = a completion-promise flag was
# observed on any call in the run (the runner-visible "loop is done" signal).
_RUN_AGGREGATE_COLUMNS = """
                run_id,
                MAX(iteration)                AS iterations,
                COUNT(*)                      AS call_count,
                COUNT(DISTINCT session_id)    AS session_count,
                MIN(timestamp)                AS started_at,
                MAX(timestamp)                AS last_call_at,
                SUM(COALESCE(cost_usd, 0))    AS total_cost_usd,
                SUM(COALESCE(tokens_in, 0))   AS total_tokens_in,
                SUM(COALESCE(tokens_out, 0))  AS total_tokens_out,
                MAX(framework)                AS framework,
                SUM(CASE WHEN loop_flags LIKE '%repeat_tool_call%' OR loop_flags LIKE '%step_budget_exceeded%' THEN 1 ELSE 0 END) AS flagged_calls,
                MAX(CASE WHEN loop_flags LIKE '%completion_promise%' THEN 1 ELSE 0 END) AS promise_seen
"""

# Per-iteration projection for the Loop Lens iteration ribbon.
_ITERATION_AGGREGATE_COLUMNS = """
                iteration,
                COUNT(*)                      AS call_count,
                MIN(timestamp)                AS started_at,
                MAX(timestamp)                AS last_call_at,
                SUM(COALESCE(cost_usd, 0))    AS cost_usd,
                SUM(COALESCE(tokens_in, 0))   AS tokens_in,
                SUM(COALESCE(tokens_out, 0))  AS tokens_out,
                SUM(COALESCE(cache_read_tokens, 0)) AS cache_read_tokens,
                SUM(CASE WHEN loop_flags LIKE '%repeat_tool_call%' OR loop_flags LIKE '%step_budget_exceeded%' THEN 1 ELSE 0 END) AS flagged_calls,
                SUM(CASE WHEN status_code != 200 THEN 1 ELSE 0 END)     AS error_calls
"""


# Drill-down projection behind the flagged-calls count — enough context to
# explain each flag without shipping full message bodies.
_FLAGGED_CALL_COLUMNS = (
    "action_id, session_id, thread_id, iteration, step_index, "
    "loop_flags, tool_calls, model_id, timestamp"
)


def _flagged_row(d: dict) -> dict:
    ts = d.get("timestamp")
    if isinstance(ts, (int, float)):
        d["timestamp"] = _unix_to_iso(ts)
    elif ts is not None:
        d["timestamp"] = ts.isoformat()
    if isinstance(d.get("tool_calls"), str):
        with contextlib.suppress(Exception):
            d["tool_calls"] = json.loads(d["tool_calls"])
    return d


def _iteration_row(d: dict) -> dict:
    d["started_at"] = _unix_to_iso(d["started_at"]) if isinstance(d["started_at"], (int, float)) else d["started_at"].isoformat()
    d["last_call_at"] = _unix_to_iso(d["last_call_at"]) if isinstance(d["last_call_at"], (int, float)) else d["last_call_at"].isoformat()
    if d.get("cost_usd") is not None:
        d["cost_usd"] = float(d["cost_usd"])
    return d


class Store(ABC):
    """Common interface — use Store.connect(), not the subclasses directly."""

    @classmethod
    async def connect(cls, dsn: str) -> "Store":
        if dsn.startswith("sqlite"):
            return await _SqliteStore._connect(dsn)
        return await _PostgresStore._connect(dsn)

    @abstractmethod
    async def save(
        self,
        action_id: str,
        req: CanonicalRequest,
        resp: CanonicalResponse,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        app_id: Optional[str] = None,
        parent_action_id: Optional[str] = None,
        environment: str = "development",
        handoff_from: Optional[str] = None,
        handoff_to: Optional[str] = None,
        status_code: int = 200,
        error_detail: Optional[str] = None,
        framework: Optional[str] = None,
        thread_id: Optional[str] = None,
        step_index: Optional[int] = None,
        turn_index: Optional[int] = None,
        prev_action_id: Optional[str] = None,
        run_id: Optional[str] = None,
        iteration: Optional[int] = None,
        loop_flags: Optional[str] = None,
    ) -> None: ...

    @abstractmethod
    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        """Aggregate calls by run_id, newest first. Inferred one-iteration
        runs (auto-run-*) are hidden until a second iteration confirms a loop."""
        ...

    @abstractmethod
    async def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        """Aggregate one run, including promise_seen (completion-promise flag
        observed on any call). None when the run has no calls."""
        ...

    @abstractmethod
    async def get_run_iterations(self, run_id: str) -> list[dict[str, Any]]:
        """Per-iteration aggregates for one run, in iteration order."""
        ...

    @abstractmethod
    async def get_flagged_calls(self, run_id: str) -> list[dict[str, Any]]:
        """Calls in a run that carry loop_flags, oldest first — the drill-down
        behind the Loop Lens flagged-calls count."""
        ...

    @abstractmethod
    async def save_tool_executions(self, executions: list[dict[str, Any]]) -> None:
        """Persist derived tool-execution records (paired tool call → result)."""
        ...

    @abstractmethod
    async def get_tool_executions(self, session_id: str) -> list[dict[str, Any]]:
        """Tool executions for a session, in execution order."""
        ...

    @abstractmethod
    async def get(self, action_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    async def get_session(self, session_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_session_cost(self, session_id: str) -> float: ...

    @abstractmethod
    async def get_agent_cost(self, agent_name: str, since_ts: float) -> float: ...

    @abstractmethod
    async def get_period_cost(self, since_ts: float) -> float: ...

    @abstractmethod
    async def delete_session(self, session_id: str) -> int:
        """Delete all calls for a session. Returns number of rows deleted."""
        ...

    @abstractmethod
    async def ping(self) -> None:
        """Raise if the backend is not reachable. Used by the readiness probe."""
        ...

    # ── API tokens (timestamps are unix seconds) ─────────────────────────────

    @abstractmethod
    async def create_token(
        self, token_id: str, name: str, token_hash: str, role: str,
        created_at: float, expires_at: Optional[float],
    ) -> None: ...

    @abstractmethod
    async def get_token_by_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        """Return the token row for a hash, or None. Includes revoked_at/expires_at."""
        ...

    @abstractmethod
    async def list_tokens(self) -> list[dict[str, Any]]:
        """List tokens (metadata only — never the hash), newest first."""
        ...

    @abstractmethod
    async def revoke_token(self, token_id: str, revoked_at: float) -> int:
        """Mark a token revoked. Returns the number of rows updated (0 if unknown)."""
        ...

    @abstractmethod
    async def purge_older_than(self, cutoff_ts: float) -> int:
        """Delete calls older than cutoff_ts (unix seconds). Returns rows deleted."""
        ...

    @abstractmethod
    async def delete_user(self, user_id: str) -> int:
        """Right-to-erasure: delete all calls for a user_id. Returns rows deleted."""
        ...

    # ── Audit log ────────────────────────────────────────────────────────────

    @abstractmethod
    async def add_audit(self, entry: dict[str, Any]) -> None:
        """Append an audit entry (who did what to which target, when)."""
        ...

    @abstractmethod
    async def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent audit entries, newest first."""
        ...

    @abstractmethod
    async def close(self) -> None: ...


# ── SQLite ───────────────────────────────────────────────────────────────────

class _SqliteStore(Store):

    def __init__(self, db) -> None:
        self._db = db

    @classmethod
    async def _connect(cls, dsn: str) -> "_SqliteStore":
        import aiosqlite
        path = dsn.split("sqlite:///", 1)[1] or ":memory:"
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        await db.execute("""
            CREATE TABLE IF NOT EXISTS llm_calls (
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
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS llm_calls_session_idx
            ON llm_calls (session_id) WHERE session_id IS NOT NULL
        """)
        for col, col_type in _MIGRATION_COLUMNS:
            # Column already exists on an upgraded DB — that's fine, skip it.
            with contextlib.suppress(Exception):
                await db.execute(f"ALTER TABLE llm_calls ADD COLUMN {col} {col_type}")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS api_tokens (
                token_id   TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                role       TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL,
                revoked_at REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id           TEXT PRIMARY KEY,
                timestamp    REAL NOT NULL,
                actor_role   TEXT,
                actor_source TEXT,
                actor        TEXT,
                action       TEXT NOT NULL,
                target       TEXT,
                details      TEXT,
                client       TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tool_executions (
                tool_call_id          TEXT,
                tool_name             TEXT,
                arguments             TEXT,
                issued_by_action_id   TEXT,
                resolved_by_action_id TEXT,
                session_id            TEXT,
                thread_id             TEXT,
                latency_ms            INTEGER,
                is_error              INTEGER,
                timestamp             REAL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS tool_executions_session_idx
            ON tool_executions (session_id) WHERE session_id IS NOT NULL
        """)
        await db.commit()
        return cls(db)

    async def save(self, action_id, req, resp, *, session_id=None, user_id=None,
                   agent_name=None, app_id=None, parent_action_id=None,
                   environment="development", handoff_from=None, handoff_to=None,
                   status_code=200, error_detail=None, framework=None,
                   thread_id=None, step_index=None, turn_index=None,
                   prev_action_id=None, run_id=None, iteration=None,
                   loop_flags=None) -> None:
        await self._db.execute(
            """
            INSERT INTO llm_calls
                (action_id, session_id, timestamp, model_id, provider,
                 messages, tools, content, tool_calls, stop_reason,
                 tokens_in, tokens_out, latency_ms,
                 user_id, agent_name, app_id, parent_action_id, environment,
                 system_prompt, temperature, max_tokens,
                 tool_results, cost_usd, handoff_from, handoff_to,
                 status_code, error_detail,
                 cache_read_tokens, cache_write_tokens, thinking, framework,
                 thread_id, step_index, turn_index, prev_action_id,
                 run_id, iteration, loop_flags)
            VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?,?, ?,?, ?,?,?,?, ?,?,?,?, ?,?,?)
            """,
            (
                action_id, session_id, req.timestamp, req.model_id, req.provider,
                json.dumps(req.messages),
                json.dumps(req.tools) if req.tools is not None else None,
                resp.content,
                json.dumps(resp.tool_calls) if resp.tool_calls is not None else None,
                resp.stop_reason, resp.tokens_in, resp.tokens_out, round(resp.latency_ms),
                user_id, agent_name, app_id, parent_action_id, environment,
                req.system_prompt, req.temperature, req.max_tokens,
                json.dumps(req.tool_results) if req.tool_results is not None else None,
                resp.cost_usd, handoff_from, handoff_to,
                status_code, error_detail,
                resp.cache_read_tokens, resp.cache_write_tokens, resp.thinking,
                framework,
                thread_id, step_index, turn_index, prev_action_id,
                run_id, iteration, loop_flags,
            ),
        )
        await self._db.commit()

    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._db.execute(
            f"""
            SELECT {_RUN_AGGREGATE_COLUMNS}
            FROM llm_calls
            WHERE run_id IS NOT NULL
            GROUP BY run_id
            HAVING run_id NOT LIKE 'auto-run-%' OR MAX(iteration) >= 2
            ORDER BY MAX(timestamp) DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [_sqlite_run_row(r) for r in rows]

    async def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        async with self._db.execute(
            f"""
            SELECT {_RUN_AGGREGATE_COLUMNS}
            FROM llm_calls
            WHERE run_id = ?
            GROUP BY run_id
            """,
            (run_id,),
        ) as cur:
            row = await cur.fetchone()
        return _sqlite_run_row(row) if row else None

    async def get_run_iterations(self, run_id: str) -> list[dict[str, Any]]:
        async with self._db.execute(
            f"""
            SELECT {_ITERATION_AGGREGATE_COLUMNS}
            FROM llm_calls
            WHERE run_id = ?
            GROUP BY iteration
            ORDER BY iteration ASC
            """,
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_iteration_row(dict(r)) for r in rows]

    async def get_flagged_calls(self, run_id: str) -> list[dict[str, Any]]:
        async with self._db.execute(
            f"SELECT {_FLAGGED_CALL_COLUMNS} FROM llm_calls "
            "WHERE run_id = ? AND loop_flags IS NOT NULL ORDER BY timestamp ASC",
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_flagged_row(dict(r)) for r in rows]

    async def save_tool_executions(self, executions: list[dict[str, Any]]) -> None:
        if not executions:
            return
        await self._db.executemany(
            """
            INSERT INTO tool_executions
                (tool_call_id, tool_name, arguments, issued_by_action_id,
                 resolved_by_action_id, session_id, thread_id,
                 latency_ms, is_error, timestamp)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    e.get("tool_call_id"), e.get("tool_name"),
                    _as_json_text(e.get("arguments")),
                    e.get("issued_by_action_id"), e.get("resolved_by_action_id"),
                    e.get("session_id"), e.get("thread_id"),
                    e.get("latency_ms"),
                    None if e.get("is_error") is None else int(bool(e.get("is_error"))),
                    e.get("timestamp"),
                )
                for e in executions
            ],
        )
        await self._db.commit()

    async def get_tool_executions(self, session_id: str) -> list[dict[str, Any]]:
        async with self._db.execute(
            "SELECT * FROM tool_executions WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get(self, action_id: str) -> Optional[dict[str, Any]]:
        async with self._db.execute(
            "SELECT * FROM llm_calls WHERE action_id = ?", (action_id,)
        ) as cur:
            row = await cur.fetchone()
        return _sqlite_row(row) if row else None

    async def get_session(self, session_id: str) -> list[dict[str, Any]]:
        async with self._db.execute(
            "SELECT * FROM llm_calls WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_sqlite_row(r) for r in rows]

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._db.execute(
            """
            SELECT
                session_id,
                COUNT(*)         AS call_count,
                MIN(timestamp)   AS started_at,
                SUM(latency_ms)  AS total_latency_ms,
                SUM(tokens_in)   AS total_tokens_in,
                SUM(tokens_out)  AS total_tokens_out,
                SUM(cost_usd)    AS total_cost_usd,
                MAX(model_id)    AS model_id,
                MAX(agent_name)  AS agent_name,
                MAX(user_id)     AS user_id,
                MAX(environment) AS environment
            FROM llm_calls
            WHERE session_id IS NOT NULL
            GROUP BY session_id
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [_sqlite_session_row(r) for r in rows]

    async def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        async with self._db.execute(
            """
            SELECT * FROM llm_calls
            WHERE messages LIKE ? OR content LIKE ? OR system_prompt LIKE ?
               OR agent_name LIKE ? OR user_id LIKE ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, pattern, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_sqlite_row(r) for r in rows]

    async def get_session_cost(self, session_id: str) -> float:
        async with self._db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE session_id = ? AND status_code = 200",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def get_agent_cost(self, agent_name: str, since_ts: float) -> float:
        async with self._db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE agent_name = ? AND timestamp >= ? AND status_code = 200",
            (agent_name, since_ts),
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def get_period_cost(self, since_ts: float) -> float:
        async with self._db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE timestamp >= ? AND status_code = 200",
            (since_ts,),
        ) as cur:
            row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def delete_session(self, session_id: str) -> int:
        async with self._db.execute(
            "DELETE FROM llm_calls WHERE session_id = ?", (session_id,)
        ) as cur:
            deleted = cur.rowcount
        await self._db.commit()
        return deleted

    async def ping(self) -> None:
        await self._db.execute("SELECT 1")

    async def create_token(self, token_id, name, token_hash, role, created_at, expires_at) -> None:
        await self._db.execute(
            "INSERT INTO api_tokens (token_id, name, token_hash, role, created_at, expires_at) "
            "VALUES (?,?,?,?,?,?)",
            (token_id, name, token_hash, role, created_at, expires_at),
        )
        await self._db.commit()

    async def get_token_by_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        async with self._db.execute(
            "SELECT * FROM api_tokens WHERE token_hash = ?", (token_hash,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def list_tokens(self) -> list[dict[str, Any]]:
        async with self._db.execute(
            "SELECT token_id, name, role, created_at, expires_at, revoked_at "
            "FROM api_tokens ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def revoke_token(self, token_id: str, revoked_at: float) -> int:
        async with self._db.execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL",
            (revoked_at, token_id),
        ) as cur:
            updated = cur.rowcount
        await self._db.commit()
        return updated

    async def purge_older_than(self, cutoff_ts: float) -> int:
        async with self._db.execute(
            "DELETE FROM llm_calls WHERE timestamp < ?", (cutoff_ts,)
        ) as cur:
            deleted = cur.rowcount
        await self._db.commit()
        return deleted

    async def delete_user(self, user_id: str) -> int:
        async with self._db.execute(
            "DELETE FROM llm_calls WHERE user_id = ?", (user_id,)
        ) as cur:
            deleted = cur.rowcount
        await self._db.commit()
        return deleted

    async def add_audit(self, entry: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO audit_log "
            "(id, timestamp, actor_role, actor_source, actor, action, target, details, client) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (entry["id"], entry["timestamp"], entry.get("actor_role"), entry.get("actor_source"),
             entry.get("actor"), entry["action"], entry.get("target"), entry.get("details"),
             entry.get("client")),
        )
        await self._db.commit()

    async def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._db.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["timestamp"] = _unix_to_iso(d["timestamp"])
            out.append(d)
        return out

    async def close(self) -> None:
        await self._db.close()


def _sqlite_row(row) -> dict[str, Any]:
    d = dict(row)
    for field in ("messages", "tools", "tool_calls", "tool_results"):
        if d.get(field):
            d[field] = json.loads(d[field])
    d["timestamp"] = _unix_to_iso(d["timestamp"])
    return d


def _sqlite_session_row(row) -> dict[str, Any]:
    d = dict(row)
    d["started_at"] = _unix_to_iso(d["started_at"])
    return d


def _as_json_text(value) -> Optional[str]:
    """Tool arguments arrive as a JSON string (OpenAI) or a dict (Anthropic
    tool_use input) — store one canonical text form."""
    if value is None or isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except Exception:
        return str(value)


def _sqlite_run_row(row) -> dict[str, Any]:
    d = dict(row)
    d["started_at"] = _unix_to_iso(d["started_at"])
    d["last_call_at"] = _unix_to_iso(d["last_call_at"])
    return d


def _unix_to_iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(
        ts, tz=datetime.timezone.utc
    ).isoformat()


# ── Postgres ─────────────────────────────────────────────────────────────────

class _PostgresStore(Store):

    def __init__(self, pool) -> None:
        self._pool = pool

    @classmethod
    async def _connect(cls, dsn: str) -> "_PostgresStore":
        import asyncpg
        pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_calls (
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
                    latency_ms  INTEGER
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS llm_calls_session_idx
                ON llm_calls (session_id) WHERE session_id IS NOT NULL
            """)
            for col, col_type in _MIGRATION_COLUMNS:
                pg_type = "JSONB" if col in ("tool_results",) else col_type
                await conn.execute(
                    f"ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS {col} {pg_type}"
                )
            # Migrate legacy databases where session_id was a UUID column. Agent
            # session ids are arbitrary strings (e.g. "auto-2026-01-01" or a
            # human-readable run name), not UUIDs — a UUID column silently rejected
            # every non-UUID id. Convert in place; the guard avoids a needless table
            # rewrite on already-migrated databases.
            session_type = await conn.fetchval(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'llm_calls' AND column_name = 'session_id'"
            )
            if session_type == "uuid":
                await conn.execute(
                    "ALTER TABLE llm_calls ALTER COLUMN session_id TYPE TEXT "
                    "USING session_id::text"
                )
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS api_tokens (
                    token_id   TEXT PRIMARY KEY,
                    name       TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    role       TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    expires_at DOUBLE PRECISION,
                    revoked_at DOUBLE PRECISION
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id           TEXT PRIMARY KEY,
                    timestamp    DOUBLE PRECISION NOT NULL,
                    actor_role   TEXT,
                    actor_source TEXT,
                    actor        TEXT,
                    action       TEXT NOT NULL,
                    target       TEXT,
                    details      TEXT,
                    client       TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_executions (
                    tool_call_id          TEXT,
                    tool_name             TEXT,
                    arguments             TEXT,
                    issued_by_action_id   TEXT,
                    resolved_by_action_id TEXT,
                    session_id            TEXT,
                    thread_id             TEXT,
                    latency_ms            INTEGER,
                    is_error              INTEGER,
                    timestamp             DOUBLE PRECISION
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS tool_executions_session_idx
                ON tool_executions (session_id) WHERE session_id IS NOT NULL
            """)
        return cls(pool)

    async def save(self, action_id, req, resp, *, session_id=None, user_id=None,
                   agent_name=None, app_id=None, parent_action_id=None,
                   environment="development", handoff_from=None, handoff_to=None,
                   status_code=200, error_detail=None, framework=None,
                   thread_id=None, step_index=None, turn_index=None,
                   prev_action_id=None, run_id=None, iteration=None,
                   loop_flags=None) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO llm_calls
                    (action_id, session_id, timestamp, model_id, provider,
                     messages, tools, content, tool_calls, stop_reason,
                     tokens_in, tokens_out, latency_ms,
                     user_id, agent_name, app_id, parent_action_id, environment,
                     system_prompt, temperature, max_tokens,
                     tool_results, cost_usd, handoff_from, handoff_to,
                     status_code, error_detail,
                     cache_read_tokens, cache_write_tokens, thinking, framework,
                     thread_id, step_index, turn_index, prev_action_id,
                     run_id, iteration, loop_flags)
                VALUES
                    ($1,$2,to_timestamp($3),$4,$5,
                     $6::jsonb,$7::jsonb,$8,$9::jsonb,$10,
                     $11,$12,$13,
                     $14,$15,$16,$17,$18,
                     $19,$20,$21,
                     $22::jsonb,$23,$24,$25,
                     $26,$27,
                     $28,$29,$30,$31,
                     $32,$33,$34,$35,
                     $36,$37,$38)
                """,
                uuid.UUID(action_id),
                session_id,
                req.timestamp, req.model_id, req.provider,
                json.dumps(req.messages),
                json.dumps(req.tools) if req.tools is not None else None,
                resp.content,
                json.dumps(resp.tool_calls) if resp.tool_calls is not None else None,
                resp.stop_reason, resp.tokens_in, resp.tokens_out, round(resp.latency_ms),
                user_id, agent_name, app_id, parent_action_id, environment,
                req.system_prompt, req.temperature, req.max_tokens,
                json.dumps(req.tool_results) if req.tool_results is not None else None,
                resp.cost_usd, handoff_from, handoff_to,
                status_code, error_detail,
                resp.cache_read_tokens, resp.cache_write_tokens, resp.thinking,
                framework,
                thread_id, step_index, turn_index, prev_action_id,
                run_id, iteration, loop_flags,
            )

    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_RUN_AGGREGATE_COLUMNS}
                FROM llm_calls
                WHERE run_id IS NOT NULL
                GROUP BY run_id
                HAVING run_id NOT LIKE 'auto-run-%' OR MAX(iteration) >= 2
                ORDER BY MAX(timestamp) DESC
                LIMIT $1
                """,
                limit,
            )
        return [_pg_run_row(r) for r in rows]

    async def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT {_RUN_AGGREGATE_COLUMNS}
                FROM llm_calls
                WHERE run_id = $1
                GROUP BY run_id
                """,
                run_id,
            )
        return _pg_run_row(row) if row else None

    async def get_run_iterations(self, run_id: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT {_ITERATION_AGGREGATE_COLUMNS}
                FROM llm_calls
                WHERE run_id = $1
                GROUP BY iteration
                ORDER BY iteration ASC
                """,
                run_id,
            )
        return [_iteration_row(dict(r)) for r in rows]

    async def get_flagged_calls(self, run_id: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_FLAGGED_CALL_COLUMNS} FROM llm_calls "
                "WHERE run_id = $1 AND loop_flags IS NOT NULL ORDER BY timestamp ASC",
                run_id,
            )
        return [_flagged_row(dict(r)) for r in rows]

    async def save_tool_executions(self, executions: list[dict[str, Any]]) -> None:
        if not executions:
            return
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO tool_executions
                    (tool_call_id, tool_name, arguments, issued_by_action_id,
                     resolved_by_action_id, session_id, thread_id,
                     latency_ms, is_error, timestamp)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                [
                    (
                        e.get("tool_call_id"), e.get("tool_name"),
                        _as_json_text(e.get("arguments")),
                        e.get("issued_by_action_id"), e.get("resolved_by_action_id"),
                        e.get("session_id"), e.get("thread_id"),
                        e.get("latency_ms"),
                        None if e.get("is_error") is None else int(bool(e.get("is_error"))),
                        e.get("timestamp"),
                    )
                    for e in executions
                ],
            )

    async def get_tool_executions(self, session_id: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM tool_executions WHERE session_id = $1 ORDER BY timestamp ASC",
                session_id,
            )
        return [dict(r) for r in rows]

    async def get(self, action_id: str) -> Optional[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM llm_calls WHERE action_id = $1", uuid.UUID(action_id)
            )
        return _pg_row(row) if row else None

    async def get_session(self, session_id: str) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM llm_calls WHERE session_id = $1 ORDER BY timestamp ASC",
                session_id,
            )
        return [_pg_row(r) for r in rows]

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    session_id::text,
                    COUNT(*)                    AS call_count,
                    MIN(timestamp)              AS started_at,
                    SUM(latency_ms)             AS total_latency_ms,
                    SUM(tokens_in)              AS total_tokens_in,
                    SUM(tokens_out)             AS total_tokens_out,
                    SUM(cost_usd)               AS total_cost_usd,
                    MAX(model_id)               AS model_id,
                    MAX(agent_name)             AS agent_name,
                    MAX(user_id)                AS user_id,
                    MAX(environment)            AS environment
                FROM llm_calls
                WHERE session_id IS NOT NULL
                GROUP BY session_id
                ORDER BY started_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [_pg_session_row(r) for r in rows]

    async def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM llm_calls
                WHERE messages::text ILIKE $1 OR content ILIKE $1
                   OR system_prompt ILIKE $1 OR agent_name ILIKE $1 OR user_id ILIKE $1
                ORDER BY timestamp DESC LIMIT $2
                """,
                pattern, limit,
            )
        return [_pg_row(r) for r in rows]

    async def get_session_cost(self, session_id: str) -> float:
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE session_id = $1 AND status_code = 200",
                session_id,
            )
        return float(val or 0)

    async def get_agent_cost(self, agent_name: str, since_ts: float) -> float:
        import datetime as _dt
        since = _dt.datetime.fromtimestamp(since_ts, tz=_dt.timezone.utc)
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE agent_name = $1 AND timestamp >= $2 AND status_code = 200",
                agent_name, since,
            )
        return float(val or 0)

    async def get_period_cost(self, since_ts: float) -> float:
        import datetime as _dt
        since = _dt.datetime.fromtimestamp(since_ts, tz=_dt.timezone.utc)
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE timestamp >= $1 AND status_code = 200",
                since,
            )
        return float(val or 0)

    async def delete_session(self, session_id: str) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM llm_calls WHERE session_id = $1", session_id
            )
        return int(result.split()[-1])  # "DELETE N"

    async def ping(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    async def create_token(self, token_id, name, token_hash, role, created_at, expires_at) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO api_tokens (token_id, name, token_hash, role, created_at, expires_at) "
                "VALUES ($1,$2,$3,$4,$5,$6)",
                token_id, name, token_hash, role, created_at, expires_at,
            )

    async def get_token_by_hash(self, token_hash: str) -> Optional[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM api_tokens WHERE token_hash = $1", token_hash
            )
        return dict(row) if row else None

    async def list_tokens(self) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT token_id, name, role, created_at, expires_at, revoked_at "
                "FROM api_tokens ORDER BY created_at DESC"
            )
        return [dict(r) for r in rows]

    async def revoke_token(self, token_id: str, revoked_at: float) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE api_tokens SET revoked_at = $1 WHERE token_id = $2 AND revoked_at IS NULL",
                revoked_at, token_id,
            )
        return int(result.split()[-1])  # "UPDATE N"

    async def purge_older_than(self, cutoff_ts: float) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM llm_calls WHERE timestamp < to_timestamp($1)", cutoff_ts
            )
        return int(result.split()[-1])  # "DELETE N"

    async def delete_user(self, user_id: str) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM llm_calls WHERE user_id = $1", user_id
            )
        return int(result.split()[-1])  # "DELETE N"

    async def add_audit(self, entry: dict[str, Any]) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO audit_log "
                "(id, timestamp, actor_role, actor_source, actor, action, target, details, client) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                entry["id"], entry["timestamp"], entry.get("actor_role"), entry.get("actor_source"),
                entry.get("actor"), entry["action"], entry.get("target"), entry.get("details"),
                entry.get("client"),
            )

    async def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT $1", limit
            )
        out = []
        for r in rows:
            d = dict(r)
            d["timestamp"] = _unix_to_iso(d["timestamp"])
            out.append(d)
        return out

    async def close(self) -> None:
        await self._pool.close()


def _pg_row(row) -> dict[str, Any]:
    d = dict(row)
    d["action_id"] = str(d["action_id"])
    d["session_id"] = str(d["session_id"]) if d["session_id"] else None
    d["parent_action_id"] = str(d["parent_action_id"]) if d.get("parent_action_id") else None
    d["timestamp"] = d["timestamp"].isoformat()
    return d


def _pg_session_row(row) -> dict[str, Any]:
    d = dict(row)
    d["started_at"] = d["started_at"].isoformat()
    return d


def _pg_run_row(row) -> dict[str, Any]:
    d = dict(row)
    d["started_at"] = d["started_at"].isoformat()
    d["last_call_at"] = d["last_call_at"].isoformat()
    d["total_cost_usd"] = float(d["total_cost_usd"] or 0)
    return d
