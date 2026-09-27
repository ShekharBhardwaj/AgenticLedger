"""Reliable notifications (#123): one door for every webhook, with retries,
deduplication, a delivery history, native payloads, end-of-run summaries,
and a test button."""

import asyncio
import json

import httpx2 as httpx
import pytest

from agenticledger.proxy.notify import (
    Notifier,
    NotifyConfig,
    detect_format,
    event_link,
    render,
)

from .conftest import openai_response


class _Sink:
    """A post primitive that records payloads and answers as told."""

    def __init__(self, answers=None):
        self.calls: list[tuple[str, dict]] = []
        self.answers = list(answers or [])

    async def __call__(self, url, payload):
        self.calls.append((url, payload))
        if self.answers:
            return self.answers.pop(0)
        return True, None


def _notifier(sink, **cfg):
    cfg.setdefault("webhook_url", "https://hooks.example.test/x")
    return Notifier(NotifyConfig(**cfg), post=sink, backoff=(0.0, 0.0, 0.0))


# ── shapes ───────────────────────────────────────────────────────────────────

def test_format_is_read_from_the_host_unless_forced():
    assert detect_format("https://hooks.slack.com/services/T/B/x") == "slack"
    assert detect_format("https://discord.com/api/webhooks/1/abc") == "discord"
    assert detect_format("https://events.pagerduty.com/v2/enqueue") == "pagerduty"
    assert detect_format("https://relay.example.test/hook") == "generic"
    assert detect_format("https://hooks.slack.com/services/x", "generic") == "generic"
    assert detect_format(None, "nonsense") == "generic"


def test_links_point_at_the_run_or_the_session():
    assert event_link("https://ledger.example/", {"run_id": "night loop"}) == \
        "https://ledger.example/app#/runs/night%20loop"
    assert event_link("https://ledger.example", {"session_id": "s1"}) == \
        "https://ledger.example/app#/sessions/s1"
    assert event_link("https://ledger.example", {"type": "daily_digest"}) is None
    assert event_link(None, {"run_id": "r"}) is None


def test_native_payloads_carry_the_same_facts():
    event = {"type": "run_failed", "message": "Run r ended badly", "run_id": "r",
             "url": "https://l/app#/runs/r", "call_count": 3}
    slack = render("slack", event)
    assert slack["text"] == "Run failed: Run r ended badly"
    assert "<https://l/app#/runs/r|Open in Agentic Ledger>" in slack["blocks"][0]["text"]["text"]
    discord = render("discord", event)
    assert discord["content"].startswith("Run failed:")
    assert discord["embeds"][0]["url"] == "https://l/app#/runs/r"
    pd = render("pagerduty", event, pagerduty_key="RK", dedup_key="run_failed:r")
    assert pd["routing_key"] == "RK" and pd["event_action"] == "trigger"
    assert pd["payload"]["severity"] == "critical"
    assert pd["payload"]["custom_details"]["call_count"] == 3
    assert pd["dedup_key"] == "run_failed:r"
    assert pd["links"][0]["href"] == "https://l/app#/runs/r"
    assert render("generic", event) == event
    # Anything unknown is a warning to a pager, and a digest is informational.
    assert render("pagerduty", {"type": "high_cost", "message": "m"}, pagerduty_key="k")[
        "payload"]["severity"] == "warning"
    assert render("pagerduty", {"type": "daily_digest", "text": "t"}, pagerduty_key="k")[
        "payload"]["severity"] == "info"


# ── delivery ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delivery_retries_and_then_lands():
    sink = _Sink([(False, "HTTP 502: bad gateway"), (False, "ConnectError: x"), (True, None)])
    n = _notifier(sink)
    row = await n.send({"type": "test", "message": "hi"}, wait=True)
    assert row["status"] == "delivered" and row["attempts"] == 3
    assert n.delivered == 1 and n.failed == 0
    assert len(sink.calls) == 3


@pytest.mark.asyncio
async def test_delivery_gives_up_with_the_last_error():
    sink = _Sink([(False, "HTTP 500: a"), (False, "HTTP 500: b"), (False, "HTTP 500: c")])
    n = _notifier(sink)
    row = await n.send({"type": "test", "message": "hi"}, wait=True)
    assert row["status"] == "failed" and row["attempts"] == 3
    assert row["error"] == "HTTP 500: c"
    assert n.failed == 1


