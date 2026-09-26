"""Fleet refusal controls (0.15, "The wall holds"): one global emergency
stop, model and provider allow and deny lists for the fleet and per team
card, every refusal on the record (rate-limit and loop refusals included),
and loop blocks liftable without a restart. Refuse only, reason named."""

import httpx2 as httpx
import pytest

from agenticledger.proxy.policy import Policy, check_policies, parse_list, refusal_type
from agenticledger.proxy.ratelimit import RateLimitConfig

from .conftest import anthropic_response, openai_response

MASTER = {"x-agenticledger-api-key": "master-key"}


def _call(client, session="fleet-session", model="gpt-4o", messages=None, **headers):
    return client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": messages or [{"role": "user", "content": "hi"}]},
        headers={"x-agenticledger-session-id": session, **headers},
    )


def _blocked_rows(client, session):
    rows = client.get(f"/session/{session}").json()
    return [r for r in rows if (r.get("error_detail") or "").startswith("blocked:")]


def _metric(client, name):
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(name + " ") or line.startswith(name + "{"):
            yield line


# ── the lists themselves ─────────────────────────────────────────────────────

def test_parse_list_accepts_strings_and_sequences():
    assert parse_list(None) == ()
    assert parse_list("") == ()
    assert parse_list(" claude-* , gpt-4o,, ") == ("claude-*", "gpt-4o")
    assert parse_list(["a", " b ", ""]) == ("a", "b")


def test_deny_wins_and_allow_admits_only_what_it_names():
    fleet = Policy.from_values(allow_models="gpt-4o*, claude-*", deny_models="*-preview")
    assert fleet.check("gpt-4o", "openai") is None
    assert fleet.check("GPT-4O-MINI", "openai") is None          # case-insensitive globs
    assert "not on the fleet's allow list" in fleet.check("o1", "openai")
    # Denied even though the allow list would admit it: deny wins.
    reason = fleet.check("claude-4-preview", "anthropic")
    assert "deny list (rule '*-preview')" in reason
    assert refusal_type(reason) == "model_not_allowed"


def test_provider_rules_come_first_and_name_themselves():
    fleet = Policy.from_values(allow_providers="anthropic", deny_providers="bedrock")
    assert fleet.check("claude-sonnet-5", "anthropic") is None
    reason = fleet.check("claude-sonnet-5", "bedrock")
    assert reason == "provider 'bedrock' is on the fleet's deny list (rule 'bedrock')"
    assert refusal_type(reason) == "provider_not_allowed"
    assert fleet.check("gpt-4o", "openai") == \
        "provider 'openai' is not on the fleet's allow list (anthropic)"


def test_a_card_narrows_the_fleet_and_never_widens_it():
    fleet = Policy.from_values(allow_models="gpt-*")
    card = Policy.from_card({"name": "marketing", "allow_models": "gpt-4o",
                             "deny_models": None, "allow_providers": None,
                             "deny_providers": None})
    assert check_policies("gpt-4o", "openai", fleet, card) is None
    # The card's own rule, named as the team's.
    assert "team 'marketing''s allow list" in check_policies("gpt-4o-mini", "openai", fleet, card)
    # A card that would admit claude cannot: the fleet rule fires first.
    wide = Policy.from_card({"name": "research", "allow_models": "claude-*"})
    assert "the fleet's allow list" in check_policies("claude-sonnet-5", "anthropic", fleet, wide)
    assert Policy.from_card({"name": "plain"}) is None


def test_long_allow_lists_are_spelled_out_briefly():
    fleet = Policy.from_values(allow_models=",".join(f"m{i}" for i in range(9)))
    reason = fleet.check("other", "openai")
    assert "m0, m1, m2, m3, m4, m5 and 3 more" in reason


# ── stop all calls ───────────────────────────────────────────────────────────

