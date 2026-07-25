"""
Agentic Ledger proxy — sits between the agent and LiteLLM (or any OpenAI-compatible
upstream).

Intercepts POST /v1/chat/completions and POST /v1/messages, assigns an action_id,
normalizes to canonical schema, stores to SQLite or Postgres, then returns the
upstream response unmodified — including full streaming support.

Caller-supplied headers (all optional):
    x-agentledger-session-id       Group calls into a run
    x-agentledger-user-id          End user who triggered this
    x-agentledger-agent-name       Which agent made this call
    x-agentledger-app-id           Which application
    x-agentledger-parent-action-id Parent in the call graph
    x-agentledger-environment      prod / staging / development (default)
    x-agentledger-handoff-from     Agent handing off control
    x-agentledger-handoff-to       Agent receiving control

Endpoints:
    GET  /                             Dashboard (live via WebSocket)
    GET  /api/sessions                 List recent sessions
    GET  /api/search?q=...             Full-text search across calls
    GET  /explain/{action_id}          Single captured call
    GET  /session/{session_id}         All calls in a run, ordered by time
    GET  /export/{session_id}          JSON compliance export
    GET  /export/{session_id}/report   Printable HTML audit report
    WS   /ws                           Live event stream (new calls as they happen)
    POST /mcp                          MCP tool server

Or via CLI:
    python -m agentledger.proxy
"""

import asyncio
import datetime
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .alerts import AlertConfig, check_and_fire
from .auth import (
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_VIEWER,
    Principal,
    generate_token,
    hash_token,
    role_satisfies,
    valid_role,
)
from .dashboard import get_dashboard_html
from .export import build_export, render_html_report
from .mcp import handle_mcp
from .normalize import (
    CanonicalRequest,
    CanonicalResponse,
    normalize_request,
    normalize_response,
)
from .otel import emit_span
from .ratelimit import RateLimitConfig, RateLimiter
from .redact import Redactor, apply_capture_policy, normalize_capture_level
from .store import Store
from .stream import detect_stream_error, reconstruct_from_sse

logger = logging.getLogger(__name__)

_DEFAULT_LLM_PATHS = {"v1/chat/completions", "v1/messages", "v1/responses"}
_extra = os.getenv("AGENTLEDGER_EXTRA_PATHS", "")
_LLM_PATHS = _DEFAULT_LLM_PATHS | {p.strip() for p in _extra.split(",") if p.strip()}

_AL_HEADERS = {
    "x-agentledger-session-id",
    "x-agentledger-user-id",
    "x-agentledger-agent-name",
    "x-agentledger-app-id",
    "x-agentledger-parent-action-id",
    "x-agentledger-environment",
    "x-agentledger-handoff-from",
    "x-agentledger-handoff-to",
    "x-agentledger-ingest-key",
    "x-agentledger-api-key",
}


@dataclass
class _CaptureJob:
    """The post-call work for one captured request — persisted inline (sync mode)
    or off the hot path by the background worker (async mode)."""
    action_id: str
    req: CanonicalRequest
    resp: CanonicalResponse
    status_code: int
    error_detail: Optional[str]
    meta: dict
    budget_warning: Optional[str]


def _extract_token(carrier) -> Optional[str]:
    """Pull an API token from a request/websocket: Bearer header, x-agentledger-token, or ?token."""
    authz = carrier.headers.get("authorization") or ""
    if authz.lower().startswith("bearer "):
        return authz[7:].strip() or None
    return carrier.headers.get("x-agentledger-token") or carrier.query_params.get("token")


def _token_is_valid(row: dict) -> bool:
    """A token row is usable if it is not revoked, not expired, and has a known role."""
    if row.get("revoked_at"):
        return False
    expires_at = row.get("expires_at")
    if expires_at is not None and expires_at <= time.time():
        return False
    return valid_role(row.get("role", ""))


