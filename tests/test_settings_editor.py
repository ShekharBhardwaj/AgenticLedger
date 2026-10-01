"""Settings you can change from the dashboard: the editor writes the same
config file `agenticledger config set` does, says when a restart is needed
and when an environment variable keeps winning, and the restart endpoint
re-executes the proxy in place."""

import time

import httpx2 as httpx

from agenticledger import config as config_mod

from .conftest import openai_response

MASTER = {"x-agenticledger-api-key": "master-key"}


def _client(proxy, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config_mod, "loaded_path", None)
    return proxy(handler=lambda r: httpx.Response(200, json=openai_response()))


def test_rows_say_which_key_sets_them_and_what_the_choices_are(proxy, monkeypatch, tmp_path):
    client = _client(proxy, monkeypatch, tmp_path)
    body = client.get("/api/settings").json()
    rows = {r["label"]: r for r in body["rows"]}
    assert rows["circuit breaker"]["config_key"] == "loops.action"
    assert rows["circuit breaker"]["choices"] == ["warn", "block", "off"]
    assert rows["telemetry calls"]["config_key"] == "proxy.telemetry_calls"
    assert rows["dashboard key"]["config_key"] == "keys.api_key"
    assert rows["dashboard key"]["secret"] is True
    assert rows["unpriced models"]["choices"] == ["allow", "refuse"]
    assert body["restart_required"] is False
    assert body["config_path"].endswith("config.toml")


def test_set_writes_the_file_and_asks_for_a_restart(proxy, monkeypatch, tmp_path):
    client = _client(proxy, monkeypatch, tmp_path)
    resp = client.put("/api/config", json={"key": "proxy.telemetry_calls", "value": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["value"] == "true" and body["restart_required"] is True and body["env_wins"] is False
    target = tmp_path / ".agenticledger" / "config.toml"
    assert target.is_file() and "telemetry_calls = true" in target.read_text()
    assert config_mod.get_value("proxy.telemetry_calls", str(target)) == "True"
    listing = client.get("/api/config").json()
    assert listing["values"]["proxy.telemetry_calls"] == "True"
    assert listing["restart_required"] is True and listing["exists"] is True
    assert client.get("/api/settings").json()["restart_required"] is True
    # Clearing comments the line out again.
    assert client.put("/api/config", json={"key": "proxy.telemetry_calls", "value": None}).status_code == 200
    assert config_mod.get_value("proxy.telemetry_calls", str(target)) is None
    actions = [(r["action"], r["target"], r["details"]) for r in client.get("/api/audit").json()
               if r["action"] == "config_set"]
    assert ("config_set", "proxy.telemetry_calls", "set to true") in actions
    assert ("config_set", "proxy.telemetry_calls", "cleared") in actions


def test_secrets_are_written_but_never_echoed(proxy, monkeypatch, tmp_path):
    client = _client(proxy, monkeypatch, tmp_path)
    resp = client.put("/api/config", json={"key": "keys.api_key", "value": "s3cret-value"})
    assert resp.json()["value"] == "set (hidden)"
    assert "s3cret-value" in (tmp_path / ".agenticledger" / "config.toml").read_text()
    assert client.get("/api/config").json()["values"]["keys.api_key"] == "set (hidden)"
    trail = [r for r in client.get("/api/audit").json() if r["action"] == "config_set"]
    assert trail and "s3cret-value" not in (trail[0]["details"] or "")
    assert trail[0]["details"] == "set (hidden)"


def test_validation_and_the_env_wins_warning(proxy, monkeypatch, tmp_path):
    client = _client(proxy, monkeypatch, tmp_path)
    assert client.put("/api/config", json={"key": "nope.key", "value": "x"}).status_code == 400
    assert client.put("/api/config", json={"key": "loops.action", "value": "maybe"}).status_code == 400
    assert client.put("/api/config", json={"key": "env.AGENTICLEDGER_X", "value": "x"}).status_code == 400
    assert client.put("/api/config", json={"key": "loops.action", "value": ["a"]}).status_code == 400
    monkeypatch.setenv("AGENTICLEDGER_LOOP_ACTION", "block")
    body = client.put("/api/config", json={"key": "loops.action", "value": "warn"}).json()
    assert body["env_wins"] is True and body["env"] == "AGENTICLEDGER_LOOP_ACTION"


def test_restart_runs_the_hook_after_answering(proxy, monkeypatch, tmp_path):
    client = _client(proxy, monkeypatch, tmp_path)
    fired = []
    client.app.state.restart_hook = lambda: fired.append(time.time())
    assert client.post("/api/restart").json() == {"restarting": True}
    deadline = time.time() + 5
    while not fired and time.time() < deadline:
        client.portal.call(__import__("asyncio").sleep, 0.1)
    assert fired, "the restart hook never ran"
    assert "restart" in [r["action"] for r in client.get("/api/audit").json()]


def test_editing_needs_an_admin(proxy, monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    monkeypatch.setenv("HOME", str(tmp_path))
    client = proxy(handler=lambda r: httpx.Response(200, json=openai_response()))
    viewer = client.post("/api/tokens", headers=MASTER,
                         json={"name": "v", "role": "viewer"}).json()["token"]
    v = {"x-agenticledger-token": viewer}
    assert client.get("/api/config", headers=v).status_code == 403
    assert client.put("/api/config", headers=v, json={"key": "loops.action", "value": "warn"}).status_code == 403
    assert client.post("/api/restart", headers=v).status_code == 403
    assert client.get("/api/config", headers=MASTER).status_code == 200