def test_stop_all_refuses_every_call_and_files_it_blocked(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    assert _call(client, session="s-a").status_code == 200

    state = client.post("/api/stop").json()
    assert state["calls_stopped"] is True and state["since"] and state["by"]

    for session in ("s-a", "s-b"):
        refused = _call(client, session=session)
        assert refused.status_code == 429
        body = refused.json()["error"]
        assert body["type"] == "calls_stopped"
        assert "stop all calls" in body["message"] and "DELETE /api/stop" in body["message"]
        blocked = _blocked_rows(client, session)
        assert len(blocked) == 1 and "stopped by" in blocked[0]["error_detail"]
    # Only the first call reached the upstream.
    assert len(client.upstream.requests) == 1

    assert client.get("/health").json()["calls_stopped"] is True
    assert client.get("/api/stop").json()["calls_stopped"] is True
    assert 'agenticledger_refusals_total{reason="calls_stopped"} 2' in \
        list(_metric(client, "agenticledger_refusals_total"))
    assert list(_metric(client, "agenticledger_calls_stopped")) == ["agenticledger_calls_stopped 1"]

    # Lifted: calls flow again, the answer says so, the state is clean.
    assert client.delete("/api/stop").json()["calls_stopped"] is False
    assert _call(client, session="s-a").status_code == 200
    assert client.get("/health").json()["calls_stopped"] is False
    actions = [r["action"] for r in client.get("/api/audit").json()]
    assert "stop_all" in actions and "resume_all" in actions


def test_stop_all_is_idempotent_and_keeps_the_first_operator(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    first = client.post("/api/stop").json()
    again = client.post("/api/stop").json()
    assert again == first
    assert client.delete("/api/stop").json()["calls_stopped"] is False
    assert client.delete("/api/stop").json()["calls_stopped"] is False


def test_stop_all_survives_a_restart(proxy, tmp_path):
    dsn = f"sqlite:///{tmp_path / 'fleet.db'}"
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()), dsn=dsn)
    client.post("/api/stop")

    reborn = proxy(handler=lambda r: httpx.Response(200, json=openai_response()), dsn=dsn)
    assert reborn.get("/health").json()["calls_stopped"] is True
    assert _call(reborn).status_code == 429
    assert reborn.get("/api/stop").json()["by"]
    reborn.delete("/api/stop")
    assert _call(reborn).status_code == 200


def test_stop_all_leaves_count_tokens_and_the_api_alone(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json={"input_tokens": 3}),
                   upstream_url="https://api.anthropic.com")
    client.post("/api/stop")
    counted = client.post("/v1/messages/count_tokens",
                          json={"model": "claude-sonnet-5",
                                "messages": [{"role": "user", "content": "hi"}]})
    assert counted.status_code == 200
    assert client.get("/api/runs").status_code == 200


def test_stop_all_refuses_replay_too(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   replay_api_key="replay-key")
    client.post("/api/stop")
    refused = client.post("/api/replay", json={"action_id": "whatever"})
    assert refused.status_code == 409
    assert refused.json()["error"].startswith("Replay refused: all calls are stopped")
    assert "replay_refused" in [r["action"] for r in client.get("/api/audit").json()]


def test_stop_all_names_the_operator_behind_a_token(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    minted = client.post("/api/tokens", json={"name": "oncall-dana", "role": "editor"},
                         headers=MASTER).json()
    state = client.post("/api/stop",
                        headers={"x-agenticledger-token": minted["token"]}).json()
    assert state["by"] == "oncall-dana"
    refused = _call(client)
    assert "stopped by oncall-dana" in refused.json()["error"]["message"]


# ── allow and deny lists at the wall ─────────────────────────────────────────

def test_fleet_deny_list_refuses_with_the_rule_named_and_records_it(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   policy=Policy.from_values(deny_models="gpt-4o-*"))
    assert _call(client, model="gpt-4o").status_code == 200
    refused = _call(client, model="gpt-4o-mini")
    assert refused.status_code == 403
    assert refused.json()["error"] == {
        "type": "model_not_allowed",
        "message": "model 'gpt-4o-mini' is on the fleet's deny list (rule 'gpt-4o-*')",
    }
    assert len(client.upstream.requests) == 1
    blocked = _blocked_rows(client, "fleet-session")
    assert len(blocked) == 1 and blocked[0]["status_code"] == 403
    assert blocked[0]["model_id"] == "gpt-4o-mini"
    assert 'agenticledger_refusals_total{reason="model_not_allowed"} 1' in \
        list(_metric(client, "agenticledger_refusals_total"))


def test_fleet_allow_list_admits_only_what_it_names(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   policy=Policy.from_values(allow_models="gpt-4o, claude-*"))
    assert _call(client, model="gpt-4o").status_code == 200
    refused = _call(client, model="o3")
    assert refused.status_code == 403
    assert "not on the fleet's allow list (gpt-4o, claude-*)" in refused.json()["error"]["message"]


def test_provider_lists_judge_the_resolved_provider(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=anthropic_response()),
                   upstream_url="https://api.anthropic.com",
                   policy=Policy.from_values(deny_providers="anthropic"))
    refused = client.post("/v1/messages",
                          json={"model": "claude-sonnet-5", "max_tokens": 10,
                                "messages": [{"role": "user", "content": "hi"}]},
                          headers={"x-agenticledger-session-id": "prov"})
    assert refused.status_code == 403
    assert refused.json()["error"]["type"] == "provider_not_allowed"
    assert "provider 'anthropic' is on the fleet's deny list" in refused.json()["error"]["message"]
    assert client.upstream.requests == []
    assert 'agenticledger_refusals_total{reason="provider_not_allowed"} 1' in \
        list(_metric(client, "agenticledger_refusals_total"))