def _record_capture_drop(app: FastAPI, action_id: Optional[str]) -> None:
    """A call was served to the agent but could not be recorded. Never re-raise —
    observability must not break the proxy — but make the loss visible."""
    with suppress(Exception):
        app.state.capture_dropped += 1
    logger.warning(
        "Capture failed for action_id=%s — call was served upstream but not recorded",
        action_id, exc_info=True,
    )


class _Broadcaster:
    """Fanout to all connected WebSocket clients."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def broadcast(self, data: dict) -> None:
        dead: set[WebSocket] = set()
        for client in self._clients:
            try:
                await client.send_json(data)
            except Exception:
                dead.add(client)
        self._clients -= dead


def create_app(
    upstream_url: str,
    dsn: str,
    budget_session: Optional[float] = None,
    budget_agent: Optional[float] = None,
    budget_daily: Optional[float] = None,
    budget_action: str = "block",   # "block" | "warn" | "both"
    alert_config: Optional[AlertConfig] = None,
    rate_limit_config: Optional[RateLimitConfig] = None,
    async_capture: bool = False,
    capture_queue_max: int = 10_000,
    capture_level: str = "full",
    redactor: Optional[Redactor] = None,
    retention_days: Optional[float] = None,
    retention_interval_seconds: float = 3600.0,
    audit_enabled: bool = True,
) -> FastAPI:

    broadcaster = _Broadcaster()
    _rate_limiter = RateLimiter(rate_limit_config or RateLimitConfig())
    _alert_config = alert_config or AlertConfig(
        webhook_url=None, cost_per_call=None,
        latency_ms=None, error_rate=None, daily_spend=None,
    )
    # When async_capture is on, post-call persistence runs on a background worker so
    # it never adds latency to the agent's call — at the cost of read-after-write
    # (a just-captured call may not be queryable for a brief moment). Default off.
    _async_capture = async_capture
    _capture_queue: asyncio.Queue = asyncio.Queue(maxsize=capture_queue_max)
    # Data governance: capture level + optional redaction, applied to the stored copy
    # only (never to the response returned to the agent).
    _capture_level = normalize_capture_level(capture_level)
    _redactor = redactor
    # Retention: when set, a background worker periodically deletes calls older than
    # this many days. None = keep forever.
    _retention_days = retention_days
    _retention_interval = retention_interval_seconds
    _audit_enabled = audit_enabled

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store = await Store.connect(dsn)
        app.state.client = httpx.AsyncClient(
            base_url=upstream_url,
            timeout=httpx.Timeout(120.0),
        )
        app.state.broadcaster = broadcaster
        worker: Optional[asyncio.Task] = None
        if _async_capture:
            worker = asyncio.create_task(_capture_worker(app))
        retention_task: Optional[asyncio.Task] = None
        if _retention_days is not None:
            retention_task = asyncio.create_task(_retention_worker(app))
        yield
        if retention_task is not None:
            retention_task.cancel()
            with suppress(asyncio.CancelledError):
                await retention_task
        if worker is not None:
            # Flush pending captures, then stop the worker, before closing the store.
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(_capture_queue.join(), timeout=10.0)
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        await app.state.store.close()
        await app.state.client.aclose()

    app = FastAPI(title="Agentic Ledger Proxy", lifespan=lifespan)
    # Count calls whose capture failed (served to the agent but not recorded), so
    # silent data loss is observable instead of invisible. Surfaced via /readyz.
    app.state.capture_dropped = 0
    app.state.capture_persisted = 0

    async def _persist(job: _CaptureJob) -> None:
        """Do the post-call work for a captured request. The store write is the
        critical part (and counts the capture); span/broadcast/alerts are best-effort."""
        # Apply governance here so every sink (store, OTel span, dashboard) sees the
        # redacted/leveled copy. In async mode this runs off the request hot path.
        apply_capture_policy(job.req, job.resp, _capture_level, _redactor)
        store = app.state.store
        await store.save(
            job.action_id, job.req, job.resp,
            status_code=job.status_code, error_detail=job.error_detail, **job.meta,
        )
        app.state.capture_persisted += 1
        with suppress(Exception):
            emit_span(job.action_id, job.req, job.resp, status_code=job.status_code, **job.meta)
        with suppress(Exception):
            await broadcaster.broadcast({
                "type": "call",
                "action_id": job.action_id,
                "session_id": job.meta.get("session_id"),
                "status_code": job.status_code,
                "budget_warning": bool(job.budget_warning),
            })
        with suppress(Exception):
            await check_and_fire(
                _alert_config, store, job.resp, job.action_id,
                job.meta.get("session_id"), job.meta.get("agent_name"), job.status_code,
            )

    async def _capture_worker(app: FastAPI) -> None:
        """Drain the capture queue, persisting each job off the request hot path."""
        while True:
            job = await _capture_queue.get()
            try:
                await _persist(job)
            except Exception:
                _record_capture_drop(app, job.action_id)
            finally:
                _capture_queue.task_done()

    async def _retention_worker(app: FastAPI) -> None:
        """Periodically delete captured calls older than the retention window."""
        while True:
            try:
                cutoff = time.time() - _retention_days * 86400
                deleted = await app.state.store.purge_older_than(cutoff)
                if deleted:
                    logger.info(
                        "Retention: purged %d calls older than %s days", deleted, _retention_days
                    )
            except Exception:
                logger.warning("Retention purge failed", exc_info=True)
            await asyncio.sleep(_retention_interval)

    async def _capture(job: _CaptureJob) -> None:
        """Persist a captured call — enqueued (async mode) or inline (sync mode)."""
        if _async_capture:
            try:
                _capture_queue.put_nowait(job)
            except asyncio.QueueFull:
                # Shed load rather than block the agent's response; the drop is counted.
                _record_capture_drop(app, job.action_id)
        else:
            try:
                await _persist(job)
            except Exception:
                _record_capture_drop(app, job.action_id)

    _api_key = os.environ.get("AGENTLEDGER_API_KEY")
    # Optional proxy-ingest key. When set, the proxy refuses to forward a request
    # unless it carries a matching x-agentledger-ingest-key — closing the open relay.
    # When unset the proxy forwards anything (zero-config dev UX); __main__ warns loudly.
    _ingest_key = os.environ.get("AGENTLEDGER_INGEST_KEY")
    # Read/management endpoints enforce auth only when a master key is configured.
    # The master key grants admin (and is the bootstrap for minting tokens); API
    # tokens grant their own role. When unset, access is open (dev UX) and __main__ warns.
    _auth_enabled = bool(_api_key)

    async def _authenticate(carrier) -> Optional[Principal]:
        """Resolve a Principal from a request/websocket, or None if no valid credential."""
        supplied_key = carrier.headers.get("x-agentledger-api-key") or carrier.query_params.get("api_key")
        if _api_key and supplied_key and hmac.compare_digest(supplied_key, _api_key):
            return Principal(ROLE_ADMIN, "master")
        raw = _extract_token(carrier)
        if raw:
            row = await carrier.app.state.store.get_token_by_hash(hash_token(raw))
            if row and _token_is_valid(row):
                return Principal(row["role"], "token", row.get("token_id"), row.get("name"))
        return None

    async def _require(request: Request, role: str) -> Principal:
        """Enforce that the request carries a credential satisfying ``role``."""
        if not _auth_enabled:
            return Principal(ROLE_ADMIN, "open")
        principal = await _authenticate(request)
        if principal is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if not role_satisfies(principal.role, role):
            raise HTTPException(status_code=403, detail=f"Forbidden: requires '{role}' role")
        return principal

    async def _audit(
        principal: Optional[Principal], request: Request,
        action: str, target: Optional[str] = None, details: Optional[str] = None,
    ) -> None:
        """Record a sensitive access/mutation. Best-effort — never breaks the request."""
        if not _audit_enabled:
            return
        with suppress(Exception):
            await app.state.store.add_audit({
                "id": str(uuid.uuid4()),
                "timestamp": time.time(),
                "actor_role": principal.role if principal else None,
                "actor_source": principal.source if principal else "open",
                "actor": (principal.name or principal.token_id) if principal else None,
                "action": action,
                "target": target,
                "details": details,
                "client": request.client.host if request.client else None,
            })

    # ── Health ───────────────────────────────────────────────────────────────

    @app.get("/health")
    async def health() -> JSONResponse:
        """Liveness — the process is up. Always 200; does not touch the store."""
        try:
            from importlib.metadata import version as _v
            _version = _v("agentic-ledger")
        except Exception:
            _version = "unknown"
        return JSONResponse({"status": "ok", "version": _version})

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Readiness — the store is reachable. 503 when it isn't, so load balancers
        and k8s can stop routing traffic. Also surfaces the dropped-capture count."""
        store = getattr(app.state, "store", None)
        db_ok = False
        if store is not None:
            try:
                await store.ping()
                db_ok = True
            except Exception:
                logger.warning("Readiness check: store ping failed", exc_info=True)
        body = {
            "status": "ok" if db_ok else "unavailable",
            "store": "ok" if db_ok else "error",
            "capture_dropped": getattr(app.state, "capture_dropped", 0),
        }
        return JSONResponse(body, status_code=200 if db_ok else 503)

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus text-format metrics (low-cardinality; no per-session labels)."""
        persisted = getattr(app.state, "capture_persisted", 0)
        dropped = getattr(app.state, "capture_dropped", 0)
        depth = _capture_queue.qsize() if _async_capture else 0
        lines = [
            "# HELP agentledger_captures_persisted_total Calls successfully recorded to the store.",
            "# TYPE agentledger_captures_persisted_total counter",
            f"agentledger_captures_persisted_total {persisted}",
            "# HELP agentledger_captures_dropped_total Calls served but not recorded (error or queue overflow).",
            "# TYPE agentledger_captures_dropped_total counter",
            f"agentledger_captures_dropped_total {dropped}",
            "# HELP agentledger_capture_queue_depth Capture jobs awaiting persistence (async mode).",
            "# TYPE agentledger_capture_queue_depth gauge",
            f"agentledger_capture_queue_depth {depth}",
            "# HELP agentledger_capture_async Whether async capture is enabled (1) or not (0).",
            "# TYPE agentledger_capture_async gauge",
            f"agentledger_capture_async {1 if _async_capture else 0}",
        ]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # ── Dashboard ────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        await _require(request, ROLE_VIEWER)
        return HTMLResponse(get_dashboard_html())

    # ── WebSocket (live events) ───────────────────────────────────────────────

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await broadcaster.connect(websocket)
        try:
            while True:
                await websocket.receive_text()  # keep-alive; client sends pings
        except WebSocketDisconnect:
            broadcaster.disconnect(websocket)

    # ── API ──────────────────────────────────────────────────────────────────

    @app.get("/api/sessions")
    async def api_sessions(request: Request) -> JSONResponse:
        await _require(request, ROLE_VIEWER)
        sessions = await request.app.state.store.list_sessions()
        return JSONResponse(sessions)

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, request: Request) -> JSONResponse:
        principal = await _require(request, ROLE_EDITOR)
        deleted = await request.app.state.store.delete_session(session_id)
        if deleted == 0:
            raise HTTPException(status_code=404, detail="session_id not found")
        await _audit(principal, request, "delete_session", session_id, f"deleted {deleted} calls")
        return JSONResponse({"deleted": deleted})

    @app.delete("/api/users/{user_id}")
    async def erase_user(user_id: str, request: Request) -> JSONResponse:
        """Right-to-erasure: delete all captured calls for a user_id."""
        principal = await _require(request, ROLE_ADMIN)
        deleted = await request.app.state.store.delete_user(user_id)
        await _audit(principal, request, "erase_user", user_id, f"deleted {deleted} calls")
        return JSONResponse({"deleted": deleted})

    @app.get("/api/audit")
    async def get_audit(request: Request, limit: int = 100) -> JSONResponse:
        await _require(request, ROLE_ADMIN)
        entries = await request.app.state.store.list_audit(limit=max(1, min(limit, 1000)))
        return JSONResponse(entries)

    # ── API token management (admin only) ─────────────────────────────────────

    @app.post("/api/tokens")
    async def create_api_token(request: Request) -> JSONResponse:
        principal = await _require(request, ROLE_ADMIN)
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = (body.get("name") or "").strip()
        role = (body.get("role") or ROLE_VIEWER).strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        if not valid_role(role):
            raise HTTPException(status_code=400, detail=f"invalid role: {role!r} (viewer|editor|admin)")
        expires_in_days = body.get("expires_in_days")
        created_at = time.time()
        expires_at = created_at + float(expires_in_days) * 86400 if expires_in_days else None
        raw, token_hash = generate_token()
        token_id = str(uuid.uuid4())
        await request.app.state.store.create_token(
            token_id, name, token_hash, role, created_at, expires_at
        )
        await _audit(principal, request, "create_token", token_id, f"role={role} name={name}")
        # The raw token is returned exactly once; only its hash is stored.
        return JSONResponse({
            "token_id": token_id, "name": name, "role": role,
            "token": raw, "expires_at": expires_at,
            "note": "Store this token now — it is shown only once.",
        }, status_code=201)

    @app.get("/api/tokens")
    async def list_api_tokens(request: Request) -> JSONResponse:
        await _require(request, ROLE_ADMIN)
        return JSONResponse(await request.app.state.store.list_tokens())

    @app.delete("/api/tokens/{token_id}")
    async def revoke_api_token(token_id: str, request: Request) -> JSONResponse:
        principal = await _require(request, ROLE_ADMIN)
        revoked = await request.app.state.store.revoke_token(token_id, time.time())
        if not revoked:
            raise HTTPException(status_code=404, detail="token_id not found or already revoked")
        await _audit(principal, request, "revoke_token", token_id)
        return JSONResponse({"revoked": True})

    @app.get("/api/search")
    async def api_search(request: Request, q: str = "") -> JSONResponse:
        principal = await _require(request, ROLE_VIEWER)
        if not q.strip():
            return JSONResponse([])
        results = await request.app.state.store.search(q.strip())
        await _audit(principal, request, "search", q.strip()[:200])
        return JSONResponse(results)

    @app.get("/explain/{action_id}")
    async def explain(action_id: str, request: Request) -> JSONResponse:
        principal = await _require(request, ROLE_VIEWER)
        record = await request.app.state.store.get(action_id)
        if record is None:
            raise HTTPException(status_code=404, detail="action_id not found")
        await _audit(principal, request, "explain", action_id)
        return JSONResponse(record)

    @app.get("/session/{session_id}")
    async def session(session_id: str, request: Request) -> JSONResponse:
        principal = await _require(request, ROLE_VIEWER)
        records = await request.app.state.store.get_session(session_id)
        if not records:
            raise HTTPException(status_code=404, detail="session_id not found")
        await _audit(principal, request, "view_session", session_id)
        return JSONResponse(records)

    # ── Compliance export ─────────────────────────────────────────────────────

    @app.get("/export/{session_id}")
    async def export_json(session_id: str, request: Request) -> Response:
        principal = await _require(request, ROLE_VIEWER)
        calls = await request.app.state.store.get_session(session_id)
        if not calls:
            raise HTTPException(status_code=404, detail="session_id not found")
        await _audit(principal, request, "export_session", session_id)
        export = build_export(session_id, calls)
        filename = f"agentledger-{session_id[:16]}.json"
        return Response(
            content=json.dumps(export, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/export/{session_id}/report")
    async def export_report(session_id: str, request: Request) -> HTMLResponse:
        principal = await _require(request, ROLE_VIEWER)
        calls = await request.app.state.store.get_session(session_id)
        if not calls:
            raise HTTPException(status_code=404, detail="session_id not found")
        await _audit(principal, request, "export_report", session_id)
        export = build_export(session_id, calls)
        return HTMLResponse(render_html_report(export))

    # ── MCP ──────────────────────────────────────────────────────────────────

    @app.post("/mcp")
    async def mcp(request: Request) -> JSONResponse:
        await _require(request, ROLE_VIEWER)
        return await handle_mcp(request)

    # ── Transparent proxy ────────────────────────────────────────────────────

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy(request: Request, path: str) -> Response:
        # Proxy-ingest auth: gate forwarding behind a dedicated key when configured.
        if _ingest_key:
            supplied = request.headers.get("x-agentledger-ingest-key")
            if not supplied or not hmac.compare_digest(supplied, _ingest_key):
                return JSONResponse(
                    {"error": {
                        "type": "unauthorized",
                        "message": "Missing or invalid x-agentledger-ingest-key.",
                    }},
                    status_code=401,
                )

        body_bytes = await request.body()

        # Parse the request body exactly once — streaming detection, budget
        # capture, and normalization all need it, and coding-agent bodies can
        # be megabytes of context.
        body_json: Optional[dict] = None
        if request.method == "POST" and path in _LLM_PATHS and body_bytes:
            with suppress(Exception):
                parsed = json.loads(body_bytes)
                if isinstance(parsed, dict):
                    body_json = parsed

        is_llm_path = body_json is not None
        is_streaming = is_llm_path and bool(body_json.get("stream"))
        is_llm_call = is_llm_path and not is_streaming

        action_id = str(uuid.uuid4()) if is_llm_path else None
        meta = _extract_meta(request)

        # ── Rate limit check ─────────────────────────────────────────────────
        # Fail open: a rate-limiter error must never block the agent's LLM call.
        if is_llm_path:
            try:
                rate_error = _rate_limiter.check(
                    meta.get("session_id"), meta.get("agent_name"), meta.get("user_id")
                )
            except Exception:
                logger.warning("Rate limiter check failed — allowing call through", exc_info=True)
                rate_error = None
            if rate_error:
                return JSONResponse(
                    {"error": {"type": "rate_limit_exceeded", "message": rate_error}},
                    status_code=429,
                )

        # ── Budget check ─────────────────────────────────────────────────────
        # Fail open: if the store is unavailable the agent must not be blocked.
        # Budget enforcement resumes automatically once the store recovers.
        _budget_warning: Optional[str] = None  # set in warn mode; carried into actual save
        if is_llm_path and (budget_session is not None or budget_agent is not None or budget_daily is not None):
            try:
                budget_error = await _check_budgets(
                    request.app.state.store, meta,
                    budget_session, budget_agent, budget_daily,
                )
            except Exception:
                logger.warning("Budget check failed — allowing call through", exc_info=True)
                budget_error = None
            if budget_error:
                should_block = budget_action in ("block", "both")
                should_warn  = budget_action in ("warn",  "both")
                if should_block:
                    # Save blocked call with empty response, then reject
                    try:
                        canonical_req = normalize_request(body_json, path)
                        blocked_resp = _empty_response(0)
                        apply_capture_policy(canonical_req, blocked_resp, _capture_level, _redactor)
                        await request.app.state.store.save(
                            action_id, canonical_req, blocked_resp,
                            status_code=429, error_detail=budget_error, **meta,
                        )
                        await broadcaster.broadcast({
                            "type": "call",
                            "action_id": action_id,
                            "session_id": meta.get("session_id"),
                            "status_code": 429,
                            "budget_warning": False,
                        })
                    except Exception:
                        _record_capture_drop(request.app, action_id)
                    return JSONResponse(
                        {"error": {"type": "budget_exceeded", "message": budget_error}},
                        status_code=429,
                    )
                if should_warn:
                    # Let call through; tag the actual response on save
                    _budget_warning = budget_error
                    if _alert_config and _alert_config.webhook_url:
                        try:
                            from .alerts import _fire
                            await _fire(_alert_config.webhook_url, {
                                "type": "budget_exceeded",
                                "message": budget_error,
                                "action_id": action_id,
                                "session_id": meta.get("session_id"),
                                "agent_name": meta.get("agent_name"),
                            })
                        except Exception:
                            pass

        forward_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding")
            and k.lower() not in _AL_HEADERS
        }

        if is_streaming:
            return await _streaming_proxy(
                request, path, body_bytes, body_json, forward_headers, action_id,
                meta, _capture, _budget_warning,
            )

        start = time.monotonic()
        upstream_resp = await request.app.state.client.request(
            method=request.method,
            url=f"/{path}",
            content=body_bytes,
            headers=forward_headers,
            params=dict(request.query_params),
        )
        latency_ms = (time.monotonic() - start) * 1000

        if is_llm_call:
            try:
                canonical_req = normalize_request(body_json, path)
                status_code = upstream_resp.status_code
                if status_code == 200:
                    canonical_resp = normalize_response(
                        upstream_resp.json(), latency_ms, canonical_req.model_id
                    )
                    error_detail = f"budget_warning: {_budget_warning}" if _budget_warning else None
                else:
                    canonical_resp = _empty_response(latency_ms)
                    error_detail = _extract_error(upstream_resp)
                await _capture(_CaptureJob(
                    action_id, canonical_req, canonical_resp,
                    status_code, error_detail, meta, _budget_warning,
                ))
            except Exception:
                _record_capture_drop(request.app, action_id)

        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            headers=_response_headers(upstream_resp.headers, action_id, meta),
            media_type=upstream_resp.headers.get("content-type"),
        )

    return app


async def _streaming_proxy(
    request: Request,
    path: str,
    body_bytes: bytes,
    body_json: dict,
    forward_headers: dict,
    action_id: str,
    meta: dict,
    capture,
    budget_warning: Optional[str] = None,
) -> StreamingResponse:
    client: httpx.AsyncClient = request.app.state.client

    stream_ctx = client.stream(
        method=request.method,
        url=f"/{path}",
        content=body_bytes,
        headers=forward_headers,
        params=dict(request.query_params),
    )

    upstream = await stream_ctx.__aenter__()
    start = time.monotonic()

    canonical_req: Optional[CanonicalRequest] = None
    try:
        canonical_req = normalize_request(body_json, path)
    except Exception:
        canonical_req = None

    def _build_job(raw: bytes, completed: bool) -> _CaptureJob:
        latency_ms = (time.monotonic() - start) * 1000
        if upstream.status_code == 200:
            canonical_resp = reconstruct_from_sse(raw, latency_ms, canonical_req.model_id)
            parts: list[str] = []
            if budget_warning:
                parts.append(f"budget_warning: {budget_warning}")
            stream_err = detect_stream_error(raw)
            if stream_err:
                parts.append(f"stream_error: {stream_err}")
            if not completed:
                parts.append("partial: client disconnected before stream completed")
            return _CaptureJob(
                action_id, canonical_req, canonical_resp, 200,
                "; ".join(parts) or None, meta, budget_warning,
            )
        detail = (
            raw.decode("utf-8", errors="replace")[:300]
            or f"upstream returned {upstream.status_code}"
        )
        return _CaptureJob(
            action_id, canonical_req, _empty_response(latency_ms),
            upstream.status_code, detail, meta, budget_warning,
        )

    async def generator() -> AsyncIterator[bytes]:
        chunks: list[bytes] = []
        captured = False
        try:
            async for chunk in upstream.aiter_bytes():
                if canonical_req is not None:
                    chunks.append(chunk)
                yield chunk

            # Normal completion: capture inline, so the record exists by the
            # time the client sees the stream end. Errored upstreams (non-200)
            # are captured too, with the buffered error body as detail.
            if canonical_req is not None:
                captured = True
                try:
                    await capture(_build_job(b"".join(chunks), completed=True))
                except Exception:
                    _record_capture_drop(request.app, action_id)
        finally:
            # Abnormal teardown — client disconnect or cancellation — means the
            # inline capture above never ran. Schedule it on a task instead of
            # awaiting: an await here can be interrupted by the very
            # cancellation that tore the stream down, losing the record.
            if canonical_req is not None and not captured:
                try:
                    _spawn_capture(
                        request.app, capture,
                        _build_job(b"".join(chunks), completed=False), action_id,
                    )
                except Exception:
                    _record_capture_drop(request.app, action_id)
            await stream_ctx.__aexit__(None, None, None)

    return StreamingResponse(
        generator(),
        status_code=upstream.status_code,
        headers=_response_headers(upstream.headers, action_id, meta),
        media_type=upstream.headers.get("content-type"),
    )


async def _check_budgets(
    store: Store,
    meta: dict,
    budget_session: Optional[float],
    budget_agent: Optional[float],
    budget_daily: Optional[float],
) -> Optional[str]:
    """Return an error message if any budget is exceeded, else None."""
    session_id = meta.get("session_id")
    agent_name = meta.get("agent_name")

    if budget_session is not None and session_id:
        spent = await store.get_session_cost(session_id)
        if spent >= budget_session:
            return (
                f"Session budget of ${budget_session:.4f} exceeded "
                f"(current spend: ${spent:.4f}). Session: {session_id}"
            )

    if budget_agent is not None and agent_name:
        since = _today_start_ts()
        spent = await store.get_agent_cost(agent_name, since)
        if spent >= budget_agent:
            return (
                f"Agent daily budget of ${budget_agent:.4f} exceeded "
                f"(current spend: ${spent:.4f}). Agent: {agent_name}"
            )

    if budget_daily is not None:
        since = _today_start_ts()
        spent = await store.get_period_cost(since)
        if spent >= budget_daily:
            return (
                f"Daily budget of ${budget_daily:.4f} exceeded "
                f"(current spend: ${spent:.4f})."
            )

    return None


def _today_start_ts() -> float:
    today = datetime.datetime.now(tz=datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return today.timestamp()


def _empty_response(latency_ms: float) -> CanonicalResponse:
    return CanonicalResponse(
        content=None, tool_calls=None, stop_reason=None,
        tokens_in=None, tokens_out=None, latency_ms=latency_ms,
    )


def _extract_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error", {})
        if isinstance(err, dict):
            return err.get("message") or resp.text[:300]
        return str(err)[:300]
    except Exception:
        return resp.text[:300]


def _extract_meta(request: Request) -> dict:
    import datetime
    h = request.headers
    session_id = h.get("x-agentledger-session-id") or f"auto-{datetime.date.today().isoformat()}"
    return {
        "session_id":       session_id,
        "user_id":          h.get("x-agentledger-user-id"),
        "agent_name":       h.get("x-agentledger-agent-name"),
        "app_id":           h.get("x-agentledger-app-id"),
        "parent_action_id": h.get("x-agentledger-parent-action-id"),
        "environment":      h.get("x-agentledger-environment", "development"),
        "handoff_from":     h.get("x-agentledger-handoff-from"),
        "handoff_to":       h.get("x-agentledger-handoff-to"),
    }


def _response_headers(
    upstream_headers: httpx.Headers,
    action_id: str | None,
    meta: dict,
) -> dict:
    # httpx auto-decompresses responses, so strip content-encoding to prevent
    # the client from trying to decompress already-decompressed content.
    headers = {
        k: v for k, v in upstream_headers.items()
        if k.lower() not in ("content-encoding", "transfer-encoding")
    }
    if action_id:
        headers["x-agentledger-action-id"] = action_id
    if meta.get("session_id"):
        headers["x-agentledger-session-id"] = meta["session_id"]
    return headers


_BG_CAPTURE_TASKS: set[asyncio.Task] = set()


def _spawn_capture(app: FastAPI, capture, job: _CaptureJob, action_id: Optional[str]) -> None:
    """Schedule a capture without awaiting it. The task set holds strong
    references until each task completes, so the event loop can't GC them."""
    async def _run() -> None:
        try:
            await capture(job)
        except Exception:
            _record_capture_drop(app, action_id)

    task = asyncio.get_running_loop().create_task(_run())
    _BG_CAPTURE_TASKS.add(task)
    task.add_done_callback(_BG_CAPTURE_TASKS.discard)