@pytest.mark.asyncio
async def test_duplicates_are_suppressed_inside_the_window():
    sink = _Sink()
    now = [1000.0]
    n = Notifier(NotifyConfig(webhook_url="https://h.test/x"), post=sink,
                 backoff=(0.0,), clock=lambda: now[0])
    assert await n.send({"type": "daily_spend"}, key="daily_spend:d", dedupe_seconds=600, wait=True)
    assert await n.send({"type": "daily_spend"}, key="daily_spend:d", dedupe_seconds=600, wait=True) is None
    assert n.suppressed == 1
    now[0] += 601
    assert await n.send({"type": "daily_spend"}, key="daily_spend:d", dedupe_seconds=600, wait=True)
    assert len(sink.calls) == 2


@pytest.mark.asyncio
async def test_disabled_sends_nothing_and_pagerduty_needs_its_key():
    sink = _Sink()
    assert await _notifier(sink, webhook_url=None).send({"type": "test"}, wait=True) is None
    assert sink.calls == []
    pd = _notifier(sink, webhook_url="https://events.pagerduty.com/v2/enqueue")
    row = await pd.send({"type": "test", "message": "m"}, wait=True)
    assert row["status"] == "failed" and "AGENTICLEDGER_ALERT_PAGERDUTY_KEY" in row["error"]
    assert sink.calls == []


@pytest.mark.asyncio
async def test_background_delivery_is_off_the_caller_path_and_flushed():
    gate = asyncio.Event()
    seen = []

    async def slow_post(url, payload):
        await gate.wait()
        seen.append(payload)
        return True, None

    n = Notifier(NotifyConfig(webhook_url="https://h.test/x"), post=slow_post, backoff=(0.0,))
    row = await n.send({"type": "test", "message": "m"})
    assert row["status"] == "pending" and seen == []
    gate.set()
    await n.flush()
    assert seen and n.delivered == 1


# ── through the app ──────────────────────────────────────────────────────────

def _sink_app(proxy, monkeypatch, **kw):
    """A proxy whose notifier posts into a list instead of the network."""
    sink = _Sink(kw.pop("answers", None))
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   notify_config=NotifyConfig(webhook_url="https://hooks.slack.com/services/T/B/x",
                                              public_url="https://ledger.example"),
                   **kw)
    notifier = client.app.state.notifier
    notifier.post = sink
    notifier.backoff = (0.0, 0.0, 0.0)
    return client, sink, notifier


def _call(client, session="n-session", run_id=None):
    headers = {"x-agenticledger-session-id": session}
    if run_id:
        headers["x-agenticledger-run-id"] = run_id
    return client.post("/v1/chat/completions",
                       json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
                       headers=headers)


def _drain(client):
    """Let background deliveries finish inside the test client's loop."""
    client.portal.call(client.app.state.notifier.flush)


def test_history_and_the_test_button(proxy, monkeypatch):
    client, sink, notifier = _sink_app(proxy, monkeypatch)
    listing = client.get("/api/notifications").json()
    assert listing == {"enabled": True, "format": "slack", "rows": []}
    sent = client.post("/api/notifications/test").json()
    assert sent["sent"] is True and sent["format"] == "slack"
    assert sent["row"]["status"] == "delivered" and sent["row"]["attempts"] == 1
    url, payload = sink.calls[0]
    assert "hooks.slack.com" in url and payload["text"].startswith("Test notification:")
    rows = client.get("/api/notifications").json()["rows"]
    assert len(rows) == 1 and rows[0]["type"] == "test" and rows[0]["status"] == "delivered"
    assert rows[0]["timestamp"].endswith("+00:00")
    assert "notify_test" in [r["action"] for r in client.get("/api/audit").json()]
    metrics = client.get("/metrics").text
    assert 'agenticledger_notifications_total{outcome="delivered"} 1' in metrics


