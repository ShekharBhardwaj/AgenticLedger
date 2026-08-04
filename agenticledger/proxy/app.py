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
    GET  /api/reports.csv?days=N       Model spend insights as CSV
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
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from .alerts import AlertConfig, check_and_fire
from .auth import (
    ROLE_ADMIN,
    ROLE_EDITOR,
    ROLE_INGEST,
    ROLE_VIEWER,
    Principal,
    generate_token,
    hash_token,
    role_satisfies,
    valid_role,
)
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
from .pricing import compute_cost, infer_provider
from .ratelimit import RateLimitConfig, RateLimiter
from .redact import Redactor, apply_capture_policy, normalize_capture_level
from .replay import (
    NotTranslatable,
    build_cross_request,
    build_replay_request,
    replay_auth_headers,
    replayable_reason,
    score_replay,
)
from .reports import build_report, digest_text, estimate_whatif
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


def _secret_env(name: str) -> Optional[str]:
    """NAME from the environment, or the stripped contents of the file named
    by NAME_FILE — the Docker/Kubernetes secrets pattern, so keys never have
    to be typed on a command line and land in shell history."""
    val = os.environ.get(name)
    if val:
        return val
    path = os.environ.get(f"{name}_FILE")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip() or None
        except OSError:
            logger.warning("Could not read %s_FILE at %s", name, path)
    return None


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
    upstream_auto: bool = False,   # no upstream configured: route by wire format
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
    replay_targets: Optional[dict] = None,  # {"openai": {"url", "key"}, "anthropic": {...}}
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
        # Operator kill switch: run ids whose calls are refused at the wall.
        # Loaded once, kept in memory (the hot path must not pay a query),
        # persisted as marker rows so a restart keeps the wall up.
        app.state.stopped_runs = set(
            (await app.state.store.get_labels("stopped")).keys())
        app.state.client = httpx.AsyncClient(
            base_url=upstream_url,
            timeout=httpx.Timeout(120.0),
        )
        # Zero-config routing: with no explicit upstream, Anthropic-format
        # calls go to Anthropic while everything else keeps the OpenAI-format
        # default above. An explicitly configured upstream never creates this
        # client, so it always wins.
        app.state.client_anthropic = httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            timeout=httpx.Timeout(120.0),
        ) if upstream_auto else None
        app.state.replay_clients = {
            prov: httpx.AsyncClient(base_url=cfg["url"], timeout=httpx.Timeout(120.0))
            for prov, cfg in (replay_targets or {}).items()
        }
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
        if app.state.client_anthropic is not None:
            await app.state.client_anthropic.aclose()
        for rc in app.state.replay_clients.values():
            await rc.aclose()

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

    _api_key = _secret_env("AGENTICLEDGER_API_KEY")
    # Optional proxy-ingest key. When set, the proxy refuses to forward a request
    # unless it carries a matching x-agenticledger-ingest-key — closing the open relay.
    # When unset the proxy forwards anything (zero-config dev UX); __main__ warns loudly.
    _ingest_key = _secret_env("AGENTICLEDGER_INGEST_KEY")
    # Read/management endpoints enforce auth only when a master key is configured.
    # The master key grants admin (and is the bootstrap for minting tokens); API
    # tokens grant their own role. When unset, access is open (dev UX) and __main__ warns.
    _auth_enabled = bool(_api_key)

    async def _authenticate(carrier) -> Optional[Principal]:
        """Resolve a Principal from a request/websocket, or None if no valid credential.

        Every credential channel (x-agenticledger-api-key header, ?api_key,
        Bearer/x-agenticledger-token/?token) accepts every kind of key — the
        server sorts out what it was handed. Asymmetry here caused real bugs:
        minted tokens pasted into the dashboard's ⚿ field silently 401'd, and
        the SPA's websocket sent the master key on the token channel."""
        candidates = [c for c in (
            carrier.headers.get("x-agenticledger-api-key"),
            carrier.query_params.get("api_key"),
            _extract_token(carrier),
        ) if c]
        for cand in candidates:
            if _api_key and hmac.compare_digest(cand, _api_key):
                return Principal(ROLE_ADMIN, "master")
        for cand in candidates:
            row = await carrier.app.state.store.get_token_by_hash(hash_token(cand))
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

    # Dashboard HTML shells are served without auth — they carry no ledger
    # data (every number comes from the individually-gated /api endpoints),
    # and the ⚿ key panel lives inside the page, so gating the shell would
    # lock the door with the keyhole behind it (the login-page pattern).

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        """The web app (React SPA). Wheel and Docker installs always ship the
        build; a source checkout without one gets told how to make it."""
        index = _SPA_DIR / "index.html"
        if index.is_file():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        raise HTTPException(
            status_code=404,
            detail="Web app not built — run `npm run build` in dashboard-app/ "
                   "(PyPI and Docker installs include it).",
        )

    # ── Web app (React SPA — Loop Lens) ──────────────────────────────────────
    # Built from dashboard-app/ into agenticledger/proxy/static/ and shipped in
    # the wheel. When the assets are missing (e.g. a source checkout without a
    # Node build), / and /app explain how to build them.

    @app.get("/app", response_class=HTMLResponse)
    async def spa_index(request: Request) -> HTMLResponse:
        index = _SPA_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(
                status_code=404,
                detail="Web app not built — run `npm run build` in dashboard-app/ "
                       "(PyPI and Docker installs include it).",
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

    def _annotate_labels(rows: list, labels: dict, key: str,
                         rules: Optional[dict] = None,
                         run_projects: Optional[dict] = None) -> list:
        for r in rows:
            lab = labels.get(r.get(key)) or {}
            r["label"] = lab.get("name")
            r["pinned"] = bool(lab.get("pinned"))
            r["project"] = lab.get("project")
            r["project_auto"] = False
            # Auto-filing, weakest-to-strongest: a session inherits its
            # run's project (filing a loop files its sessions), an app
            # binding files matching work, and a hand-assigned project on
            # the row itself always wins. Computed at read time, so it
            # applies retroactively and un-applies when rules change.
            if r["project"] is None:
                auto = None
                if rules:
                    auto = rules.get(r.get("app_id") or "")
                if auto is None and run_projects:
                    auto = run_projects.get(r.get("run_id") or "")
                if auto:
                    r["project"] = auto
                    r["project_auto"] = True
        return rows

    async def _run_project_map(store) -> dict:
        """run_id → explicitly assigned project, for session inheritance."""
        return {rid: lab["project"]
                for rid, lab in (await store.get_labels("run")).items()
                if lab.get("project")}

    @app.get("/api/sessions")
    async def api_sessions(request: Request) -> JSONResponse:
        await _require(request, ROLE_VIEWER)
        store = request.app.state.store
        sessions = await store.list_sessions()
        return JSONResponse(_annotate_labels(
            sessions, await store.get_labels("session"), "session_id",
            await store.get_project_rules(),
            await _run_project_map(store)))

    @app.get("/api/runs")
    async def api_runs(request: Request) -> JSONResponse:
        await _require(request, ROLE_VIEWER)
        store = request.app.state.store
        runs = await store.list_runs()
        ended = await store.get_run_end_markers([r["run_id"] for r in runs])
        runs = _annotate_labels(runs, await store.get_labels("run"), "run_id",
                                await store.get_project_rules())
        return JSONResponse([
            _with_run_status(r, loop_run_gap_seconds,
                             explicitly_ended=r["run_id"] in ended,
                             stopped=r["run_id"] in request.app.state.stopped_runs)
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

    @app.post("/api/runs/{run_id}/stop")
    async def api_run_stop(run_id: str, request: Request) -> JSONResponse:
        """The kill switch: refuse this run's further calls at the wall
        until resumed. The run keeps its history; nothing is deleted."""
        principal = await _require(request, ROLE_EDITOR)
        store = request.app.state.store
        if await store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run_id not found")
        await store.set_label("stopped", run_id, name="operator")
        request.app.state.stopped_runs.add(run_id)
        await _audit(principal, request, "run_stop", run_id,
                     "operator kill switch engaged")
        return JSONResponse({"run_id": run_id, "status": "stopped"})

    @app.delete("/api/runs/{run_id}/stop")
    async def api_run_resume(run_id: str, request: Request) -> JSONResponse:
        """Lift the kill switch. Idempotent."""
        principal = await _require(request, ROLE_EDITOR)
        await request.app.state.store.delete_label("stopped", run_id)
        request.app.state.stopped_runs.discard(run_id)
        await _audit(principal, request, "run_resume", run_id,
                     "operator kill switch lifted")
        return JSONResponse({"run_id": run_id, "status": "resumed"})

    @app.post("/api/redetect")
    async def api_redetect(request: Request) -> JSONResponse:
        """Re-run framework detection over history. When the detector
        learns a new framework, calls captured before that knowledge sit
        as "(unattributed)"; this fills them in. Gaps only: attribution
        set at capture time is never overwritten, and the pass is
        idempotent (a second run finds nothing left to name)."""
        principal = await _require(request, ROLE_EDITOR)
        store = request.app.state.store
        examined = updated = 0
        cursor = None
        while True:
            # Cursor sweep, not first-page-forever: thousands of
            # undetectable rows in front must not hide detectable ones
            # behind them, and strict cursor advance guarantees the loop
            # terminates after visiting every gap row exactly once.
            batch = await store.get_unattributed_calls(limit=500, after=cursor)
            if not batch:
                break
            for row in batch:
                examined += 1
                body = {"system": row.get("system_prompt") or "",
                        "messages": row.get("messages") or []}
                found = detect_agent({}, body)
                if found["framework"] or found["agent_name"]:
                    await store.update_attribution(
                        row["action_id"], found["framework"], found["agent_name"])
                    updated += 1
            cursor = (batch[-1]["timestamp"], batch[-1]["action_id"])
        await _audit(principal, request, "redetect", "-",
                     f"re-ran detection: {updated} of {examined} calls newly attributed")
        return JSONResponse({"examined": examined, "updated": updated})

    @app.put("/api/labels/{scope}/{ref_id}")
    async def api_set_label(scope: str, ref_id: str, request: Request) -> JSONResponse:
        """Name, pin, or file a session/run under a project — ids stay
        stable underneath; only the provided fields change."""
        principal = await _require(request, ROLE_EDITOR)
        if scope not in ("session", "run"):
            raise HTTPException(status_code=400, detail="scope must be session or run")
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        for field, limit in (("name", 120), ("project", 60)):
            val = payload.get(field)
            if val is not None and (not isinstance(val, str) or len(val) > limit):
                raise HTTPException(status_code=400,
                                    detail=f"{field} must be a string ≤ {limit} chars")
        if "pinned" in payload and not isinstance(payload["pinned"], bool):
            raise HTTPException(status_code=400, detail="pinned must be true/false")
        row = await request.app.state.store.set_label(
            scope, ref_id,
            name=payload.get("name"),
            pinned=payload.get("pinned"),
            project=payload.get("project"),
        )
        await _audit(principal, request, "set_label", f"{scope}:{ref_id}",
                     json.dumps({k: payload[k] for k in ("name", "pinned", "project")
                                 if k in payload}))
        return JSONResponse(row)

    @app.get("/api/projects")
    async def api_projects(request: Request) -> JSONResponse:
        await _require(request, ROLE_VIEWER)
        store = request.app.state.store
        rules = await store.get_project_rules()
        bound = {proj: app_id for app_id, proj in rules.items()}
        return JSONResponse({
            "projects": await store.list_projects(),
            "bindings": bound,   # project → app id it auto-files from
        })

    @app.post("/api/projects")
    async def api_create_project(request: Request) -> JSONResponse:
        """Declare a project before anything is filed under it — optionally
        bound to an app id so matching sessions and runs file themselves."""
        principal = await _require(request, ROLE_EDITOR)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 60:
            raise HTTPException(status_code=400,
                                detail="name must be 1-60 characters")
        app_id = payload.get("app_id")
        if app_id is not None and (not isinstance(app_id, str) or len(app_id) > 120):
            raise HTTPException(status_code=400, detail="app_id must be a short string")
        await request.app.state.store.set_label(
            "project", name, name=(app_id.strip() if app_id else None))
        await _audit(principal, request, "create_project", name,
                     f"app_id={app_id or '—'}")
        return JSONResponse({"project": name, "app_id": app_id or None},
                            status_code=201)

    async def _project_sessions(store, name: str) -> list[str]:
        """Every session under a project: hand-filed labels plus app-rule
        matches — the same resolution the views use."""
        labels = await store.get_labels("session")
        rules = await store.get_project_rules()
        bound_apps = {app for app, proj in rules.items() if proj == name}
        out = []
        for srow in await store.list_sessions(limit=10_000):
            sid = srow["session_id"]
            explicit = (labels.get(sid) or {}).get("project")
            if explicit == name or (explicit is None and srow.get("app_id") in bound_apps):
                out.append(sid)
        return out

    @app.put("/api/projects/{name}")
    async def api_rename_project(name: str, request: Request) -> JSONResponse:
        """Rename a project everywhere — filed sessions and runs, the
        declared marker, and its app binding all follow."""
        principal = await _require(request, ROLE_EDITOR)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        new = str(payload.get("name") or "").strip()
        if not new or len(new) > 60:
            raise HTTPException(status_code=400, detail="name must be 1-60 characters")
        store = request.app.state.store
        if name not in await store.list_projects():
            raise HTTPException(status_code=404, detail="project not found")
        moved = await store.rename_project(name, new)
        await _audit(principal, request, "rename_project", name, f"→ {new}")
        return JSONResponse({"project": new, "moved_labels": moved})

    @app.delete("/api/projects/{name}")
    async def api_delete_project(name: str, request: Request,
                                 purge: bool = False) -> JSONResponse:
        """Delete a project. Default: the project vanishes, its sessions
        survive unfiled. purge=true is the destructive form — every session
        under the project (hand-filed or auto-filed) is deleted, calls and
        all. The response says exactly what happened."""
        principal = await _require(request, ROLE_EDITOR)
        store = request.app.state.store
        if name not in await store.list_projects():
            raise HTTPException(status_code=404, detail="project not found")
        deleted_sessions = 0
        deleted_calls = 0
        if purge:
            for sid in await _project_sessions(store, name):
                deleted_calls += await store.delete_session(sid)
                deleted_sessions += 1
        unfiled = await store.unfile_project(name)
        await _audit(principal, request, "delete_project", name,
                     f"purge={purge} sessions_deleted={deleted_sessions} "
                     f"calls_deleted={deleted_calls} unfiled={unfiled}")
        return JSONResponse({"project": name, "purged": purge,
                             "sessions_deleted": deleted_sessions,
                             "calls_deleted": deleted_calls,
                             "labels_unfiled": unfiled})

    @app.get("/api/settings")
    async def api_settings(request: Request) -> JSONResponse:
        """What is this proxy actually running with? Read-only, admin-only,
        secrets masked — answers "why isn't my budget working?" by looking.
        Each row says where its value came from: the config file, an
        environment variable, or the built-in default."""
        await _require(request, ROLE_ADMIN)
        from ..config import applied_from_file, find_config, load_attempted, loaded_path

        def src(env_name: Optional[str]) -> str:
            if env_name is None:
                return "default"
            if env_name in applied_from_file:
                return "file"
            if env_name in os.environ:
                return "env"
            return "default"

        def masked_dsn(value: str) -> str:
            try:
                p = urlparse(value)
                if p.password:
                    return value.replace(f":{p.password}@", ":•••@")
            except Exception:
                pass
            return value

        def key_state(present: bool) -> str:
            return "set (hidden)" if present else "not set"

        def row(section: str, label: str, value, env: Optional[str] = None,
                means: str = "", key: str = "") -> dict:
            """One settings row. `means` explains it in plain words and `key`
            names where to set it — the page should not need a translator."""
            set_with = " · ".join(x for x in (key, env) if x)
            return {"section": section, "label": label,
                    "value": "—" if value is None else str(value),
                    "source": src(env), "means": means, "set_with": set_with}

        try:
            from importlib.metadata import version as _v
            version = _v("agentic-ledger")
        except Exception:
            version = "unknown"
        if ".dev" in version:
            # An editable/source install stamps this at install time, so it
            # can name an old tag while the code is far newer — say so
            # instead of looking like a stale release.
            version += " (dev build — stamped when the package was installed; "\
                       "reinstall to refresh)"
        # Report the file this process actually loaded at startup, not what a
        # fresh search would find now — a file created after start has NOT
        # been read, and saying otherwise recreates the "I set the budget,
        # why is the wall still up?" trap. find_config is only the fallback
        # for embedded uses that never ran apply_config.
        cfg = loaded_path if load_attempted else find_config()
        rows = [
            row("Proxy", "version", version,
                means="Which build is running."),
            row("Proxy", "config file",
                str(cfg) if cfg else "none (using env vars and defaults)",
                means="The file this proxy read at startup, as a full path. "
                      "Searched in order: AGENTICLEDGER_CONFIG, "
                      "./agenticledger.toml, ~/.agenticledger/config.toml.",
                key="AGENTICLEDGER_CONFIG"),
            row("Proxy", "upstream",
                ("auto: anthropic calls → api.anthropic.com, openai-style → "
                 "api.openai.com") if upstream_auto else upstream_url,
                "AGENTICLEDGER_UPSTREAM_URL",
                means="Where your agents' calls are forwarded. Auto means no "
                      "upstream is configured, so each call goes to the "
                      "provider whose wire format it speaks.",
                key="[proxy] upstream_url"),
            row("Proxy", "database", masked_dsn(dsn), "AGENTICLEDGER_DSN",
                means="Where the ledger is stored — a SQLite file or Postgres.",
                key="[proxy] db"),
            row("Proxy", "port", os.environ.get("AGENTICLEDGER_PORT", "8000"),
                "AGENTICLEDGER_PORT",
                means="The port the proxy and dashboard listen on.",
                key="[proxy] port"),
            row("Access", "dashboard key", key_state(_auth_enabled),
                "AGENTICLEDGER_API_KEY",
                means="The key needed to read the ledger. Not set means anyone "
                      "who can reach this port can read everything.",
                key="[keys] api_key / api_key_file"),
            row("Access", "ingest key (relay)",
                key_state(bool(_ingest_key)) + ("" if _ingest_key else " — OPEN RELAY"),
                "AGENTICLEDGER_INGEST_KEY",
                means="The key agents must present to send calls through. Open "
                      "relay means anyone who can reach this port can spend your "
                      "provider credit. Team cards work here too.",
                key="[keys] ingest_key / ingest_key_file"),
            row("Access", "audit log", "on" if _audit_enabled else "off",
                "AGENTICLEDGER_AUDIT_LOG",
                means="Records who viewed, exported, or deleted what."),
            row("Budgets", "daily (whole ledger)", budget_daily,
                "AGENTICLEDGER_BUDGET_DAILY",
                means="Hard ceiling across everything, per UTC day. Enforced "
                      "before the call reaches the provider.",
                key="[budgets] daily"),
            row("Budgets", "per session", budget_session,
                "AGENTICLEDGER_BUDGET_SESSION",
                means="Ceiling for a single session — one chat, one story cycle.",
                key="[budgets] session"),
            row("Budgets", "per agent / day", budget_agent,
                "AGENTICLEDGER_BUDGET_AGENT",
                means="Ceiling per agent name, per UTC day.",
                key="[budgets] agent"),
            row("Budgets", "per user / day", budget_user,
                "AGENTICLEDGER_BUDGET_USER",
                means="Ceiling per user id, per UTC day.",
                key="[budgets] user"),
            row("Budgets", "on breach", f"{budget_action} → HTTP {budget_status}",
                "AGENTICLEDGER_BUDGET_ACTION",
                means="What happens at the wall: block the call or only warn, and "
                      "which answer the agent gets — 429 says 'come back later' "
                      "(with Retry-After), 402 says 'no' and stops retries.",
                key="[budgets] status"),
            row("Capture", "level", _capture_level, "AGENTICLEDGER_CAPTURE_LEVEL",
                means="full stores prompts and answers; metadata stores only the "
                      "numbers (tokens, cost, latency)."),
            row("Capture", "async capture", "on" if _async_capture else "off",
                "AGENTICLEDGER_ASYNC_CAPTURE",
                means="Write records in the background so capturing never adds "
                      "latency to your agent's call."),
            row("Capture", "redaction", "on" if redactor else "off",
                "AGENTICLEDGER_REDACT",
                means="Scrub emails, cards, and keys out of the STORED copy. What "
                      "your agent sends and receives is never modified."),
            row("Capture", "retention (days)", retention_days,
                "AGENTICLEDGER_RETENTION_DAYS",
                means="Delete captured calls older than this many days."),
            row("Loops", "circuit breaker", _loop_action, "AGENTICLEDGER_LOOP_ACTION",
                means="warn flags a stuck loop in the dashboard; block actually "
                      "stops it before it burns more quota."),
            row("Loops", "completion promise", completion_promise or None,
                "AGENTICLEDGER_COMPLETION_PROMISE",
                means="The phrase your loop prints to declare victory — what turns "
                      "a run 'complete' instead of merely 'ended'.",
                key="[proxy] completion_promise"),
            row("Replay", "same-provider replay",
                "on" if replay_api_key else "off", "AGENTICLEDGER_REPLAY_API_KEY",
                means="Key for re-running a captured call on its own provider. The "
                      "proxy never stores your agents' credentials, so replay needs "
                      "its own.",
                key="[replay] api_key / api_key_file"),
        ]
        for prov, tcfg in (replay_targets or {}).items():
            host = urlparse(tcfg["url"]).netloc or tcfg["url"]
            local = host.split(":")[0] in ("localhost", "127.0.0.1")
            rows.append(row("Replay", f"target: {prov}",
                            f"{host}{' (local — free)' if local else ''}",
                            f"AGENTICLEDGER_REPLAY_{prov.upper()}_URL",
                            means=("A local destination — replays here cost nothing."
                                   if local else
                                   f"Replays sent here run on {prov} and cost real tokens."),
                            key=f"[replay] {prov}_url + {prov}_key"))
        rows.append(row("Alerts", "webhook",
                        key_state(bool(_alert_config and _alert_config.webhook_url)),
                        "AGENTICLEDGER_ALERT_WEBHOOK_URL",
                        means="Where alerts are posted — Slack, Discord, PagerDuty. "
                              "Alerts notify after the fact; budgets block before."))
        rows.append(row("Alerts", "daily digest (UTC hour)", digest_hour,
                        "AGENTICLEDGER_DIGEST_HOUR",
                        means="Hour of day to post a last-24h spend summary to the "
                              "webhook above."))
        return JSONResponse({"rows": rows})

    @app.get("/api/calls/{action_id}")
    async def api_get_call(action_id: str, request: Request) -> JSONResponse:
        """One call by id — lets the dashboard follow a replay's
        parent_action_id back to the original call's session."""
        await _require(request, ROLE_VIEWER)
        row = await request.app.state.store.get(action_id)
        if row is None:
            raise HTTPException(status_code=404, detail="action_id not found")
        return JSONResponse(row)

    @app.get("/api/replay/targets")
    async def api_replay_targets(request: Request) -> JSONResponse:
        """Where replays can go — feeds the dashboard's destination dropdown
        with real places instead of wire-format names."""
        await _require(request, ROLE_VIEWER)
        out = []
        for prov, cfg in (replay_targets or {}).items():
            host = urlparse(cfg["url"]).netloc or cfg["url"]
            out.append({"provider": prov, "host": host,
                        "local": host.split(":")[0] in ("localhost", "127.0.0.1")})
        return JSONResponse({"targets": out, "same_provider": bool(replay_api_key)})

    @app.get("/api/replay/models")
    async def api_replay_models(request: Request, provider: str = "") -> JSONResponse:
        """Ask a replay target what models it serves (GET /v1/models), so the
        dashboard can offer the names actually loaded in e.g. LM Studio."""
        await _require(request, ROLE_VIEWER)
        target = (replay_targets or {}).get(provider)
        if not target:
            raise HTTPException(status_code=404, detail=f"no replay target for {provider!r}")
        client = request.app.state.replay_clients[provider]
        try:
            resp = await client.get(
                "/v1/models", headers=replay_auth_headers(provider, target["key"]))
            data = resp.json()
        except Exception:
            return JSONResponse({"models": []})
        models = [m.get("id") for m in (data.get("data") or [])
                  if isinstance(m, dict) and m.get("id")]
        return JSONResponse({"models": models})

    class _ReplayError(Exception):
        """A replay attempt that failed for a describable reason."""
        def __init__(self, status: int, payload: dict):
            self.status, self.payload = status, payload
            super().__init__(payload.get("error"))

    def _resolve_replay_provider(original: dict, model: str, explicit: str) -> str:
        source_provider = original.get("provider")
        provider = explicit.strip() or infer_provider(model)
        if not provider:
            # Unrecognized model name — usually a local one. If the capture's
            # own provider has no way to replay but exactly one target is
            # configured, that target is obviously where this goes.
            targets = replay_targets or {}
            source_can_replay = source_provider in targets or bool(replay_api_key)
            if not source_can_replay and len(targets) == 1:
                provider = next(iter(targets))
            else:
                provider = source_provider
        return provider

    async def _replay_once(app_ref, original: dict, model: str, provider: str,
                           replay_session_id: str) -> dict:
        """Re-execute one captured call and store the result. Raises
        _ReplayError with an HTTP-shaped payload on any describable failure —
        the single-call endpoint maps it to a response, the batch job files
        it as a failed step."""
        source_provider = original.get("provider")
        try:
            if provider == source_provider:
                path, body = build_replay_request(original, model)
            else:
                path, body = build_cross_request(original, model, provider)
        except NotTranslatable as exc:
            raise _ReplayError(400, {"error": f"Not replayable on {provider}: {exc}"}) from None

        target = (replay_targets or {}).get(provider)
        if target:
            client, key = app_ref.state.replay_clients[provider], target["key"]
        elif provider == source_provider and replay_api_key:
            client, key = app_ref.state.client, replay_api_key
        else:
            raise _ReplayError(409, {
                "error": f"No replay target for {provider!r} — pick a provider in "
                         "the replay panel's dropdown, or set "
                         f"AGENTICLEDGER_REPLAY_{provider.upper()}_KEY (and _URL for a "
                         "local server like LM Studio, where any key works)."})

        start = time.time()
        try:
            upstream = await client.post(
                "/" + path, json=body,
                headers=replay_auth_headers(provider, key),
            )
        except Exception as exc:
            where = target["url"] if target else str(client.base_url)
            local = any(h in where for h in ("localhost", "127.0.0.1"))
            hint = (" Is the local server running? (LM Studio: Developer tab → "
                    "Start Server.)") if local else ""
            raise _ReplayError(502, {
                "error": f"Couldn't reach the {provider} replay target at {where} "
                         f"({exc}).{hint}"}) from None
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
            raise _ReplayError(502, payload)
        resp = normalize_response(upstream.json(), latency_ms, model)
        if resp.cost_usd is None:
            resp.cost_usd = compute_cost(
                model, resp.tokens_in or 0, resp.tokens_out or 0,
                cache_read_tokens=resp.cache_read_tokens,
                cache_write_tokens=resp.cache_write_tokens,
                provider=provider or "",
            )
        # The stored replay record carries what was ACTUALLY sent — for a
        # cross-provider replay that is the translated conversation.
        sent_system = body.get("system")
        req = CanonicalRequest(
            messages=body.get("messages") or [], model_id=model,
            provider=provider or "", timestamp=start,
            tools=original.get("tools"),
            system_prompt=sent_system if isinstance(sent_system, str)
                          else original.get("system_prompt"),
            temperature=original.get("temperature"), max_tokens=original.get("max_tokens"),
        )
        new_id = str(uuid.uuid4())
        await app_ref.state.store.save(
            new_id, req, resp,
            session_id=replay_session_id,
            agent_name=original.get("agent_name"),
            framework="replay",
            parent_action_id=original["action_id"],
            environment=original.get("environment") or "development",
        )
        return {
            "action_id": new_id, "model_id": model, "provider": provider,
            "content": resp.content, "tool_calls": resp.tool_calls,
            "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
            "cache_read_tokens": resp.cache_read_tokens,
            "cache_write_tokens": resp.cache_write_tokens,
            "cost_usd": resp.cost_usd, "latency_ms": round(latency_ms, 1),
        }

    _REPLAY_UNCONFIGURED = (
        "Replay is not configured — set AGENTICLEDGER_REPLAY_API_KEY "
        "(same-provider) or AGENTICLEDGER_REPLAY_OPENAI_KEY / "
        "AGENTICLEDGER_REPLAY_ANTHROPIC_KEY (any provider, including "
        "a local LM Studio URL) on the proxy.")

    @app.post("/api/replay")
    async def api_replay(request: Request) -> JSONResponse:
        """Re-execute a captured call using the proxy's replay credentials —
        on the original provider, or translated to the other provider's wire
        format (cross-provider replay); the result is stored as a new call
        linked to the original."""
        principal = await _require(request, ROLE_EDITOR)
        if not replay_api_key and not (replay_targets or {}):
            return JSONResponse({"error": _REPLAY_UNCONFIGURED}, status_code=409)
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

        model = str(payload.get("model") or original["model_id"]).strip()
        provider = _resolve_replay_provider(
            original, model, str(payload.get("provider") or ""))
        try:
            replay = await _replay_once(request.app, original, model, provider,
                                        f"replay-{action_id[:8]}")
        except _ReplayError as exc:
            return JSONResponse(exc.payload, status_code=exc.status)
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
            "replay": replay,
        })

    @app.post("/api/replay/batch")
    async def api_replay_batch(request: Request) -> JSONResponse:
        """Replay a WHOLE run or session on another model — the 0.8 flagship.
        Starts a background job (a batch can take minutes against a local
        model); poll GET /api/replay/jobs/{id} for progress and the report
        card. Each step re-sends the ORIGINAL captured inputs — honest
        moment-by-moment comparison, not a pretend re-run."""
        principal = await _require(request, ROLE_EDITOR)
        if not replay_api_key and not (replay_targets or {}):
            return JSONResponse({"error": _REPLAY_UNCONFIGURED}, status_code=409)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        run_id = str(payload.get("run_id") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        if bool(run_id) == bool(session_id):
            return JSONResponse({"error": "pass exactly one of run_id, session_id"},
                                status_code=400)
        model = str(payload.get("model") or "").strip()
        if not model:
            return JSONResponse({"error": "model is required"}, status_code=400)
        store = request.app.state.store
        calls = (await store.get_run_calls(run_id) if run_id
                 else await store.get_session(session_id))
        # Replays of replays and metering calls are noise, not steps.
        steps = [c for c in calls if c.get("framework") != "replay"]
        if not steps:
            raise HTTPException(status_code=404, detail="no calls found for that scope")
        provider = _resolve_replay_provider(
            steps[0], model, str(payload.get("provider") or ""))

        ref = run_id or session_id
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id, "scope": "run" if run_id else "session", "ref_id": ref,
            "model": model, "provider": provider,
            "replay_session_id": f"replay-{'run' if run_id else 'sess'}-{ref[:8]}",
            "total": len(steps), "done": 0, "status": "running",
            "steps": [], "error": None,
        }
        if not hasattr(request.app.state, "replay_jobs"):
            request.app.state.replay_jobs = {}
        request.app.state.replay_jobs[job_id] = job
        await _audit(principal, request, "replay_batch", ref,
                     f"model={model} steps={len(steps)}")

        app_ref = request.app

        async def _run_job() -> None:
            for original in steps:
                step: dict = {
                    "original_action_id": original["action_id"],
                    "original_model": original.get("model_id"),
                    "original_content": (original.get("content") or "")[:400] or None,
                    "original_cost_usd": original.get("cost_usd"),
                    "original_latency_ms": original.get("latency_ms"),
                }
                reason = replayable_reason(original)
                if reason:
                    step.update(status="skipped", reason=reason)
                else:
                    try:
                        replay = await _replay_once(
                            app_ref, original, model, provider,
                            job["replay_session_id"])
                        score = score_replay(original, replay.get("content"),
                                             replay.get("tool_calls"))
                        step.update(
                            status="ok",
                            replay_action_id=replay["action_id"],
                            replay_content=(replay.get("content") or "")[:400] or None,
                            replay_cost_usd=replay.get("cost_usd"),
                            replay_latency_ms=replay.get("latency_ms"),
                            score=score,
                        )
                    except _ReplayError as exc:
                        step.update(status="failed",
                                    reason=exc.payload.get("error"))
                    except Exception as exc:   # never let one step kill the job
                        step.update(status="failed", reason=str(exc))
                job["steps"].append(step)
                job["done"] += 1
            scored = [st for st in job["steps"] if st.get("score")]
            matched = sum(1 for st in scored if st["score"]["match"])
            job["report"] = {
                "replayed": len(scored),
                "matched": matched,
                "fumbles": [st["original_action_id"] for st in scored
                            if not st["score"]["match"]],
                "skipped": sum(1 for st in job["steps"] if st["status"] == "skipped"),
                "failed": sum(1 for st in job["steps"] if st["status"] == "failed"),
                "original_cost_usd": round(sum(
                    float(st.get("original_cost_usd") or 0) for st in job["steps"]), 6),
                "replay_cost_usd": round(sum(
                    float(st.get("replay_cost_usd") or 0) for st in job["steps"]), 6),
            }
            job["status"] = "done"

        task = asyncio.create_task(_run_job())
        task.add_done_callback(lambda t: t.exception())  # surfaced via job status
        return JSONResponse({"job_id": job_id, "total": job["total"],
                             "provider": provider,
                             "replay_session_id": job["replay_session_id"]},
                            status_code=202)

    async def _rebuild_card(store, replay_session_id: str) -> Optional[dict]:
        """Reconstruct a report card from the ledger itself — replay calls,
        their parent links, and the pure grader are all durable, so a card
        survives proxy restarts even though jobs live in memory. When a
        session holds several batches, the newest replay per original wins."""
        replays = await store.get_session(replay_session_id)
        replays = [c for c in replays if c.get("parent_action_id")]
        if not replays:
            return None
        latest: dict[str, dict] = {}
        for c in replays:                      # timestamp-ordered: last wins
            latest[c["parent_action_id"]] = c
        steps, scope, ref = [], "session", None
        model = replays[-1].get("model_id")
        for orig_id, rep in latest.items():
            orig = await store.get(orig_id)
            if orig is None:
                continue
            if replay_session_id.startswith("replay-run-"):
                scope, ref = "run", orig.get("run_id") or ref
            else:
                scope, ref = "session", orig.get("session_id") or ref
            score = score_replay(orig, rep.get("content"), rep.get("tool_calls"))
            steps.append({
                "original_action_id": orig_id,
                "original_model": orig.get("model_id"),
                "original_content": (orig.get("content") or "")[:400] or None,
                "original_cost_usd": orig.get("cost_usd"),
                "original_latency_ms": orig.get("latency_ms"),
                "status": "ok",
                "replay_action_id": rep["action_id"],
                "replay_content": (rep.get("content") or "")[:400] or None,
                "replay_cost_usd": rep.get("cost_usd"),
                "replay_latency_ms": rep.get("latency_ms"),
                "score": score,
                "_ts": rep.get("timestamp"),
            })
        steps.sort(key=lambda st: st.pop("_ts") or "")
        scored = [st for st in steps if st.get("score")]
        return {
            "job_id": f"db:{replay_session_id}", "scope": scope, "ref_id": ref,
            "model": model, "provider": replays[-1].get("provider"),
            "replay_session_id": replay_session_id,
            "total": len(steps), "done": len(steps), "status": "done",
            "steps": steps, "error": None, "rebuilt": True,
            "report": {
                "replayed": len(scored),
                "matched": sum(1 for st in scored if st["score"]["match"]),
                "fumbles": [st["original_action_id"] for st in scored
                            if not st["score"]["match"]],
                "skipped": 0, "failed": 0,
                "original_cost_usd": round(sum(
                    float(st.get("original_cost_usd") or 0) for st in steps), 6),
                "replay_cost_usd": round(sum(
                    float(st.get("replay_cost_usd") or 0) for st in steps), 6),
            },
        }

    @app.get("/api/replay/jobs")
    async def api_replay_jobs(request: Request, scope: str = "",
                              ref_id: str = "",
                              replay_session_id: str = "") -> JSONResponse:
        """Batch jobs, filterable — lets the panel reopen a finished report
        card, and lets a replay session point back at its comparison."""
        await _require(request, ROLE_VIEWER)
        jobs = list(getattr(request.app.state, "replay_jobs", {}).values())
        if scope:
            jobs = [j for j in jobs if j["scope"] == scope]
        if ref_id:
            jobs = [j for j in jobs if j["ref_id"] == ref_id]
        if replay_session_id:
            jobs = [j for j in jobs if j["replay_session_id"] == replay_session_id]
        # Nothing in memory (e.g. after a restart): rebuild from the ledger.
        if not jobs:
            candidates = []
            if replay_session_id:
                candidates = [replay_session_id]
            elif scope and ref_id:
                candidates = [f"replay-{'run' if scope == 'run' else 'sess'}-{ref_id[:8]}"]
            for cand in candidates:
                card = await _rebuild_card(request.app.state.store, cand)
                if card:
                    jobs = [card]
                    break
        return JSONResponse({"jobs": [
            {**{k: j[k] for k in ("job_id", "scope", "ref_id", "model", "provider",
                                  "status", "done", "total", "replay_session_id")},
             "rebuilt": bool(j.get("rebuilt"))}
            for j in jobs]})

    @app.get("/api/replay/jobs/{job_id}")
    async def api_replay_job(job_id: str, request: Request) -> JSONResponse:
        await _require(request, ROLE_VIEWER)
        job = getattr(request.app.state, "replay_jobs", {}).get(job_id)
        if job is None and job_id.startswith("db:"):
            job = await _rebuild_card(request.app.state.store, job_id[3:])
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return JSONResponse(job)

    @app.get("/api/whatif")
    async def api_whatif(request: Request, model: str,
                         session_id: str = "", run_id: str = "",
                         action_id: str = "") -> JSONResponse:
        """Reprice captured token counts against another model — pure math,
        zero API calls: 'this run on haiku would have cost $0.31'."""
        await _require(request, ROLE_VIEWER)
        scopes = [("session_id", session_id), ("run_id", run_id), ("action_id", action_id)]
        chosen = [(f, v) for f, v in scopes if v.strip()]
        if len(chosen) != 1:
            return JSONResponse(
                {"error": "pass exactly one of session_id, run_id, action_id"},
                status_code=400,
            )
        field, value = chosen[0]
        rows = await request.app.state.store.get_token_rows(field, value.strip())
        if not rows:
            raise HTTPException(status_code=404, detail=f"no calls for {field}={value}")
        result = estimate_whatif(rows, model.strip(), infer_provider(model))
        if result is None:
            return JSONResponse(
                {"error": f"no pricing known for {model!r} — add it via "
                          "AGENTICLEDGER_PRICING or use a model from the built-in table"},
                status_code=400,
            )
        return JSONResponse(result)

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
        teams = raw.get("teams") or []
        if teams:
            # Answer "who ran dry?" at a glance: pair each team's row with its
            # card's daily allowance and what it has spent TODAY (budgets are
            # daily-UTC, independent of the report window).
            budgets = await request.app.state.store.get_team_budgets()
            if budgets:
                day_start = (datetime.datetime.now(datetime.timezone.utc)
                             .replace(hour=0, minute=0, second=0, microsecond=0)
                             .timestamp())
                for row in teams:
                    allowance = budgets.get(row.get("team"))
                    if allowance is None:
                        continue
                    spent = await request.app.state.store.get_team_cost(
                        row["team"], day_start)
                    row["budget_daily"] = allowance
                    row["spent_today"] = round(spent, 4)
                    row["over_budget"] = spent >= allowance
        # By-project rollup: sessions carry their project in the labels
        # table, so join per-session totals with labels here and aggregate.
        labels = await request.app.state.store.get_labels("session")
        rules = await request.app.state.store.get_project_rules()
        run_projects = await _run_project_map(request.app.state.store)
        projects: dict = {}
        if rules or run_projects or any(lab.get("project") for lab in labels.values()):
            for st in await request.app.state.store.get_session_totals(
                    time.time() - days * 86400):
                project = ((labels.get(st["session_id"]) or {}).get("project")
                           or rules.get(st.get("app_id") or "")
                           or run_projects.get(st.get("run_id") or ""))
                if not project:
                    continue
                agg = projects.setdefault(project, {
                    "project": project, "call_count": 0, "session_count": 0,
                    "cost_usd": 0.0, "error_count": 0, "blocked_count": 0})
                agg["call_count"] += st["call_count"]
                agg["session_count"] += 1
                agg["cost_usd"] += float(st["cost_usd"] or 0)
                agg["error_count"] += int(st["error_count"] or 0)
                agg["blocked_count"] += int(st["blocked_count"] or 0)
        return JSONResponse(build_report(
            raw["daily"], raw["models"], raw["agents"], days, teams=teams,
            projects=sorted(projects.values(),
                            key=lambda p: -p["cost_usd"])))

    @app.get("/api/reports.csv")
    async def api_reports_csv(request: Request, days: int = 30,
                              tz_offset_minutes: int = 0) -> Response:
        await _require(request, ROLE_VIEWER)
        days = max(1, min(days, 365))
        tz_offset_minutes = max(-840, min(tz_offset_minutes, 840))
        raw = await request.app.state.store.get_report_aggregates(
            time.time() - days * 86400, tz_offset_minutes=tz_offset_minutes)
        report = build_report(
            raw["daily"], raw["models"], raw["agents"], days)

        import csv
        import io
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow([
            "model_id", "provider", "call_count", "cost_usd", "tokens_in",
            "tokens_out", "cache_read_tokens", "cache_write_tokens",
            "cache_savings_usd", "error_calls", "blocked_calls",
            "p50_latency_ms", "p95_latency_ms", "p99_latency_ms"
        ])
        for m in report["models"]:
            writer.writerow([
                m.get("model_id"),
                m.get("provider"),
                m.get("call_count"),
                m.get("cost_usd"),
                m.get("tokens_in"),
                m.get("tokens_out"),
                m.get("cache_read_tokens"),
                m.get("cache_write_tokens"),
                m.get("cache_savings_usd"),
                m.get("error_calls"),
                m.get("blocked_calls"),
                m.get("p50_latency_ms") if m.get("p50_latency_ms") is not None else "",
                m.get("p95_latency_ms") if m.get("p95_latency_ms") is not None else "",
                m.get("p99_latency_ms") if m.get("p99_latency_ms") is not None else ""
            ])

        return Response(
            content=out.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="agenticledger-models-{days}d.csv"'},
        )

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
            run, loop_run_gap_seconds, explicitly_ended=run["run_id"] in ended,
            stopped=run["run_id"] in request.app.state.stopped_runs))

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

    @app.get("/api/whoami")
    async def api_whoami(request: Request) -> JSONResponse:
        """What is the key I'm holding? Answers for any valid credential —
        including team cards, which can't open the dashboard but deserve a
        clear "this is team X's agent card" instead of a bare 401."""
        if not _auth_enabled:
            return JSONResponse({"auth": False, "role": ROLE_ADMIN, "source": "open",
                                 "name": None, "team": None, "dashboard": True})
        principal = await _authenticate(request)
        if principal is None:
            raise HTTPException(status_code=401, detail="Unauthorized")
        is_card = principal.role == ROLE_INGEST
        return JSONResponse({
            "auth": True,
            "role": principal.role,
            "source": principal.source,
            "name": principal.name,
            "team": principal.name if is_card else None,
            "dashboard": role_satisfies(principal.role, ROLE_VIEWER),
        })

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
            raise HTTPException(status_code=400, detail=f"invalid role: {role!r} (viewer|editor|admin|ingest)")
        budget_daily = body.get("budget_daily")
        if budget_daily is not None:
            if role != ROLE_INGEST:
                raise HTTPException(status_code=400,
                                    detail="budget_daily applies to ingest tokens (team cards)")
            try:
                budget_daily = float(budget_daily)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="budget_daily must be a number") from None
            if budget_daily <= 0:
                raise HTTPException(status_code=400, detail="budget_daily must be positive")
        expires_in_days = body.get("expires_in_days")
        created_at = time.time()
        expires_at = created_at + float(expires_in_days) * 86400 if expires_in_days else None
        raw, token_hash = generate_token()
        token_id = str(uuid.uuid4())
        await request.app.state.store.create_token(
            token_id, name, token_hash, role, created_at, expires_at, budget_daily
        )
        await _audit(principal, request, "create_token", token_id, f"role={role} name={name}")
        # The raw token is returned exactly once; only its hash is stored.
        return JSONResponse({
            "token_id": token_id, "name": name, "role": role,
            "token": raw, "expires_at": expires_at, "budget_daily": budget_daily,
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

    async def _check_ingest_gate(request: Request) -> Optional[JSONResponse]:
        supplied = request.headers.get("x-agenticledger-ingest-key")
        if supplied:
            if _ingest_key and hmac.compare_digest(supplied, _ingest_key):
                return None
            card = await request.app.state.store.get_token_by_hash(hash_token(supplied))
            if card and _token_is_valid(card) and card.get("role") == ROLE_INGEST:
                return None
            # Same rule as the relay: a presented-but-dead credential gets a
            # final 403 (401 invites credential-refresh retry bursts).
            return JSONResponse(
                {"error": {"type": "permission_error",
                           "message": "This Agentic Ledger ingest key or team card "
                                      "is invalid or has been revoked."}},
                status_code=403,
            )
        if _ingest_key:
            return JSONResponse(
                {"error": {"type": "unauthorized",
                           "message": "Missing x-agenticledger-ingest-key."}},
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
        denied = await _check_ingest_gate(request)
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
        denied = await _check_ingest_gate(request)
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
        denied = await _check_ingest_gate(request)
        if denied:
            return denied
        return _otlp_ack(request)

    # ── Transparent proxy ────────────────────────────────────────────────────

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def proxy(request: Request, path: str) -> Response:
        # Proxy-ingest auth: the shared env key or a team card (an
        # ingest-role token) opens the door. A team card additionally
        # attributes every call to its team and can carry its own daily
        # budget — the allowance-card model.
        team_name: Optional[str] = None
        team_budget: Optional[float] = None
        supplied_ingest = request.headers.get("x-agenticledger-ingest-key")
        if supplied_ingest and not (_ingest_key and hmac.compare_digest(supplied_ingest, _ingest_key)):
            card = await request.app.state.store.get_token_by_hash(hash_token(supplied_ingest))
            if card and _token_is_valid(card) and card.get("role") == ROLE_INGEST:
                team_name = card.get("name")
                team_budget = card.get("budget_daily")
            else:
                # A key was presented and it is neither the shared key nor a
                # live card. 403, not 401: 401 means "authenticate again" and
                # sends agents into credential-refresh retry bursts; 403 is
                # final. Enforced even when the relay is otherwise open — a
                # revoked card must not silently pass as anonymous traffic.
                return JSONResponse(
                    {"error": {
                        "type": "permission_error",
                        "message": "This Agentic Ledger ingest key or team card "
                                   "is invalid or has been revoked.",
                    }},
                    status_code=403,
                )
        elif _ingest_key and not supplied_ingest:
            return JSONResponse(
                {"error": {
                    "type": "unauthorized",
                    "message": "Missing x-agenticledger-ingest-key.",
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
        meta["team"] = team_name
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

        # ── Operator kill switch ─────────────────────────────────────────────
        # A stopped run's calls are refused before they cost anything. Same
        # semantics as the budget wall: captured with a "blocked:" detail
        # (amber in the dashboard, never counted as an agent error).
        _stopped_run = meta.get("run_id")
        if (is_llm_path and not is_count_tokens and _stopped_run
                and _stopped_run in request.app.state.stopped_runs):
            reason = (f"run '{_stopped_run}' was stopped by the operator; "
                      "resume it from the dashboard to allow calls")
            try:
                canonical_req = normalize_request(body_json, path)
                blocked_resp = _empty_response(0)
                apply_capture_policy(canonical_req, blocked_resp, _capture_level, _redactor)
                await request.app.state.store.save(
                    action_id, canonical_req, blocked_resp,
                    status_code=budget_status,
                    error_detail=f"blocked: {reason}", **meta,
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
            return JSONResponse(
                {"error": {"type": "run_stopped", "message": reason}},
                status_code=budget_status,
            )

        # ── Budget check ─────────────────────────────────────────────────────
        # Fail open: if the store is unavailable the agent must not be blocked.
        # Budget enforcement resumes automatically once the store recovers.
        _budget_warning: Optional[str] = None  # set in warn mode; carried into actual save
        if is_llm_path and not is_count_tokens and (budget_session is not None or budget_agent is not None or budget_daily is not None or budget_user is not None or team_budget is not None):
            try:
                budget_error, budget_retry_after = await _check_budgets(
                    request.app.state.store, meta,
                    budget_session, budget_agent, budget_daily, budget_user,
                    budget_team=(team_name, team_budget)
                    if team_name and team_budget is not None else None,
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
                        # "blocked:" prefix (like "partial:") marks this as the
                        # ledger's own refusal, not an upstream failure — so
                        # reports can count walls separately from breakage.
                        await request.app.state.store.save(
                            action_id, canonical_req, blocked_resp,
                            status_code=budget_status,
                            error_detail=f"blocked: {budget_error}", **meta,
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
        client = _upstream_client(request.app, path)
        upstream_resp = await client.request(
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
                    error_detail = _classify_failure(
                        status_code, body_json,
                        _extract_error(upstream_resp, f"{request.method} /{path}"))
                    # Hint from the CONFIGURED upstream, not the client's
                    # address; in auto mode the destination matches the knock
                    # by construction, so there is nothing to hint about.
                    mismatch = "" if upstream_auto else wire_format_mismatch(path, upstream_url)
                    if mismatch:
                        error_detail = f"{error_detail} — {mismatch}"
                        _warn_wire_mismatch(mismatch)
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
    client: httpx.AsyncClient = _upstream_client(request.app, path)

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
        # Same promise on the streaming path: name the status and the
        # endpoint even when the error body is empty.
        body_text = raw.decode("utf-8", errors="replace").strip()[:300]
        head = f"upstream {upstream.status_code} on {request.method} /{path}"
        detail = f"{head}: {body_text}" if body_text else f"{head} (no error body)"
        detail = _classify_failure(upstream.status_code, body_json, detail)
        mismatch = wire_format_mismatch(path, str(client.base_url))
        if mismatch:
            detail = f"{detail} — {mismatch}"
            _warn_wire_mismatch(mismatch)
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
    budget_team: Optional[tuple[str, float]] = None,
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

    if budget_team is not None:
        team, cap = budget_team
        since = _today_start_ts()
        spent = await store.get_team_cost(team, since)
        if spent >= cap:
            return (
                f"Team daily budget of ${cap:.4f} exceeded "
                f"(current spend: ${spent:.4f}). Team: {team}",
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


_WIRE_FORMATS = (
    ("anthropic", ("v1/messages", "v1/complete")),
    ("openai", ("v1/chat/completions", "v1/completions", "v1/responses",
                "v1/embeddings")),
)


def _wire_format(path: str) -> str:
    """Which provider dialect a request path speaks ("" when unknown)."""
    p = path.lstrip("/").lower()
    return next((name for name, prefixes in _WIRE_FORMATS
                 if any(p.startswith(pre) for pre in prefixes)), "")


def _upstream_client(app, path: str) -> httpx.AsyncClient:
    """Zero-config routing: Anthropic-format knocks go to the Anthropic
    client when it exists (auto mode); everything else uses the default
    client. Explicit configuration never creates the second client, so an
    explicit upstream always wins."""
    anthropic = getattr(app.state, "client_anthropic", None)
    if anthropic is not None and _wire_format(path) == "anthropic":
        return anthropic
    return app.state.client


def wire_format_mismatch(path: str, upstream_url: str) -> str:
    """The single most confusing misconfiguration: an Anthropic-shaped call
    sent to an OpenAI upstream (or vice versa). The provider answers 404 with
    no body and the agent reports 'that model does not exist', which sends
    people hunting for the wrong problem. Returns a hint, or "" when the pair
    is fine or unknown."""
    sent = _wire_format(path)
    host = (urlparse(upstream_url or "").hostname or "").lower()
    def _is(domain: str) -> bool:
        return host == domain or host.endswith("." + domain)
    serves = ("anthropic" if _is("anthropic.com")
              else "openai" if _is("openai.com") else "")
    if not sent or not serves or sent == serves:
        return ""   # gateways and local servers are none of our business
    return (f"This is a {sent}-format request but the proxy's upstream is "
            f"{upstream_url}, which serves {serves}. Point "
            f"AGENTICLEDGER_UPSTREAM_URL (or [proxy] upstream_url) at the "
            f"right provider — one proxy fronts one provider at a time.")


_warned_mismatch = False


def _warn_wire_mismatch(hint: str) -> None:
    """Say it in the terminal too, once — the person debugging is usually
    watching the log, not the dashboard."""
    global _warned_mismatch
    if not _warned_mismatch:
        _warned_mismatch = True
        logger.warning("Upstream mismatch: %s", hint)


def _is_probe_request(body_json) -> bool:
    """Claude Code's quota probe: one tiny user message, no tools, no system.
    It fails routinely and means nothing about the agent's health."""
    if not isinstance(body_json, dict):
        return False
    msgs = body_json.get("messages")
    if not isinstance(msgs, list) or len(msgs) != 1:
        return False
    m = msgs[0]
    content = m.get("content") if isinstance(m, dict) else None
    return (isinstance(m, dict) and m.get("role") == "user"
            and isinstance(content, str) and len(content) <= 24
            and not body_json.get("tools") and not body_json.get("system"))


def _classify_failure(status: int, body_json, detail: str) -> str:
    """Red should mean 'your agent had a problem'. A failing probe is
    routine; an upstream 429/503/529 is the provider having a moment, which
    clients retry through. Both get prefixes (like 'blocked:') so every
    aggregate can keep them out of the error count while the call itself
    still shows what happened."""
    if _is_probe_request(body_json):
        return f"probe: {detail}"
    if status in (429, 503, 529):
        return f"transient: {detail}"
    return detail


def _extract_error(resp: httpx.Response, where: str = "") -> str:
    """Why did this call fail? Always answerable: the provider's own message
    when it sent one, and otherwise the status and the endpoint that
    produced it. A red badge with no reason is a dead end for the reader."""
    message = ""
    try:
        body = resp.json()
        err = body.get("error", {})
        message = err.get("message") or "" if isinstance(err, dict) else str(err)
    except Exception:
        message = ""
    if not message:
        message = (resp.text or "").strip()[:300]
    head = f"upstream {resp.status_code}" + (f" on {where}" if where else "")
    return f"{head}: {message}" if message else f"{head} (no error body)"


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
                     explicitly_ended: bool = False, stopped: bool = False) -> dict:
    """Derive a runner-facing status from the aggregate row.

    Precedence: operator stop (the kill switch outranks everything: the
    human said stop) → completion promise → flags → an explicit end marker
    (the runner told us the loop exited) → inactivity inference (last call
    older than the run-gap window) → running."""
    promise_seen = bool(run.pop("promise_seen", 0))
    if stopped:
        run["status"] = "stopped"
    elif promise_seen:
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
```