def test_team_cards_narrow_the_fleet_lists_and_cannot_widen_them(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   policy=Policy.from_values(allow_models="gpt-*"))
    narrow = client.post("/api/tokens", headers=MASTER, json={
        "name": "marketing", "role": "ingest", "allow_models": ["gpt-4o"]}).json()
    assert narrow["allow_models"] == ["gpt-4o"] and narrow["deny_models"] == []
    wide = client.post("/api/tokens", headers=MASTER, json={
        "name": "research", "role": "ingest", "allow_models": "claude-*, gpt-*"}).json()

    ok = _call(client, model="gpt-4o", **{"x-agenticledger-ingest-key": narrow["token"]})
    assert ok.status_code == 200
    refused = _call(client, model="gpt-4o-mini",
                    **{"x-agenticledger-ingest-key": narrow["token"]})
    assert refused.status_code == 403
    assert "team 'marketing''s allow list (gpt-4o)" in refused.json()["error"]["message"]
    # The wide card's own list would admit claude; the fleet's does not.
    refused = _call(client, model="claude-sonnet-5",
                    **{"x-agenticledger-ingest-key": wide["token"]})
    assert refused.status_code == 403
    assert "the fleet's allow list (gpt-*)" in refused.json()["error"]["message"]
    # Refusals wear the team, so Reports can count them per card.
    rows = client.get("/session/fleet-session", headers=MASTER).json()
    teams = {r["team"] for r in rows if (r.get("error_detail") or "").startswith("blocked:")}
    assert teams == {"marketing", "research"}
    # The lists are on the card's listing, for the admin who set them.
    listed = {t["name"]: t for t in client.get("/api/tokens", headers=MASTER).json()}
    assert listed["marketing"]["allow_models"] == "gpt-4o"
    assert listed["research"]["allow_models"] == "claude-*,gpt-*"