def test_the_test_button_says_when_nothing_is_configured(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    out = client.post("/api/notifications/test").json()
    assert out["sent"] is False and "AGENTICLEDGER_ALERT_WEBHOOK_URL" in out["reason"]
    assert client.get("/api/notifications").json()["enabled"] is False


def test_a_failed_delivery_is_on_the_record_with_its_error(proxy, monkeypatch):
    client, sink, notifier = _sink_app(
        proxy, monkeypatch, answers=[(False, "HTTP 404: gone")] * 3)
    sent = client.post("/api/notifications/test").json()
    assert sent["sent"] is False and sent["row"]["error"] == "HTTP 404: gone"
    row = client.get("/api/notifications").json()["rows"][0]
    assert row["status"] == "failed" and row["attempts"] == 3
    assert 'agenticledger_notifications_total{outcome="failed"} 1' in client.get("/metrics").text


def test_a_run_stopped_at_the_wall_is_announced_once_with_a_link(proxy, monkeypatch):
    client, sink, notifier = _sink_app(proxy, monkeypatch)
    assert _call(client, run_id="night").status_code == 200
    client.post("/api/runs/night/stop")
    for _ in range(3):
        assert _call(client, run_id="night").status_code == 429
    _drain(client)
    blocked = [p for _, p in sink.calls if "Run blocked" in p["text"]]
    assert len(blocked) == 1, [p["text"] for _, p in sink.calls]
    assert "https://ledger.example/app#/runs/night" in blocked[0]["blocks"][0]["text"]["text"]
    rows = client.get("/api/notifications").json()["rows"]
    assert [r for r in rows if r["type"] == "run_blocked"][0]["target_id"] == "night"


def test_rate_limit_refusals_stay_in_the_ledger_only(proxy, monkeypatch):
    from agenticledger.proxy.ratelimit import RateLimitConfig
    client, sink, notifier = _sink_app(proxy, monkeypatch,
                                       rate_limit_config=RateLimitConfig(session_rpm=1))
    assert _call(client).status_code == 200
    assert _call(client).status_code == 429
    _drain(client)
    assert sink.calls == []


def test_end_of_run_summaries_ended_and_failed(proxy, monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        if "fail" in body["messages"][-1]["content"]:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, json=openai_response())

    sink = _Sink()
    client = proxy(handler=handler, loop_run_gap_seconds=0.2,
                   notify_config=NotifyConfig(webhook_url="https://relay.example.test/hook"))
    notifier = client.app.state.notifier
    notifier.post = sink
    notifier.backoff = (0.0,)
    assert _call(client, session="ok-1", run_id="calm").status_code == 200
    calm_fail = client.post("/v1/chat/completions",
                            json={"model": "gpt-4o",
                                  # Long enough not to read as a quota probe.
                                  "messages": [{"role": "user", "content":
                                                "please fail this call for the test"}]},
                            headers={"x-agenticledger-session-id": "bad-1",
                                     "x-agenticledger-run-id": "stormy"})
    assert calm_fail.status_code == 500
    # Still live: nothing announced.
    assert client.portal.call(client.app.state.run_watch_once, client.app) == []
    import time as _t
    _t.sleep(0.3)
    announced = client.portal.call(client.app.state.run_watch_once, client.app)
    assert sorted(announced) == ["calm", "stormy"]
    _drain(client)
    kinds = {p["run_id"]: p["type"] for _, p in sink.calls}
    assert kinds == {"calm": "run_ended", "stormy": "run_failed"}
    assert all("Run" in p["message"] and "calls" in p["message"] for _, p in sink.calls)
    # A second pass is silent, and so is a reboot: the history remembers.
    assert client.portal.call(client.app.state.run_watch_once, client.app) == []
    assert client.app.state.run_notified >= {"calm", "stormy"}


def test_threshold_alerts_ride_the_same_door_and_daily_spend_says_it_once(proxy, monkeypatch):
    from agenticledger.proxy.alerts import AlertConfig
    client, sink, notifier = _sink_app(
        proxy, monkeypatch,
        alert_config=AlertConfig(webhook_url="https://hooks.slack.com/services/T/B/x",
                                 cost_per_call=None, latency_ms=None, error_rate=None,
                                 daily_spend=0.0000001))
    for _ in range(3):
        assert _call(client).status_code == 200
    _drain(client)
    spend = [p for _, p in sink.calls if p["text"].startswith("Daily spend:")]
    assert len(spend) == 1
    assert notifier.suppressed == 2


def test_notifications_need_a_viewer_and_the_test_an_editor(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    assert client.get("/api/notifications").status_code == 401
    assert client.post("/api/notifications/test").status_code == 401
    master = {"x-agenticledger-api-key": "master-key"}
    viewer = client.post("/api/tokens", headers=master,
                         json={"name": "v", "role": "viewer"}).json()["token"]
    assert client.get("/api/notifications",
                      headers={"x-agenticledger-token": viewer}).status_code == 200
    assert client.post("/api/notifications/test",
                       headers={"x-agenticledger-token": viewer}).status_code == 403
