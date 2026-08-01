"""`agenticledger connect` — nobody memorizes another tool's schema (#65)."""

import json

from agenticledger.connect import connect


def test_connect_claude_code_merges_existing_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"KEEP_ME": "1"}, "other": True}))
    assert connect("claude-code", port="8001") == 0
    cfg = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert cfg["env"]["ANTHROPIC_BASE_URL"] == "http://localhost:8001"
    assert cfg["env"]["KEEP_ME"] == "1"          # merge, not overwrite
    assert cfg["other"] is True
    assert (tmp_path / ".claude" / "settings.json.ledger-bak").is_file()


def test_connect_bmad_tags_the_app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert connect("bmad") == 0
    cfg = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "x-agenticledger-app-id: bmad" in cfg["env"]["ANTHROPIC_CUSTOM_HEADERS"]


def test_connect_openclaw_encodes_the_traps(tmp_path, monkeypatch):
    """Docker detection from the workspace path, host.docker.internal, and
    the {id, name} model objects OpenClaw's validator demands — everything a
    person had to learn by trial and error, encoded once."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    oc = tmp_path / ".openclaw"
    oc.mkdir()
    (oc / "openclaw.json").write_text(json.dumps({
        "agents": {"defaults": {
            "workspace": "/home/node/.openclaw/workspace",
            "models": {"anthropic/claude-opus-4-20250514": {}},
            "model": {"primary": "anthropic/claude-sonnet-4-6"},
        }},
        "gateway": {"port": 18789},
    }))
    assert connect("openclaw", port="8000") == 0
    cfg = json.loads((oc / "openclaw.json").read_text())
    prov = cfg["models"]["providers"]["anthropic"]
    assert prov["baseUrl"] == "http://host.docker.internal:8000/r/openclaw-main/1"
    ids = [m["id"] for m in prov["models"]]
    assert "claude-opus-4-20250514" in ids and "claude-sonnet-4-6" in ids
    assert all(m.get("name") for m in prov["models"])   # the validator's demand
    assert (oc / "openclaw.json.ledger-bak").is_file()
    assert cfg["gateway"]["port"] == 18789               # rest untouched


def test_connect_openclaw_native_install_uses_loopback(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    oc = tmp_path / ".openclaw"
    oc.mkdir()
    (oc / "openclaw.json").write_text(json.dumps({
        "agents": {"defaults": {"workspace": str(tmp_path / "ws")}}}))
    assert connect("openclaw") == 0
    cfg = json.loads((oc / "openclaw.json").read_text())
    assert cfg["models"]["providers"]["anthropic"]["baseUrl"].startswith("http://127.0.0.1:8000")


def test_connect_openclaw_without_install_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert connect("openclaw") == 1
    assert "is OpenClaw installed" in capsys.readouterr().out
