"""
Reliable notifications (#123): one door every alert leaves through.

Every webhook the ledger sends (threshold alerts, loop flags, ceiling
warnings, run summaries, the daily digest, a test) goes through the
Notifier, which gives them what a one-shot POST never had:

* retries with backoff, off the request path (a slow Slack never delays
  a capture), and a bounded number of attempts;
* deduplication by key inside a window, so a crossed daily budget says so
  once and not on every call after it;
* a delivery history in the store, so the dashboard can show what was
  sent, whether it landed, and why it did not;
* native payloads for Slack incoming webhooks, Discord webhooks and
  PagerDuty Events v2, chosen from the URL or forced with
  AGENTICLEDGER_ALERT_FORMAT, with the plain JSON payload as the default;
* a dashboard link on every event that concerns a run or a session.

The plain payload keeps the field names the README has always
documented; the native ones carry the same facts in the shape each
service reads.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import quote

import httpx2 as httpx

logger = logging.getLogger("agenticledger.notify")

FORMATS = ("auto", "generic", "slack", "discord", "pagerduty")

# Backoff between attempts, in seconds. Three attempts, thirteen seconds
# end to end: enough to ride out a blip, short enough that a dead
# endpoint is reported as failed while the operator is still looking.
DEFAULT_BACKOFF = (1.0, 3.0, 9.0)

# What each event type means to a pager. Everything else is a warning.
_SEVERITY = {
    "run_failed": "critical", "run_blocked": "critical", "budget_exceeded": "critical",
    "calls_stopped": "critical",
    "run_complete": "info", "run_ended": "info", "daily_digest": "info", "test": "info",
}

# One plain sentence per type for the native heading; the message carries
# the detail.
_TITLES = {
    "high_cost": "Expensive call",
    "high_latency": "Slow call",
    "high_error_rate": "Session error rate",
    "daily_spend": "Daily spend",
    "budget_exceeded": "Budget breached",
    "run_ceiling_approaching": "Run ceiling approaching",
    "loop_flag": "Loop flagged",
    "run_complete": "Run complete",
    "run_ended": "Run ended",
    "run_failed": "Run failed",
    "run_blocked": "Run blocked at the wall",
    "daily_digest": "Daily digest",
    "test": "Test notification",
}

# Post primitive: (url, payload) -> (ok, error). Swappable in tests.
PostFn = Callable[[str, dict], Awaitable[tuple[bool, Optional[str]]]]


async def post_json(url: str, payload: dict) -> tuple[bool, Optional[str]]:
    """One HTTP POST. Never raises; the Notifier decides what a failure means."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    return True, None


@dataclass
class NotifyConfig:
    webhook_url: Optional[str] = None
    format: str = "auto"                # auto | generic | slack | discord | pagerduty
    pagerduty_key: Optional[str] = None  # Events v2 routing key (integration key)
    public_url: Optional[str] = None    # where the dashboard is reachable, for links

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)


def detect_format(url: Optional[str], chosen: str = "auto") -> str:
    """The wire shape for a webhook: the operator's choice, else what the
    host says. Unknown hosts get the plain JSON payload."""
    chosen = (chosen or "auto").strip().lower()
    if chosen in FORMATS and chosen != "auto":
        return chosen
    host = (url or "").lower()
    if "hooks.slack.com" in host:
        return "slack"
    if "discord.com/api/webhooks" in host or "discordapp.com/api/webhooks" in host:
        return "discord"
    if "events.pagerduty.com" in host:
        return "pagerduty"
    return "generic"


def event_link(public_url: Optional[str], event: dict) -> Optional[str]:
    """The dashboard page for what the event is about, if it is about a
    run or a session. public_url is the operator's, so the link works from
    a phone; without it the link is the local address, which still works
    on the ledger's machine."""
    base = (public_url or "").rstrip("/")
    if not base:
        return None
    run_id = event.get("run_id")
    if run_id:
        return f"{base}/app#/runs/{quote(str(run_id), safe='')}"
    session_id = event.get("session_id")
    if session_id:
        return f"{base}/app#/sessions/{quote(str(session_id), safe='')}"
    return None


def _text_of(event: dict) -> str:
    return str(event.get("message") or event.get("text") or event.get("type") or "notification")


def render(fmt: str, event: dict, *, pagerduty_key: Optional[str] = None,
           dedup_key: Optional[str] = None) -> dict:
    """The event in the shape the destination reads. The plain payload is
    the event itself (with its link); the native ones say the same thing
    in the service's own fields."""
    kind = str(event.get("type") or "notification")
    title = _TITLES.get(kind, kind.replace("_", " "))
    text = _text_of(event)
    link = event.get("url")
    if fmt == "slack":
        body = f"*{title}*\n{text}"
        if link:
            body += f"\n<{link}|Open in Agentic Ledger>"
        return {
            "text": f"{title}: {text}",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": body}}],
        }
    if fmt == "discord":
        embed: dict[str, Any] = {"title": title, "description": text[:4000]}
        if link:
            embed["url"] = link
        return {"content": f"{title}: {text}"[:2000], "embeds": [embed]}
    if fmt == "pagerduty":
        details = {k: v for k, v in event.items() if k not in ("message", "text", "url")}
        payload: dict[str, Any] = {
            "routing_key": pagerduty_key or "",
            "event_action": "trigger",
            "payload": {
                "summary": f"{title}: {text}"[:1024],
                "severity": _SEVERITY.get(kind, "warning"),
                "source": "agenticledger",
                "component": "agentic-ledger",
                "custom_details": details,
            },
        }
        if dedup_key:
            payload["dedup_key"] = dedup_key[:255]
        if link:
            payload["links"] = [{"href": link, "text": "Open in Agentic Ledger"}]
        return payload
    return dict(event)


