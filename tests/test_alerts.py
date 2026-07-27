"""Tests for agenticledger.proxy.alerts.check_and_fire.

The module fires a webhook POST whenever a configured threshold is breached.
We monkeypatch ``alerts._fire`` with an async collector so nothing touches the
network, build ``CanonicalResponse`` objects via ``normalize.normalize_response``,
and use the in-memory ``store`` fixture for the threshold checks that query
historical state (daily_spend, high_error_rate).

Expected payload (per the module docstring):
    {type, message, value, threshold, action_id, session_id, agent_name, timestamp}
"""

import datetime
import time

import pytest

from agenticledger.proxy import alerts as alerts_mod
from agenticledger.proxy.alerts import AlertConfig, check_and_fire
from agenticledger.proxy.normalize import (
    CanonicalRequest,
    normalize_response,
)

# ── helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture
def fired(monkeypatch):
    """Replace alerts._fire with an async collector; yields the captured payloads.

    Each entry is the dict that would have been POSTed to the webhook.
    """
    captured: list[dict] = []

    async def _collect(url, payload):
        captured.append({"_url": url, **payload})

    monkeypatch.setattr(alerts_mod, "_fire", _collect)
    return captured


def make_config(**overrides):
    """Build an AlertConfig with a webhook URL set (enabled) by default."""
    base = dict(
        webhook_url="https://hooks.example.test/alert",
        cost_per_call=None,
        latency_ms=None,
        error_rate=None,
        daily_spend=None,
    )
    base.update(overrides)
    return AlertConfig(**base)


def make_resp(
    *,
    cost_model: str = "gpt-4o",
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: float = 0.0,
    content: str = "ok",
):
    """Build a CanonicalResponse via normalize_response (OpenAI wire format).

    cost_usd is computed from the pricing table for ``cost_model``.
    """
    body = {
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content},
             "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": tokens_in,
            "completion_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        },
    }
    return normalize_response(body, latency_ms=latency_ms, model_id=cost_model)


def make_request(model_id: str = "gpt-4o", *, timestamp: float | None = None):
    """A minimal CanonicalRequest for store.save."""
    return CanonicalRequest(
        messages=[{"role": "user", "content": "hi"}],
        model_id=model_id,
        provider="openai",
        timestamp=timestamp if timestamp is not None else time.time(),
    )


async def fire(config, store, resp, *, action_id="act-1", session_id="sess-1",
               agent_name="Agent", status_code=200):
    """Convenience wrapper around check_and_fire with sensible defaults."""
    await check_and_fire(
        config, store, resp,
        action_id=action_id,
        session_id=session_id,
        agent_name=agent_name,
        status_code=status_code,
    )


# ── disabled ──────────────────────────────────────────────────────────────────


class TestDisabled:
    async def test_disabled_never_fires_even_when_thresholds_trip(self, fired, store):
        """webhook_url=None disables alerting entirely: _fire is never called."""
        config = make_config(
            webhook_url=None,
            cost_per_call=0.0001,   # would trip
            latency_ms=1.0,         # would trip
        )
        resp = make_resp(cost_model="gpt-4o", tokens_in=1000, tokens_out=1000,
                         latency_ms=99999.0)
        await fire(config, store, resp)
        assert fired == []

    async def test_enabled_property_reflects_webhook_url(self):
        """AlertConfig.enabled is True only when a webhook url is configured."""
        assert make_config(webhook_url=None).enabled is False
        assert make_config(webhook_url="https://x.test").enabled is True


# ── high_cost ─────────────────────────────────────────────────────────────────


