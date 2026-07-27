"""
Anomaly detection and webhook alerts.

Agentic Ledger fires a POST to AGENTICLEDGER_ALERT_WEBHOOK_URL whenever a
threshold is breached. The payload is plain JSON — wire it to Slack,
PagerDuty, Discord, or your own endpoint on your side.

Thresholds (all optional):
    AGENTICLEDGER_ALERT_WEBHOOK_URL    URL to POST alerts to
    AGENTICLEDGER_ALERT_COST_PER_CALL  Alert if a single call costs more than $X
    AGENTICLEDGER_ALERT_LATENCY_MS     Alert if a single call takes longer than Xms
    AGENTICLEDGER_ALERT_ERROR_RATE     Alert if session error rate exceeds X (0.0–1.0)
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

Slack example — create an incoming webhook and set:
    AGENTICLEDGER_ALERT_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
The `message` field maps to Slack's `text` field automatically if you use
a Slack workflow that reads the JSON body.
"""

import datetime
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from .normalize import CanonicalResponse

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
) -> None:
    """Check all thresholds after a call is saved. Fire webhook for any breach."""
    if not config.enabled:
        return

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
        await _fire(config.webhook_url, {
            **alert,
            "action_id":  action_id,
            "session_id": session_id,
            "agent_name": agent_name,
            "timestamp":  datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
        })


async def _fire(url: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                logger.warning("Alert webhook returned %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("Alert webhook failed: %s", exc)


def _today_start_ts() -> float:
    today = datetime.datetime.now(tz=datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return today.timestamp()
