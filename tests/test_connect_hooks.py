"""`agenticledger connect claude-code --hooks` writes the ledger's http
hooks into the user's Claude Code settings, keeps everything else there,
replaces its own entries on re-run, and takes them out on --remove-hooks."""

import json

from agenticledger import connect as connect_mod
from agenticledger.connect import HOOK_EVENTS, connect


def test_hooks_are_written_kept_and_removed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(connect_mod.Path, "home", classmethod(lambda cls: tmp_path))
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        "env": {"FOO": "bar"},
        "hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}]},
    }))
    assert connect("claude-code", port="8010", hooks=True) == 0
    cfg = json.loads(settings.read_text())
    assert cfg["env"] == {"FOO": "bar"}                       # untouched
    for event in HOOK_EVENTS:
        ours = [h for g in cfg["hooks"][event] for h in g["hooks"] if h.get("_agenticledger")]
        assert len(ours) == 1 and ours[0]["type"] == "http"
        assert ours[0]["url"] == "http://localhost:8010/v1/hooks/claude-code"
        assert ours[0]["allowedEnvVars"] == ["AGENTICLEDGER_INGEST_KEY"]
    # the user's own PostToolUse hook survives beside ours
    theirs = [h for g in cfg["hooks"]["PostToolUse"] for h in g["hooks"] if h.get("type") == "command"]
    assert theirs == [{"type": "command", "command": "echo hi"}]
    assert "PreToolUse" not in cfg["hooks"]
    # re-running replaces, never duplicates
    assert connect("claude-code", port="8010", hooks=True) == 0
    cfg = json.loads(settings.read_text())
    ours = [h for g in cfg["hooks"]["Stop"] for h in g["hooks"] if h.get("_agenticledger")]
    assert len(ours) == 1
    # removal leaves only the user's hook
    assert connect("claude-code", remove_hooks=True) == 0
    cfg = json.loads(settings.read_text())
    assert list(cfg["hooks"].keys()) == ["PostToolUse"]
    assert cfg["hooks"]["PostToolUse"][0]["hooks"] == [{"type": "command", "command": "echo hi"}]
    out = capsys.readouterr().out
    assert "Removed the ledger's hooks" in out