@dataclass
class Notifier:
    """The one door. `send` records the notification, hands delivery to a
    background task with retries, and returns at once; `flush` waits for
    the stragglers at shutdown. With no store the history is skipped
    (unit tests, the legacy direct path); with no webhook nothing is sent
    and nothing is recorded."""

    config: NotifyConfig
    store: Any = None
    post: PostFn = post_json
    backoff: tuple[float, ...] = DEFAULT_BACKOFF
    clock: Callable[[], float] = time.time
    _tasks: set = field(default_factory=set)
    _recent: dict = field(default_factory=dict)   # dedupe key -> last sent (clock)
    delivered: int = 0
    failed: int = 0
    suppressed: int = 0

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def format(self) -> str:
        return detect_format(self.config.webhook_url, self.config.format)

    async def send(self, event: dict, *, key: Optional[str] = None,
                   dedupe_seconds: float = 0.0, wait: bool = False) -> Optional[dict]:
        """Queue one notification. Returns the history row (status pending,
        or the final row when wait=True), or None when disabled or
        suppressed as a duplicate."""
        if not self.enabled:
            return None
        now = self.clock()
        if key and dedupe_seconds > 0:
            last = self._recent.get(key)
            if last is not None and now - last < dedupe_seconds:
                self.suppressed += 1
                return None
            self._recent[key] = now
            if len(self._recent) > 5000:
                oldest = sorted(self._recent, key=self._recent.get)[:1000]
                for k in oldest:
                    self._recent.pop(k, None)
        event = dict(event)
        event.setdefault("timestamp", datetime.datetime.fromtimestamp(
            now, tz=datetime.timezone.utc).isoformat())
        link = event_link(self.config.public_url, event)
        if link and "url" not in event:
            event["url"] = link
        row = {
            "id": str(uuid.uuid4()),
            "timestamp": now,
            "type": str(event.get("type") or "notification"),
            "target_kind": ("run" if event.get("run_id") else
                            "session" if event.get("session_id") else None),
            "target_id": event.get("run_id") or event.get("session_id"),
            "summary": _text_of(event)[:300],
            "format": self.format,
            "status": "pending",
            "attempts": 0,
            "delivered_at": None,
            "error": None,
            "key": key,
        }
        if self.store is not None:
            with suppress(Exception):
                await self.store.add_notification(row)
        if wait:
            return await self._deliver(row, event, key)
        task = asyncio.get_running_loop().create_task(self._deliver(row, event, key))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return row

    async def _deliver(self, row: dict, event: dict, key: Optional[str]) -> dict:
        fmt = self.format
        if fmt == "pagerduty" and not self.config.pagerduty_key:
            return await self._finish(row, False,
                                      "PagerDuty needs AGENTICLEDGER_ALERT_PAGERDUTY_KEY "
                                      "(the Events v2 integration key)")
        payload = render(fmt, event, pagerduty_key=self.config.pagerduty_key, dedup_key=key)
        error: Optional[str] = None
        for attempt, delay in enumerate(self.backoff, start=1):
            row["attempts"] = attempt
            ok, error = await self.post(self.config.webhook_url or "", payload)
            if ok:
                return await self._finish(row, True, None)
            if attempt < len(self.backoff):
                await asyncio.sleep(delay)
        return await self._finish(row, False, error or "delivery failed")

    async def _finish(self, row: dict, ok: bool, error: Optional[str]) -> dict:
        row["status"] = "delivered" if ok else "failed"
        row["delivered_at"] = self.clock() if ok else None
        row["error"] = None if ok else (error or "")[:300]
        if ok:
            self.delivered += 1
        else:
            self.failed += 1
            logger.warning("Notification %s (%s) failed after %d attempts: %s",
                           row["id"], row["type"], row["attempts"], row["error"])
        if self.store is not None:
            with suppress(Exception):
                await self.store.update_notification(
                    row["id"], status=row["status"], attempts=row["attempts"],
                    delivered_at=row["delivered_at"], error=row["error"])
        return row

    async def flush(self, timeout: float = 15.0) -> None:
        """Wait for in-flight deliveries (shutdown). Never raises."""
        if not self._tasks:
            return
        with suppress(Exception):
            await asyncio.wait_for(
                asyncio.gather(*list(self._tasks), return_exceptions=True), timeout=timeout)
