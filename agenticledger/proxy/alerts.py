"""
Threshold alerts over a webhook.

Agentic Ledger posts to AGENTICLEDGER_ALERT_WEBHOOK_URL whenever one of the
fixed thresholds below is crossed (there is no baseline or anomaly model).
Delivery goes through notify.Notifier: retries with backoff off the request
path, deduplication, a delivery history, and native payloads for Slack,
Discord and PagerDuty (plain JSON with our own field names otherwise).

Thresholds (all optional):
    AGENTICLEDGER_ALERT_WEBHOOK_URL    URL to POST alerts to
    AGENTICLEDGER_ALERT_COST_PER_CALL  Alert if a single call costs more than $X
    AGENTICLEDGER_ALERT_LATENCY_MS     Alert if a single call takes longer than Xms
    AGENTICLEDGER_ALERT_ERROR_RATE     Alert if session error rate exceeds X (0.0 to 1.0)
    AGENTICLEDGER_ALERT_DAILY_SPEND    Alert (not block) when daily spend crosses $X

Payload sent to the webhook:
    {
        "type":       "high_cost" | "high_latency" | "high_error_rate" | "daily_spend",
        "message":    "Human-readable description",
        "value":      <actual value that triggered the alert>,
        "threshold":  <configured threshold>,
        "action_id":  "...",
        "session_id": "...",
        "agent_name": "...",
        "timestamp":  "2026-04-03T12:00:00+00:00"
    }

Slack incoming webhooks, Discord webhooks and PagerDuty Events v2 are
recognised from the URL (or forced with AGENTICLEDGER_ALERT_FORMAT) and get
their native shape; see notify.render.
"""

import datetime
import logging
from dataclasses import dataclass
from typing import Optional

import httpx2 as httpx

from .normalize import CanonicalResponse
from .notify import Notifier, NotifyConfig

logger = logging.getLogger(__name__)


@dataclass
class AlertConfig:
    webhook_url: Optional[str]
    cost_per_call: Optional[float]    # USD
    latency_ms: Optional[float]       # milliseconds
    error_rate: Optional[float]       # 0.0–1.0
    daily_spend: Optional[float]      # USD

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)


async def check_and_fire(
    config: AlertConfig,
    store,                          # Store — imported at call site to avoid circular
    resp: CanonicalResponse,
    action_id: str,
    session_id: Optional[str],
    agent_name: Optional[str],
    status_code: int,
    notifier: Optional[Notifier] = None,
) -> None:
    """Check all thresholds after a call is saved and hand every breach to
    the notifier. Without one (direct callers, tests) a bare notifier posts
    through _fire and waits for the delivery."""
    if not config.enabled:
        return
    wait = notifier is None
    if notifier is None:
        notifier = Notifier(NotifyConfig(webhook_url=config.webhook_url), post=_post)

    alerts = []

    # ── Cost per call ─────────────────────────────────────────────────────────
    if config.cost_per_call and resp.cost_usd and resp.cost_usd > config.cost_per_call:
        alerts.append({
            "type":      "high_cost",
            "message":   f"Single call cost ${resp.cost_usd:.4f} exceeded threshold ${config.cost_per_call:.4f}",
            "value":     resp.cost_usd,
            "threshold": config.cost_per_call,
        })

    # ── Latency ───────────────────────────────────────────────────────────────
    if config.latency_ms and resp.latency_ms and resp.latency_ms > config.latency_ms:
        alerts.append({
            "type":      "high_latency",
            "message":   f"Call latency {resp.latency_ms:.0f}ms exceeded threshold {config.latency_ms:.0f}ms",
            "value":     resp.latency_ms,
            "threshold": config.latency_ms,
        })

    # ── Session error rate ────────────────────────────────────────────────────
    if config.error_rate and session_id and status_code != 200:
        try:
            calls = await store.get_session(session_id)
            if calls:
                errors = sum(1 for c in calls if (c.get("status_code") or 200) != 200)
                rate = errors / len(calls)
                if rate >= config.error_rate:
                    alerts.append({
                        "type":      "high_error_rate",
                        "message":   f"Session error rate {rate:.0%} ({errors}/{len(calls)} calls failed) exceeded threshold {config.error_rate:.0%}",
                        "value":     rate,
                        "threshold": config.error_rate,
                    })
        except Exception:
            pass

    # ── Daily spend ───────────────────────────────────────────────────────────
    if config.daily_spend:
        try:
            since = _today_start_ts()
            spent = await store.get_period_cost(since)
            if spent >= config.daily_spend:
                alerts.append({
                    "type":      "daily_spend",
                    "message":   f"Daily spend ${spent:.4f} crossed alert threshold ${config.daily_spend:.4f}",
                    "value":     spent,
                    "threshold": config.daily_spend,
                })
        except Exception:
            pass

    for alert in alerts:
        # Per-call alerts are unique by nature; a session's error rate and
        # the day's spend would otherwise repeat on every call after the
        # line is crossed, so they say it once per window.
        kind = alert["type"]
        if kind == "high_error_rate":
            key, window = f"high_error_rate:{session_id}", 600.0
        elif kind == "daily_spend":
            key, window = f"daily_spend:{datetime.date.today().isoformat()}", 86400.0
        else:
            key, window = f"{kind}:{action_id}", 0.0
        await notifier.send({
            **alert,
            "action_id":  action_id,
            "session_id": session_id,
            "agent_name": agent_name,
            "timestamp":  datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        }, key=key, dedupe_seconds=window, wait=wait)


async def _fire(url: str, payload: dict) -> None:
    """One direct POST, kept for callers and tests that patch it; the
    Notifier is the door the app uses."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                logger.warning("Alert webhook returned %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Alert webhook failed: %s", exc)


async def _post(url: str, payload: dict) -> tuple[bool, Optional[str]]:
    """The bare notifier's post primitive: _fire, looked up at call time
    so a test that patches alerts._fire still sees every payload."""
    await _fire(url, payload)
    return True, None


def _today_start_ts() -> float:
    today = datetime.datetime.now(tz=datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return today.timestamp()