class TestHighCost:
    async def test_above_threshold_fires_high_cost(self, fired, store):
        """resp.cost_usd above cost_per_call fires exactly one high_cost alert."""
        resp = make_resp(cost_model="gpt-4o", tokens_in=1_000_000, tokens_out=0)
        # 1M input tokens of gpt-4o = $2.50
        config = make_config(cost_per_call=1.00)
        await fire(config, store, resp)

        assert len(fired) == 1
        alert = fired[0]
        assert alert["type"] == "high_cost"
        assert alert["value"] == resp.cost_usd
        assert alert["threshold"] == 1.00
        assert alert["value"] > alert["threshold"]

    async def test_high_cost_payload_merges_context_fields(self, fired, store):
        """The fired payload carries action_id/session_id/agent_name/timestamp + url."""
        resp = make_resp(cost_model="gpt-4o", tokens_in=1_000_000, tokens_out=0)
        config = make_config(cost_per_call=0.01)
        await fire(config, store, resp, action_id="A1", session_id="S1",
                   agent_name="Planner")

        alert = fired[0]
        assert alert["action_id"] == "A1"
        assert alert["session_id"] == "S1"
        assert alert["agent_name"] == "Planner"
        assert alert["_url"] == config.webhook_url
        assert "message" in alert and isinstance(alert["message"], str)
        # timestamp is an ISO-8601 string parseable as a tz-aware datetime
        ts = datetime.datetime.fromisoformat(alert["timestamp"])
        assert ts.tzinfo is not None

    async def test_below_threshold_no_alert(self, fired, store):
        """resp.cost_usd below cost_per_call fires nothing."""
        resp = make_resp(cost_model="gpt-4o", tokens_in=1000, tokens_out=0)  # tiny
        config = make_config(cost_per_call=100.0)
        await fire(config, store, resp)
        assert fired == []

    async def test_cost_equal_to_threshold_does_not_fire(self, fired, store):
        """Threshold comparison is strict '>': cost exactly at threshold is fine."""
        resp = make_resp(cost_model="gpt-4o", tokens_in=1_000_000, tokens_out=0)
        # exactly $2.50
        config = make_config(cost_per_call=resp.cost_usd)
        await fire(config, store, resp)
        assert fired == []

    async def test_no_cost_threshold_no_alert(self, fired, store):
        """With cost_per_call unset, an expensive call does not fire high_cost."""
        resp = make_resp(cost_model="gpt-4o", tokens_in=10_000_000, tokens_out=0)
        config = make_config(cost_per_call=None)
        await fire(config, store, resp)
        assert fired == []


# ── high_latency ──────────────────────────────────────────────────────────────


class TestHighLatency:
    async def test_above_threshold_fires_high_latency(self, fired, store):
        """resp.latency_ms above latency_ms threshold fires high_latency."""
        resp = make_resp(cost_model="gpt-4o", latency_ms=5000.0)
        config = make_config(latency_ms=1000.0)
        await fire(config, store, resp)

        assert len(fired) == 1
        alert = fired[0]
        assert alert["type"] == "high_latency"
        assert alert["value"] == 5000.0
        assert alert["threshold"] == 1000.0

    async def test_below_threshold_no_alert(self, fired, store):
        """Latency under threshold fires nothing."""
        resp = make_resp(cost_model="gpt-4o", latency_ms=200.0)
        config = make_config(latency_ms=1000.0)
        await fire(config, store, resp)
        assert fired == []

    async def test_latency_equal_to_threshold_does_not_fire(self, fired, store):
        """Strict '>' comparison: latency exactly at threshold does not fire."""
        resp = make_resp(cost_model="gpt-4o", latency_ms=1000.0)
        config = make_config(latency_ms=1000.0)
        await fire(config, store, resp)
        assert fired == []


# ── daily_spend ───────────────────────────────────────────────────────────────


