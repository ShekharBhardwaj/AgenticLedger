"""
Agentic Ledger proxy — sits between the agent and LiteLLM (or any OpenAI-compatible
upstream).

Intercepts POST /v1/chat/completions and POST /v1/messages, assigns an action_id,
normalizes to canonical schema, stores to SQLite or Postgres, then returns the
upstream response unmodified — including full streaming support.

Caller-supplied headers (all optional):
    x-agenticledger-session-id       Group calls into a run
    x-agenticledger-user-id          End user who triggered this
    x-agenticledger-agent-name       Which agent made this call
    x-agenticledger-app-id           Which application
    x-agenticledger-parent-action-id Parent in the call graph
    x-agenticledger-environment      prod / staging / development (default)
    x-agenticledger-handoff-from     Agent handing off control
    x-agenticledger-handoff-to       Agent receiving control
    x-agenticledger-framework        Framework making the call (else fingerprinted)
    x-agenticledger-run-id           Loop run grouping (else inferred for fresh-context loops)
    x-agenticledger-iteration        Iteration within the run

Endpoints:
    GET  /                             Dashboard (live via WebSocket)
    GET  /api/sessions                 List recent sessions
    GET  /api/runs                     List loop runs (explicit or inferred)
    GET  /api/reports?days=N           Spend insights: daily trend, model mix, cache savings
    GET  /api/search?q=...             Full-text search across calls
    POST /v1/traces                    OTLP/HTTP ingest (JSON + protobuf) — GenAI spans
    GET  /explain/{action_id}          Single captured call
    GET  /session/{session_id}         All calls in a run, ordered by time
    GET  /export/{session_id}          JSON compliance export
    GET  /export/{session_id}/report   Printable HTML audit report
    WS   /ws                           Live event stream (new calls as they happen)
    POST /mcp                          MCP tool server

Or via CLI:
    python -m agenticledger.proxy
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
from pathlib import Path
from typing import AsyncIterator, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

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
from .detect import detect_agent
from .export import build_export, render_html_report
from .loops import DEFAULT_REPEAT_THRESHOLD, DEFAULT_RUN_GAP_SECONDS, LoopTracker, is_utility_call
from .mcp import handle_mcp
from .normalize import (
    CanonicalRequest,
    CanonicalResponse,
    normalize_request,
    normalize_response,
)
from .otel import emit_span
from .otlp_ingest import decode_protobuf as decode_otlp_protobuf
from .otlp_ingest import extract_calls as extract_otlp_calls
from .otlp_ingest import extract_tool_events as extract_otlp_tool_events
from .pricing import compute_cost
from .ratelimit import RateLimitConfig, RateLimiter
from .redact import Redactor, apply_capture_policy, normalize_capture_level
from .replay import build_replay_request, replay_auth_headers, replayable_reason
from .reports import build_report, digest_text
from .store import Store
from .stream import detect_stream_error, reconstruct_from_sse

logger = logging.getLogger(__name__)

# Built SPA assets (dashboard-app/ → vite build). Absent in source checkouts.
_SPA_DIR = Path(__file__).parent / "static"

_DEFAULT_LLM_PATHS = {"v1/chat/completions", "v1/messages", "v1/responses"}
_extra = os.getenv("AGENTICLEDGER_EXTRA_PATHS", "")
_LLM_PATHS = _DEFAULT_LLM_PATHS | {p.strip() for p in _extra.split(",") if p.strip()}
# Free metering endpoints: captured for a complete record, but exempt from
# rate-limit/budget enforcement (the calls cost nothing) and never streamed.
_COUNT_TOKENS_PATHS = {"v1/messages/count_tokens"}
_CAPTURED_PATHS = _LLM_PATHS | _COUNT_TOKENS_PATHS

_AL_HEADERS = {
    "x-agenticledger-session-id",
    "x-agenticledger-user-id",
    "x-agenticledger-agent-name",
    "x-agenticledger-app-id",
    "x-agenticledger-parent-action-id",
    "x-agenticledger-environment",
    "x-agenticledger-handoff-from",
    "x-agenticledger-handoff-to",
    "x-agenticledger-framework",
    "x-agenticledger-run-id",
    "x-agenticledger-iteration",
    "x-agenticledger-ingest-key",
    "x-agenticledger-api-key",
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
    """Pull an API token from a request/websocket: Bearer header, x-agenticledger-token, or ?token."""
    authz = carrier.headers.get("authorization") or ""
    if authz.lower().startswith("bearer "):
        return authz[7:].strip() or None
    return carrier.headers.get("x-agenticledger-token") or carrier.query_params.get("token")


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
    budget_user: Optional[float] = None,   # max USD per user_id per UTC day
    budget_action: str = "block",   # "block" | "warn" | "both"
    budget_status: int = 429,       # 429 (default) or 402 — 402 stops client retry storms
    alert_config: Optional[AlertConfig] = None,
    rate_limit_config: Optional[RateLimitConfig] = None,
    async_capture: bool = False,
    capture_queue_max: int = 10_000,
    capture_level: str = "full",
    redactor: Optional[Redactor] = None,
    retention_days: Optional[float] = None,
    retention_interval_seconds: float = 3600.0,
    audit_enabled: bool = True,
    loop_action: str = "warn",   # "warn" | "block" | "off"
    loop_max_steps: Optional[int] = None,
    loop_repeat_threshold: int = DEFAULT_REPEAT_THRESHOLD,
    loop_run_gap_seconds: float = DEFAULT_RUN_GAP_SECONDS,
    completion_promise: Optional[str] = None,
    digest_hour: Optional[int] = None,   # UTC hour (0-23) for the daily digest webhook
    replay_api_key: Optional[str] = None,   # enables POST /api/replay when set
) -> FastAPI:

    broadcaster = _Broadcaster()
    _rate_limiter = RateLimiter(rate_limit_config or RateLimitConfig())
    # Loop/run inference over the capture stream + optional in-path guard.
    _loop_action = loop_action if loop_action in ("warn", "block", "off") else "warn"
    _loop_tracker = LoopTracker(
        repeat_threshold=loop_repeat_threshold,
        run_gap_seconds=loop_run_gap_seconds,
        max_steps=loop_max_steps,
        completion_promise=completion_promise,
    )
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
        digest_task: Optional[asyncio.Task] = None
        if digest_hour is not None and _alert_config.webhook_url:
            digest_task = asyncio.create_task(_digest_worker(app))
        yield
        if digest_task is not None:
            digest_task.cancel()
            with suppress(asyncio.CancelledError):
                await digest_task
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
        # Loop/run inference must run BEFORE the capture policy — metadata level
        # empties req.messages, and the chain hashes need the raw content.
        # count_tokens metering and framework utility calls (Claude Code's
        # small title/summary requests) carry conversation-shaped histories —
        # feeding them to the tracker would inflate step counts and reset
        # repeat streaks, so they stay out of inference.
        loop_fields = (
            _loop_tracker.annotate(job.action_id, job.req, job.resp, job.meta)
            if _loop_action != "off" and job.status_code == 200
            and job.resp.stop_reason != "count_tokens"
            and not is_utility_call(job.req, job.meta)
            else {}
        )
        tool_executions = loop_fields.pop("tool_executions", [])
        apply_capture_policy(job.req, job.resp, _capture_level, _redactor)
        store = app.state.store
        await store.save(
            job.action_id, job.req, job.resp,
            status_code=job.status_code, error_detail=job.error_detail,
            **{**job.meta, **loop_fields},
        )
        if tool_executions:
            # Derived data — its failure must never count as a capture drop.
            with suppress(Exception):
                await store.save_tool_executions(tool_executions)
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
        if loop_fields.get("loop_flags") and _alert_config.webhook_url:
            with suppress(Exception):
                from .alerts import _fire
                flags = json.loads(loop_fields["loop_flags"])
                await _fire(_alert_config.webhook_url, {
                    "type": "loop_flag",
                    "message": f"Loop health flags raised: {loop_fields['loop_flags']}",
                    "flags": flags,
                    "action_id": job.action_id,
                    "session_id": job.meta.get("session_id"),
                    "agent_name": job.meta.get("agent_name"),
                    "thread_id": loop_fields.get("thread_id"),
                    "step_index": loop_fields.get("step_index"),
                })
                # The morning report: the completion promise ends a run, so
                # send the whole run's story in one webhook — iterations,
                # spend, tokens, and how many calls got flagged on the way.
                run_id = loop_fields.get("run_id")
                if "completion_promise" in flags and run_id:
                    run = await store.get_run(run_id)
                    if run is not None:
                        await _fire(_alert_config.webhook_url, {
                            "type": "run_complete",
                            "message": (
                                f"Run {run_id} complete: "
                                f"{run.get('iterations') or '?'} iterations, "
                                f"{run['call_count']} calls, "
                                f"${(run.get('total_cost_usd') or 0):.2f}, "
                                f"{run['flagged_calls']} flagged calls."
                            ),
                            "run_id": run_id,
                            "iterations": run.get("iterations"),
                            "call_count": run["call_count"],
                            "session_count": run.get("session_count"),
                            "total_cost_usd": run.get("total_cost_usd"),
                            "total_tokens_in": run.get("total_tokens_in"),
                            "total_tokens_out": run.get("total_tokens_out"),
                            "flagged_calls": run["flagged_calls"],
                            "started_at": run.get("started_at"),
                        })

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

    async def _digest_worker(app: FastAPI) -> None:
        """Once a day at digest_hour UTC, post a spend digest for the last
        24h to the alert webhook (Slack-incoming-webhook friendly `text`)."""
        import datetime as _dt

        from .alerts import _fire
        while True:
            now = _dt.datetime.now(_dt.timezone.utc)
            target = now.replace(hour=digest_hour, minute=0, second=0, microsecond=0)
            if target <= now:
                target += _dt.timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            try:
                raw = await app.state.store.get_report_aggregates(time.time() - 86400)
                report = build_report(raw["daily"], raw["models"], raw["agents"], days=1)
                await _fire(_alert_config.webhook_url, {
                    "type": "daily_digest",
                    "text": digest_text(report, hours=24),
                    "totals": report["totals"],
                })
            except Exception:
                logger.warning("Daily digest failed", exc_info=True)

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

    _api_key = os.environ.get("AGENTICLEDGER_API_KEY")
    # Optional proxy-ingest key. When set, the proxy refuses to forward a request
    # unless it carries a matching x-agenticledger-ingest-key — closing the open relay.
    # When unset the proxy forwards anything (zero-config dev UX); __main__ warns loudly.
    _ingest_key = os.environ.get("AGENTICLEDGER_INGEST_KEY")
    # Read/management endpoints enforce auth only when a master key is configured.
    # The master key grants admin (and is the bootstrap for minting tokens); API
    # tokens grant their own role. When unset, access is open (dev UX) and __main__ warns.
    _auth_enabled = bool(_api_key)

    async def _authenticate(carrier) -> Optional[Principal]:
        """Resolve a Principal from a request/websocket, or None if no valid credential."""
        supplied_key = carrier.headers.get("x-agenticledger-api-key") or carrier.query_params.get("api_key")
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
            "# HELP agenticledger_captures_persisted_total Calls successfully recorded to the store.",
            "# TYPE agenticledger_captures_persisted_total counter",
            f"agenticledger_captures_persisted_total {persisted}",
            "# HELP agenticledger_captures_dropped_total Calls served but not recorded (error or queue overflow).",
            "# TYPE agenticledger_captures_dropped_total counter",
            f"agenticledger_captures_dropped_total {dropped}",
            "# HELP agenticledger_capture_queue_depth Capture jobs awaiting persistence (async mode).",
            "# TYPE agenticledger_capture_queue_depth gauge",
            f"agenticledger_capture_queue_depth {depth}",
            "# HELP agenticledger_capture_async Whether async capture is enabled (1) or not (0).",
            "# TYPE agenticledger_capture_async gauge",
            f"agenticledger_capture_async {1 if _async_capture else 0}",
        ]
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    # ── Dashboard ────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        """The web app (React SPA) when its build is present; source checkouts
        without a Node build fall back to the classic embedded dashboard."""
        await _require(request, ROLE_VIEWER)
        index = _SPA_DIR / "index.html"
        if index.is_file():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse(get_dashboard_html())

    @app.get("/classic", response_class=HTMLResponse)
    async def classic_dashboard(request: Request) -> HTMLResponse:
        await _require(request, ROLE_VIEWER)
        return HTMLResponse(get_dashboard_html())

    # ── Web app (React SPA — Loop Lens) ──────────────────────────────────────
    # Built from dashboard-app/ into agenticledger/proxy/static/ and shipped in
    # the wheel. When the assets are missing (e.g. a source checkout without a
    # Node build), /app explains itself and the classic dashboard still works.

    @app.get("/app", response_class=HTMLResponse)
    async def spa_index(request: Request) -> HTMLResponse:
        await _require(request, ROLE_VIEWER)
        index = _SPA_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=404,
                detail="Web app not built — run `npm run build` in dashboard-app/, "
                       "or use the classic dashboard at /",
            )
        return HTMLResponse(index.read_text(encoding="utf-8"))

    @app.get("/app/assets/{filename}")
    async def spa_asset(filename: str) -> FileResponse:
        # Hashed build artifacts (js/css) — no data inside, served ungated so
        # the browser can load them without credential plumbing. Serve by
        # exact match against the directory listing: the request name is only
        # ever used as a lookup key, and the path handed to FileResponse
        # comes from our own scandir — user input never becomes a path.
        try:
            entries = {e.name: e.path for e in os.scandir(_SPA_DIR / "assets") if e.is_file()}
        except OSError:
            raise HTTPException(status_code=404) from None
        path = entries.get(filename)
        if path is None:
            raise HTTPException(status_code=404)
        return FileResponse(path)

    # ── WebSocket (live events) ───────────────────────────────────────────────

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        # Live events carry call metadata (session ids, status codes) — require
        # the same credential as the dashboard when auth is configured. Closing
        # before accept rejects the handshake with 1008 (policy violation).
        if _auth_enabled and await _authenticate(websocket) is None:
            await websocket.close(code=1008)
            return
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

    @app.get("/api/runs")
    async def api_runs(request: Request) -> JSONResponse:
        await _require(request, ROLE_VIEWER)
        store = request.app.state.store
        runs = await store.list_runs()
        ended = await store.get_run_end_markers([r["run_id"] for r in runs])
        return JSONResponse([
            _with_run_status(r, loop_run_gap_seconds, explicitly_ended=r["run_id"] in ended)
            for r in runs
        ])

    @app.post("/api/runs/{run_id}/end")
    async def api_run_end(run_id: str, request: Request) -> JSONResponse:
        """The runner's exit signal: marks the run ended immediately instead
        of waiting for the inactivity window. Idempotent."""
        principal = await _require(request, ROLE_EDITOR)
        store = request.app.state.store
        if await store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run_id not found")
        await store.mark_run_ended(run_id, time.time())
        await _audit(principal, request, "run_end", run_id, "runner exit signal")
        return JSONResponse({"run_id": run_id, "status": "ended"})

    @app.post("/api/replay")
    async def api_replay(request: Request) -> JSONResponse:
        """Re-execute a captured call (optionally on a swapped same-provider
        model) using the proxy's replay credential; the result is stored as a
        new call linked to the original."""
        principal = await _require(request, ROLE_EDITOR)
        if not replay_api_key:
            return JSONResponse(
                {"error": "Replay is not configured — set AGENTICLEDGER_REPLAY_API_KEY "
                          "on the proxy to enable re-execution."},
                status_code=409,
            )
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        action_id = str(payload.get("action_id") or "").strip()
        original = await request.app.state.store.get(action_id) if action_id else None
        if original is None:
            raise HTTPException(status_code=404, detail="action_id not found")
        reason = replayable_reason(original)
        if reason:
            return JSONResponse({"error": f"Not replayable: {reason}"}, status_code=400)

        provider = original.get("provider")
        model = str(payload.get("model") or original["model_id"]).strip()
        path, body = build_replay_request(original, model)
        start = time.time()
        try:
            upstream = await request.app.state.client.post(
                "/" + path, json=body,
                headers=replay_auth_headers(provider, replay_api_key),
            )
        except Exception as exc:
            return JSONResponse({"error": f"Upstream unreachable: {exc}"}, status_code=502)
        latency_ms = (time.time() - start) * 1000
        if upstream.status_code != 200:
            payload = {"error": "Upstream rejected the replay",
                       "upstream_status": upstream.status_code,
                       "detail": upstream.text[:500]}
            if upstream.status_code == 401:
                payload["hint"] = (
                    "The replay key was rejected by the provider — check "
                    "AGENTICLEDGER_REPLAY_API_KEY. A Claude Code subscription "
                    "login is not an API key; create one at console.anthropic.com, "
                    "or replay for free against a local model (see the LM Studio "
                    "integration guide)."
                )
            return JSONResponse(payload, status_code=502)
        resp = normalize_response(upstream.json(), latency_ms, model)
        if resp.cost_usd is None:
            resp.cost_usd = compute_cost(
                model, resp.tokens_in or 0, resp.tokens_out or 0,
                cache_read_tokens=resp.cache_read_tokens,
                cache_write_tokens=resp.cache_write_tokens,
                provider=provider or "",
            )
        req = CanonicalRequest(
            messages=original.get("messages") or [], model_id=model,
            provider=provider or "", timestamp=start,
            tools=original.get("tools"), system_prompt=original.get("system_prompt"),
            temperature=original.get("temperature"), max_tokens=original.get("max_tokens"),
        )
        new_id = str(uuid.uuid4())
        await request.app.state.store.save(
            new_id, req, resp,
            session_id=f"replay-{action_id[:8]}",
            agent_name=original.get("agent_name"),
            framework="replay",
            parent_action_id=action_id,
            environment=original.get("environment") or "development",
        )
        await _audit(principal, request, "replay", action_id, f"model={model}")
        return JSONResponse({
            "original": {
                "action_id": action_id, "model_id": original["model_id"],
                "content": original.get("content"),
                "tokens_in": original.get("tokens_in"), "tokens_out": original.get("tokens_out"),
                "cache_read_tokens": original.get("cache_read_tokens"),
                "cache_write_tokens": original.get("cache_write_tokens"),
                "cost_usd": original.get("cost_usd"), "latency_ms": original.get("latency_ms"),
            },
            "replay": {
                "action_id": new_id, "model_id": model,
                "content": resp.content, "tool_calls": resp.tool_calls,
                "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
                "cache_read_tokens": resp.cache_read_tokens,
                "cache_write_tokens": resp.cache_write_tokens,
                "cost_usd": resp.cost_usd, "latency_ms": round(latency_ms, 1),
            },
        })

    @app.get("/api/reports")
    async def api_reports(request: Request, days: int = 30,
                          tz_offset_minutes: int = 0) -> JSONResponse:
        """Spend insights over the window: daily trend, model mix with
        signed cache savings, and per-agent totals. tz_offset_minutes shifts
        the day bucketing (positive = east of UTC) so 'per day' can mean the
        viewer's local day; budgets and the digest remain UTC."""
        await _require(request, ROLE_VIEWER)
        days = max(1, min(days, 365))
        tz_offset_minutes = max(-840, min(tz_offset_minutes, 840))
        raw = await request.app.state.store.get_report_aggregates(
            time.time() - days * 86400, tz_offset_minutes=tz_offset_minutes)
        return JSONResponse(build_report(raw["daily"], raw["models"], raw["agents"], days))

    @app.get("/api/sessions/{session_id}/tools")
    async def api_session_tools(session_id: str, request: Request) -> JSONResponse:
        """Derived tool executions for a session — the proxy pairs each
        tool call with the result fed back in the following LLM call."""
        await _require(request, ROLE_VIEWER)
        tools = await request.app.state.store.get_tool_executions(session_id)
        return JSONResponse(tools)

    @app.get("/api/runs/{run_id}")
    async def api_run_status(run_id: str, request: Request) -> JSONResponse:
        """Run status for loop runners: poll this between iterations and stop
        when status is 'complete' (completion promise seen) or on budget."""
        await _require(request, ROLE_VIEWER)
        run = await request.app.state.store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        ended = await request.app.state.store.get_run_end_markers([run["run_id"]])
        return JSONResponse(_with_run_status(
            run, loop_run_gap_seconds, explicitly_ended=run["run_id"] in ended))

    @app.get("/api/runs/{run_id}/iterations")
    async def api_run_iterations(run_id: str, request: Request) -> JSONResponse:
        await _require(request, ROLE_VIEWER)
        iterations = await request.app.state.store.get_run_iterations(run_id)
        return JSONResponse(iterations)

    @app.get("/api/runs/{run_id}/flags")
    async def api_run_flags(run_id: str, request: Request) -> JSONResponse:
        """The calls behind a run's flagged count, with enough context to
        understand each flag (session, iteration, step, tool calls)."""
        await _require(request, ROLE_VIEWER)
        flags = await request.app.state.store.get_flagged_calls(run_id)
        return JSONResponse(flags)

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
        filename = f"agenticledger-{session_id[:16]}.json"
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

    # ── OTLP ingest (OTel-native frameworks) ─────────────────────────────────
    # Registered before the catch-all proxy so OTLP paths never forward
    # upstream. GenAI spans become ledger calls; logs/metrics are acked so
    # exporters don't buffer and retry forever.

    def _check_ingest_gate(request: Request) -> Optional[JSONResponse]:
        if _ingest_key:
            supplied = request.headers.get("x-agenticledger-ingest-key")
            if not supplied or not hmac.compare_digest(supplied, _ingest_key):
                return JSONResponse(
                    {"error": {"type": "unauthorized",
                               "message": "Missing or invalid x-agenticledger-ingest-key."}},
                    status_code=401,
                )
        return None

    def _otlp_ack(request: Request) -> Response:
        """Success response in the caller's encoding. An empty protobuf body
        is a valid Export*ServiceResponse meaning full success."""
        if "protobuf" in (request.headers.get("content-type") or ""):
            return Response(content=b"", media_type="application/x-protobuf")
        return JSONResponse({"partialSuccess": {}})

    async def _otlp_payload(request: Request, kind: str):
        """Decode an OTLP request body (JSON or protobuf) to the JSON dict
        shape, or return an error Response."""
        content_type = request.headers.get("content-type") or ""
        if "protobuf" in content_type:
            try:
                payload = decode_otlp_protobuf(await request.body(), kind)
            except Exception:
                return JSONResponse({"error": "invalid protobuf payload"}, status_code=400)
            if payload is None:
                return JSONResponse(
                    {"error": "http/protobuf ingest needs opentelemetry-proto — "
                              "install agentic-ledger[otel], or set "
                              "OTEL_EXPORTER_OTLP_PROTOCOL=http/json on the exporter."},
                    status_code=415,
                )
            return payload
        if "json" in content_type:
            try:
                payload = await request.json()
            except Exception:
                return JSONResponse({"error": "invalid JSON"}, status_code=400)
            return payload if isinstance(payload, dict) else {}
        return JSONResponse(
            {"error": "Unsupported content-type — the OTLP JSON and protobuf "
                      "encodings are accepted."},
            status_code=415,
        )

    @app.post("/v1/traces")
    async def otlp_traces(request: Request) -> Response:
        denied = _check_ingest_gate(request)
        if denied:
            return denied
        payload = await _otlp_payload(request, "traces")
        if isinstance(payload, Response):
            return payload

        store = request.app.state.store
        saved = 0
        for call in extract_otlp_calls(payload):
            meta = call["meta"]
            status_code = meta.pop("status_code", 200)
            error_detail = meta.pop("error_detail", None)
            try:
                await store.save(
                    call["action_id"], call["req"], call["resp"],
                    status_code=status_code, error_detail=error_detail, **meta,
                )
                saved += 1
                app.state.capture_persisted += 1
                with suppress(Exception):
                    await broadcaster.broadcast({
                        "type": "call",
                        "action_id": call["action_id"],
                        "session_id": meta.get("session_id"),
                        "status_code": status_code,
                        "budget_warning": False,
                    })
            except Exception:
                # Duplicate action_id (re-exported batch) or storage hiccup —
                # OTLP delivery is at-least-once, so duplicates are expected.
                pass
        return _otlp_ack(request)

    @app.post("/v1/logs")
    async def otlp_logs(request: Request) -> Response:
        """Tool-result events (e.g. Claude Code's on-machine audit trail)
        become tool_executions rows; everything else is acknowledged."""
        denied = _check_ingest_gate(request)
        if denied:
            return denied
        with suppress(Exception):
            payload = await _otlp_payload(request, "logs")
            if isinstance(payload, dict):
                events = extract_otlp_tool_events(payload)
                if events:
                    with suppress(Exception):
                        await request.app.state.store.save_tool_executions(events)
        return _otlp_ack(request)

    @app.post("/v1/metrics")
    async def otlp_metrics_ack(request: Request) -> Response:
        denied = _check_ingest_gate(request)
        if denied:
            return denied
        return _otlp_ack(request)

    # ── Transparent proxy ────────────────────────────────────────────────────

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy(request: Request, path: str) -> Response:
        # Proxy-ingest auth: gate forwarding behind a dedicated key when configured.
        if _ingest_key:
            supplied = request.headers.get("x-agenticledger-ingest-key")
            if not supplied or not hmac.compare_digest(supplied, _ingest_key):
                return JSONResponse(
                    {"error": {
                        "type": "unauthorized",
                        "message": "Missing or invalid x-agenticledger-ingest-key.",
                    }},
                    status_code=401,
                )

        # Path-segment run attribution: /r/<run_id>/<iteration>/<real path>.
        # For clients that can only set a base URL and no custom headers —
        # `agenticledger run` points ANTHROPIC_BASE_URL/OPENAI_BASE_URL here.
        path_run_id: Optional[str] = None
        path_iteration: Optional[str] = None
        if path.startswith("r/"):
            seg = path.split("/", 3)
            if len(seg) == 4 and seg[3]:
                path_run_id, path_iteration, path = seg[1], seg[2], seg[3]

        body_bytes = await request.body()

        # Parse the request body exactly once — streaming detection, budget
        # capture, and normalization all need it, and coding-agent bodies can
        # be megabytes of context.
        body_json: Optional[dict] = None
        if request.method == "POST" and path in _CAPTURED_PATHS and body_bytes:
            with suppress(Exception):
                parsed = json.loads(body_bytes)
                if isinstance(parsed, dict):
                    body_json = parsed

        is_llm_path = body_json is not None
        is_count_tokens = is_llm_path and path in _COUNT_TOKENS_PATHS
        is_streaming = is_llm_path and not is_count_tokens and bool(body_json.get("stream"))
        is_llm_call = is_llm_path and not is_streaming

        action_id = str(uuid.uuid4()) if is_llm_path else None
        meta = _extract_meta(request, body_json)
        if path_run_id and not meta.get("run_id"):
            meta["run_id"] = path_run_id
            if meta.get("iteration") is None:
                meta["iteration"] = _int_or_none(path_iteration)

        # ── Rate limit check ─────────────────────────────────────────────────
        # Fail open: a rate-limiter error must never block the agent's LLM call.
        # count_tokens is free — it neither consumes quota nor gets blocked.
        if is_llm_path and not is_count_tokens:
            try:
                rate_error = _rate_limiter.check(
                    meta.get("session_id"), meta.get("agent_name"), meta.get("user_id")
                )
            except Exception:
                logger.warning("Rate limiter check failed — allowing call through", exc_info=True)
                rate_error = None
            if rate_error:
                # Sliding 60s window — a retry after it genuinely can succeed.
                return JSONResponse(
                    {"error": {"type": "rate_limit_exceeded", "message": rate_error}},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )

        # ── Loop circuit breaker ─────────────────────────────────────────────
        # Only in block mode: warn mode surfaces flags via alerts/dashboard,
        # and detection state comes from already-captured calls (cheap lookup).
        if is_llm_path and _loop_action == "block":
            loop_error = _loop_tracker.check_block(meta.get("session_id"))
            if loop_error:
                return JSONResponse(
                    {"error": {"type": "loop_detected", "message": loop_error}},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )

        # ── Budget check ─────────────────────────────────────────────────────
        # Fail open: if the store is unavailable the agent must not be blocked.
        # Budget enforcement resumes automatically once the store recovers.
        _budget_warning: Optional[str] = None  # set in warn mode; carried into actual save
        if is_llm_path and not is_count_tokens and (budget_session is not None or budget_agent is not None or budget_daily is not None or budget_user is not None):
            try:
                budget_error, budget_retry_after = await _check_budgets(
                    request.app.state.store, meta,
                    budget_session, budget_agent, budget_daily, budget_user,
                )
            except Exception:
                logger.warning("Budget check failed — allowing call through", exc_info=True)
                budget_error, budget_retry_after = None, None
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
                            status_code=budget_status, error_detail=budget_error, **meta,
                        )
                        await broadcaster.broadcast({
                            "type": "call",
                            "action_id": action_id,
                            "session_id": meta.get("session_id"),
                            "status_code": budget_status,
                            "budget_warning": False,
                        })
                    except Exception:
                        _record_capture_drop(request.app, action_id)
                    # A budget exceedance is not transient: tell well-behaved
                    # clients when retrying could actually succeed (daily
                    # windows reset at UTC midnight; session budgets never do).
                    headers = ({"Retry-After": str(budget_retry_after)}
                               if budget_retry_after else {})
                    return JSONResponse(
                        {"error": {"type": "budget_exceeded", "message": budget_error}},
                        status_code=budget_status,
                        headers=headers,
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


def _seconds_to_utc_midnight() -> int:
    """When daily budget windows reset — the honest Retry-After value."""
    now = datetime.datetime.now(datetime.timezone.utc)
    tomorrow = (now + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


async def _check_budgets(
    store: Store,
    meta: dict,
    budget_session: Optional[float],
    budget_agent: Optional[float],
    budget_daily: Optional[float],
    budget_user: Optional[float] = None,
) -> tuple[Optional[str], Optional[int]]:
    """(error message, Retry-After seconds) if a budget is exceeded, else
    (None, None). Daily windows carry the seconds until UTC midnight;
    session budgets never reset, so they carry no Retry-After."""
    session_id = meta.get("session_id")
    agent_name = meta.get("agent_name")
    user_id = meta.get("user_id")

    if budget_session is not None and session_id:
        spent = await store.get_session_cost(session_id)
        if spent >= budget_session:
            return (
                f"Session budget of ${budget_session:.4f} exceeded "
                f"(current spend: ${spent:.4f}). Session: {session_id}",
                None,
            )

    if budget_agent is not None and agent_name:
        since = _today_start_ts()
        spent = await store.get_agent_cost(agent_name, since)
        if spent >= budget_agent:
            return (
                f"Agent daily budget of ${budget_agent:.4f} exceeded "
                f"(current spend: ${spent:.4f}). Agent: {agent_name}",
                _seconds_to_utc_midnight(),
            )

    if budget_user is not None and user_id:
        since = _today_start_ts()
        spent = await store.get_user_cost(user_id, since)
        if spent >= budget_user:
            return (
                f"User daily budget of ${budget_user:.4f} exceeded "
                f"(current spend: ${spent:.4f}). User: {user_id}",
                _seconds_to_utc_midnight(),
            )

    if budget_daily is not None:
        since = _today_start_ts()
        spent = await store.get_period_cost(since)
        if spent >= budget_daily:
            return (
                f"Daily budget of ${budget_daily:.4f} exceeded "
                f"(current spend: ${spent:.4f}).",
                _seconds_to_utc_midnight(),
            )

    return None, None


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


def _extract_meta(request: Request, body_json: Optional[dict] = None) -> dict:
    import datetime
    h = request.headers
    # Explicit headers always win; fingerprint detection only fills the gaps
    # (framework tag, agent identity, and a real per-run session id for
    # clients like Claude Code that never send x-agenticledger-* headers).
    detected = detect_agent(h, body_json)
    session_id = (
        h.get("x-agenticledger-session-id")
        or detected["session_id"]
        or f"auto-{datetime.date.today().isoformat()}"
    )
    return {
        "session_id":       session_id,
        "user_id":          h.get("x-agenticledger-user-id"),
        "agent_name":       h.get("x-agenticledger-agent-name") or detected["agent_name"],
        "app_id":           h.get("x-agenticledger-app-id"),
        "parent_action_id": h.get("x-agenticledger-parent-action-id"),
        "environment":      h.get("x-agenticledger-environment", "development"),
        "handoff_from":     h.get("x-agenticledger-handoff-from"),
        "handoff_to":       h.get("x-agenticledger-handoff-to"),
        "framework":        h.get("x-agenticledger-framework") or detected["framework"],
        "run_id":           h.get("x-agenticledger-run-id"),
        "iteration":        _int_or_none(h.get("x-agenticledger-iteration")),
    }


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _with_run_status(run: dict, run_gap_seconds: float = DEFAULT_RUN_GAP_SECONDS,
                     explicitly_ended: bool = False) -> dict:
    """Derive a runner-facing status from the aggregate row.

    Precedence: completion promise → flags → an explicit end marker (the
    runner told us the loop exited) → inactivity inference (last call older
    than the run-gap window) → running."""
    promise_seen = bool(run.pop("promise_seen", 0))
    if promise_seen:
        run["status"] = "complete"
    elif run.get("flagged_calls"):
        run["status"] = "flagged"
    elif explicitly_ended:
        run["status"] = "ended"
    else:
        run["status"] = "running"
        last = run.get("last_call_at")
        with suppress(Exception):
            last_dt = datetime.datetime.fromisoformat(str(last))
            age = (datetime.datetime.now(datetime.timezone.utc) - last_dt).total_seconds()
            if age > run_gap_seconds:
                run["status"] = "ended"
    return run


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
        headers["x-agenticledger-action-id"] = action_id
    if meta.get("session_id"):
        headers["x-agenticledger-session-id"] = meta["session_id"]
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