def test_token_api_validates_card_lists(proxy, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    viewer = client.post("/api/tokens", headers=MASTER,
                         json={"name": "v", "role": "viewer", "deny_models": ["x"]})
    assert viewer.status_code == 400 and "team cards" in viewer.json()["detail"]
    bad = client.post("/api/tokens", headers=MASTER,
                      json={"name": "t", "role": "ingest", "allow_models": {"a": 1}})
    assert bad.status_code == 400
    mixed = client.post("/api/tokens", headers=MASTER,
                        json={"name": "t", "role": "ingest", "allow_models": ["a", 3]})
    assert mixed.status_code == 400
    empty = client.post("/api/tokens", headers=MASTER,
                        json={"name": "t", "role": "ingest", "allow_models": " , "}).json()
    assert empty["allow_models"] == []
    listed = {t["name"]: t for t in client.get("/api/tokens", headers=MASTER).json()}
    assert listed["t"]["allow_models"] is None


def test_settings_page_states_the_fleet_lists(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   policy=Policy.from_values(allow_models="gpt-*", deny_providers="bedrock"))
    rows = {r["label"]: r for r in client.get("/api/settings").json()["rows"]
            if r["section"] == "Policy"}
    assert rows["allowed models"]["value"] == "gpt-*"
    assert rows["denied providers"]["value"] == "bedrock"
    assert rows["denied models"]["value"] == "—"
    assert "[policy] allow_models" in rows["allowed models"]["set_with"]


# ── every refusal on the record ──────────────────────────────────────────────

def test_rate_limit_refusals_are_recorded_and_counted(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   rate_limit_config=RateLimitConfig(session_rpm=1))
    assert _call(client, session="rl").status_code == 200
    refused = _call(client, session="rl")
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "60"
    assert refused.json()["error"]["type"] == "rate_limit_exceeded"
    blocked = _blocked_rows(client, "rl")
    assert len(blocked) == 1
    assert blocked[0]["status_code"] == 429 and blocked[0]["cost_usd"] in (0, None)
    assert "rate limit" in blocked[0]["error_detail"].lower()
    assert 'agenticledger_refusals_total{reason="rate_limit_exceeded"} 1' in \
        list(_metric(client, "agenticledger_refusals_total"))
    # The refused knock did not consume quota: the window still holds one call.
    assert client.get("/api/sessions").json()[0]["blocked_count"] == 1


def _chained(n):
    """Messages for the n-th call of one thread: each call extends the last."""
    msgs = [{"role": "user", "content": "start"}]
    for i in range(1, n):
        msgs += [{"role": "assistant", "content": f"step {i}"},
                 {"role": "user", "content": f"go on {i}"}]
    return msgs


def test_loop_block_refusal_is_recorded_and_liftable_without_a_restart(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()),
                   loop_action="block", loop_max_steps=2)
    sid = "looping"
    assert _call(client, session=sid, messages=_chained(1)).status_code == 200
    assert _call(client, session=sid, messages=_chained(2)).status_code == 200
    assert client.get(f"/api/sessions/{sid}/loop-block").json()["blocked"] is True

    refused = _call(client, session=sid, messages=_chained(3))
    assert refused.status_code == 429
    assert refused.json()["error"]["type"] == "loop_detected"
    assert f"DELETE /api/sessions/{sid}/loop-block" in refused.json()["error"]["message"]
    blocked = _blocked_rows(client, sid)
    assert len(blocked) == 1 and "Loop guard" in blocked[0]["error_detail"]
    assert 'agenticledger_refusals_total{reason="loop_detected"} 1' in \
        list(_metric(client, "agenticledger_refusals_total"))

    lifted = client.delete(f"/api/sessions/{sid}/loop-block").json()
    assert lifted == {"session_id": sid, "lifted": True, "blocked": False}
    assert client.get(f"/api/sessions/{sid}/loop-block").json()["blocked"] is False
    assert "loop_block_lift" in [r["action"] for r in client.get("/api/audit").json()]

    # The guard re-armed from now: a fresh two-step budget, then the wall again.
    assert _call(client, session=sid, messages=_chained(3)).status_code == 200
    assert _call(client, session=sid, messages=_chained(4)).status_code == 200
    assert _call(client, session=sid, messages=_chained(5)).status_code == 429
    # Lifting an unblocked or unknown session is honest about it.
    assert client.delete("/api/sessions/nobody/loop-block").json()["lifted"] is False


def test_kill_switch_and_ceiling_refusals_are_counted_on_the_same_rail(proxy):
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    _call(client, session="ks", **{"x-agenticledger-run-id": "night"})
    client.post("/api/runs/night/stop")
    assert _call(client, session="ks", **{"x-agenticledger-run-id": "night"}).status_code == 429
    assert 'agenticledger_refusals_total{reason="run_stopped"} 1' in \
        list(_metric(client, "agenticledger_refusals_total"))
    # Every reason exists at zero before it fires.
    assert 'agenticledger_refusals_total{reason="budget_exceeded"} 0' in \
        list(_metric(client, "agenticledger_refusals_total"))


@pytest.mark.parametrize("path", ["/api/stop", "/api/sessions/x/loop-block"])
def test_controls_need_an_editor(proxy, path, monkeypatch):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    viewer = client.post("/api/tokens", headers=MASTER,
                         json={"name": "v", "role": "viewer"}).json()["token"]
    assert client.get(path, headers={"x-agenticledger-token": viewer}).status_code == 200
    assert client.delete(path, headers={"x-agenticledger-token": viewer}).status_code == 403
    assert client.post("/api/stop", headers={"x-agenticledger-token": viewer}).status_code == 403
    assert client.post("/api/stop").status_code == 401