def _today_since() -> float:
    today = datetime.datetime.now(tz=datetime.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return today.timestamp()


class TestDailySpend:
    async def test_period_cost_at_or_above_threshold_fires(self, fired, store):
        """Saved 200-status spend that meets daily_spend fires a daily_spend alert."""
        # Save two calls today, each $2.50 (1M gpt-4o input tokens) -> $5.00 total.
        for i in range(2):
            req = make_request("gpt-4o")
            saved_resp = make_resp(cost_model="gpt-4o", tokens_in=1_000_000, tokens_out=0)
            await store.save(f"a{i}", req, saved_resp, session_id="sd", status_code=200)

        # Sanity: the store agrees spend crossed the threshold.
        assert await store.get_period_cost(_today_since()) >= 5.00

        # The *current* call is cheap and on its own wouldn't trip anything.
        cur = make_resp(cost_model="gpt-4o", tokens_in=10, tokens_out=10)
        config = make_config(daily_spend=5.00)
        await fire(config, store, cur, action_id="cur", session_id="sd")

        spend_alerts = [a for a in fired if a["type"] == "daily_spend"]
        assert len(spend_alerts) == 1
        alert = spend_alerts[0]
        assert alert["threshold"] == 5.00
        assert alert["value"] >= 5.00

    async def test_period_cost_below_threshold_no_alert(self, fired, store):
        """Daily spend under threshold fires no daily_spend alert."""
        req = make_request("gpt-4o")
        saved_resp = make_resp(cost_model="gpt-4o", tokens_in=1000, tokens_out=0)  # cents
        await store.save("a0", req, saved_resp, session_id="sd", status_code=200)

        cur = make_resp(cost_model="gpt-4o", tokens_in=10, tokens_out=10)
        config = make_config(daily_spend=1000.0)
        await fire(config, store, cur, session_id="sd")
        assert [a for a in fired if a["type"] == "daily_spend"] == []

    async def test_error_status_spend_excluded_from_daily_total(self, fired, store):
        """get_period_cost only counts status_code=200 rows, so failed calls don't trip it."""
        # One big-cost call but recorded as a 500 error → excluded from period cost.
        req = make_request("gpt-4o")
        big = make_resp(cost_model="gpt-4o", tokens_in=1_000_000, tokens_out=0)  # $2.50
        await store.save("err", req, big, session_id="sd", status_code=500)

        config = make_config(daily_spend=1.00)
        cur = make_resp(cost_model="gpt-4o", tokens_in=10, tokens_out=10)
        await fire(config, store, cur, session_id="sd")
        assert [a for a in fired if a["type"] == "daily_spend"] == []


# ── high_error_rate ───────────────────────────────────────────────────────────


class TestHighErrorRate:
    async def test_session_error_ratio_meets_threshold_fires(self, fired, store):
        """When the session's error ratio meets error_rate and this call failed, fire."""
        # Session of 4 calls: 2 failures, 2 successes -> ratio 0.5.
        statuses = [200, 500, 200, 500]
        for i, sc in enumerate(statuses):
            req = make_request("gpt-4o")
            resp = make_resp(cost_model="gpt-4o", tokens_in=10, tokens_out=10)
            await store.save(f"e{i}", req, resp, session_id="serr", status_code=sc)

        config = make_config(error_rate=0.5)
        cur = make_resp(cost_model="gpt-4o", tokens_in=1, tokens_out=1)
        # Incoming call is itself an error (status != 200) — required to evaluate.
        await fire(config, store, cur, session_id="serr", status_code=500)

        rate_alerts = [a for a in fired if a["type"] == "high_error_rate"]
        assert len(rate_alerts) == 1
        alert = rate_alerts[0]
        assert alert["threshold"] == 0.5
        assert alert["value"] == pytest.approx(0.5)

    async def test_session_error_ratio_below_threshold_no_alert(self, fired, store):
        """Error ratio under the threshold fires no high_error_rate alert."""
        # 1 failure out of 4 -> ratio 0.25.
        statuses = [200, 200, 200, 500]
        for i, sc in enumerate(statuses):
            req = make_request("gpt-4o")
            resp = make_resp(cost_model="gpt-4o", tokens_in=10, tokens_out=10)
            await store.save(f"e{i}", req, resp, session_id="serr2", status_code=sc)

        config = make_config(error_rate=0.5)
        cur = make_resp(cost_model="gpt-4o", tokens_in=1, tokens_out=1)
        await fire(config, store, cur, session_id="serr2", status_code=500)
        assert [a for a in fired if a["type"] == "high_error_rate"] == []

    async def test_no_alert_when_incoming_call_succeeded(self, fired, store):
        """Even with a high stored error rate, a successful incoming call (200) skips the check."""
        statuses = [500, 500, 500]
        for i, sc in enumerate(statuses):
            req = make_request("gpt-4o")
            resp = make_resp(cost_model="gpt-4o", tokens_in=10, tokens_out=10)
            await store.save(f"e{i}", req, resp, session_id="serr3", status_code=sc)

        config = make_config(error_rate=0.5)
        cur = make_resp(cost_model="gpt-4o", tokens_in=1, tokens_out=1)
        # Incoming call succeeded → error-rate branch is guarded by status_code != 200.
        await fire(config, store, cur, session_id="serr3", status_code=200)
        assert [a for a in fired if a["type"] == "high_error_rate"] == []

    async def test_no_alert_without_session_id(self, fired, store):
        """error_rate alerting requires a session_id to look up the session."""
        config = make_config(error_rate=0.5)
        cur = make_resp(cost_model="gpt-4o", tokens_in=1, tokens_out=1)
        await fire(config, store, cur, session_id=None, status_code=500)
        assert fired == []


# ── multiple breaches at once ─────────────────────────────────────────────────


class TestMultipleBreaches:
    async def test_cost_and_latency_both_fire_separately(self, fired, store):
        """A call that breaches both cost and latency fires two distinct alerts."""
        resp = make_resp(cost_model="gpt-4o", tokens_in=1_000_000, tokens_out=0,
                         latency_ms=9999.0)
        config = make_config(cost_per_call=0.01, latency_ms=1000.0)
        await fire(config, store, resp)

        types = sorted(a["type"] for a in fired)
        assert types == ["high_cost", "high_latency"]
