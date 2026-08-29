"""agenticledger doctor — the reasoning is pure functions, tested here
without a real machine's mess (the real machine supplied the spec)."""

import os
import stat

from agenticledger.doctor import Install, _shebang, diagnose, find_installs


def _script(dirpath, shebang="#!/usr/bin/python3"):
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / "agenticledger"
    p.write_text(f"{shebang}\nprint('hi')\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def test_shebang_plain_and_env_forms(tmp_path):
    a = _script(tmp_path / "a", "#!/opt/venv/bin/python")
    b = _script(tmp_path / "b", "#!/usr/bin/env python3.12")
    assert str(_shebang(a)) == "/opt/venv/bin/python"
    assert str(_shebang(b)) == "python3.12"


def test_find_installs_orders_like_the_shell(tmp_path):
    first = _script(tmp_path / "one")
    second = _script(tmp_path / "two")
    path = os.pathsep.join([str(first.parent), str(second.parent)])
    found = find_installs(path)
    assert [i.script for i in found] == [first, second]
    assert found[0].wins and not found[1].wins


def test_find_installs_dedupes_symlinked_copies(tmp_path):
    real = _script(tmp_path / "real")
    linkdir = tmp_path / "link"
    linkdir.mkdir()
    (linkdir / "agenticledger").symlink_to(real)
    path = os.pathsep.join([str(linkdir), str(real.parent)])
    assert len(find_installs(path)) == 1


def _inst(script, wins=False, version=None, error=None):
    from pathlib import Path
    return Install(script=Path(script), interpreter=Path("/py"), wins=wins,
                   version=version, error=error)


def test_broken_shadow_names_the_working_install():
    verdicts = diagnose(
        [_inst("/sys/agenticledger", wins=True, error="ImportError: boom"),
         _inst("/real/agenticledger", version="1.0")],
        {"running": True, "version": "1.0"})
    text = " ".join(verdicts)
    assert "THE ONE YOUR SHELL RUNS" in text
    assert "shadowing the working install" in text
    assert "pip uninstall -y agentic-ledger" in text


def test_version_disagreement_between_working_installs():
    verdicts = diagnose(
        [_inst("/a/agenticledger", wins=True, version="2.0"),
         _inst("/b/agenticledger", version="1.0")],
        {"running": True, "version": "2.0"})
    assert any("two working installs disagree" in v for v in verdicts)


def test_stale_running_version_asks_for_restart():
    verdicts = diagnose(
        [_inst("/a/agenticledger", wins=True, version="2.0")],
        {"running": True, "version": "1.0"})
    assert any("restart applies it" in v for v in verdicts)


def test_healthy_machine_has_no_verdicts():
    assert diagnose(
        [_inst("/a/agenticledger", wins=True, version="2.0")],
        {"running": True, "version": "2.0"}) == []


def test_empty_path_says_install():
    assert "pip install agentic-ledger" in diagnose([], {"running": False})[0]


def test_effective_port_prefers_the_recorded_truth(tmp_path, monkeypatch):
    """status/doctor probe the port the service RECORDED at start, not the
    probing shell's config — a service on a non-default port read as down
    from any other terminal (found live)."""
    from agenticledger import service
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "proxy.pid")
    monkeypatch.setattr(service, "_read_pid", lambda: 12345)
    monkeypatch.setattr(service, "_alive", lambda pid: True)
    (tmp_path / "proxy.port").write_text("8003")
    assert service.effective_port() == 8003
    # Dead service: fall back to this shell's configuration.
    monkeypatch.setattr(service, "_alive", lambda pid: False)
    monkeypatch.setattr(service, "_port", lambda: 8000)
    assert service.effective_port() == 8000


def test_named_instances_have_their_own_state(tmp_path, monkeypatch):
    """#108: a named instance moves EVERY state path into its directory,
    including the default database, so two ledgers never share state."""
    from agenticledger import service
    monkeypatch.setattr(service, "STATE_DIR", tmp_path)
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "proxy.pid")
    service.use_instance("demo")
    try:
        assert tmp_path / "instances" / "demo" / "proxy.pid" == service.PID_FILE
        assert service.LOG_FILE.parent == service.PID_FILE.parent
        assert service.SHARE_PID_FILE.parent == service.PID_FILE.parent
        assert service._port_state_file().parent == service.PID_FILE.parent
        monkeypatch.delenv("AGENTICLEDGER_DSN", raising=False)
        env = service._child_env()
        assert "instances/demo/agenticledger.db" in env["AGENTICLEDGER_DSN"]
        (tmp_path / "instances" / "demo").mkdir(parents=True)
        assert service.instances() == ["demo"]
    finally:
        # Module globals were rebound; restore for other tests.
        service.INSTANCE = None
        service.PID_FILE = service.STATE_DIR / "proxy.pid"
        service.LOG_FILE = service.STATE_DIR / "proxy.log"
        service.CONFIG_STATE_FILE = service.STATE_DIR / "proxy.config"
        service.SHARE_PID_FILE = service.STATE_DIR / "share.pid"
        service.SHARE_LOG_FILE = service.STATE_DIR / "share.log"


def test_instance_names_are_validated():
    import pytest as _pytest

    from agenticledger import service
    with _pytest.raises(SystemExit):
        service.use_instance("Bad Name!")


def test_named_instance_ignores_the_config_files_db(tmp_path, monkeypatch):
    """Found live: a config file with [proxy] db pointed the scratch
    instance at the REAL database — two writers, one SQLite file. The
    config file describes the everyday ledger; a named instance always
    keeps its own database unless AGENTICLEDGER_DSN is set explicitly."""
    from agenticledger import service
    monkeypatch.delenv("AGENTICLEDGER_DSN", raising=False)
    cfg = tmp_path / "agenticledger.toml"
    cfg.write_text('[proxy]\ndb = "sqlite:////real/main.db"\n')
    monkeypatch.setenv("AGENTICLEDGER_CONFIG", str(cfg))
    monkeypatch.setattr(service, "STATE_DIR", tmp_path)
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "instances" / "demo" / "proxy.pid")
    monkeypatch.setattr(service, "INSTANCE", "demo")
    dsn = service._child_env()["AGENTICLEDGER_DSN"]
    assert "instances/demo/agenticledger.db" in dsn
    assert "real/main.db" not in dsn
    # An explicit env DSN on the start command is deliberate and wins —
    # "explicit" means present BEFORE any config load, which the module
    # snapshots at import (_EXPLICIT_DSN).
    monkeypatch.setenv("AGENTICLEDGER_DSN", "sqlite:///deliberate.db")
    monkeypatch.setattr(service, "_EXPLICIT_DSN", "sqlite:///deliberate.db")
    assert service._child_env()["AGENTICLEDGER_DSN"] == "sqlite:///deliberate.db"


def test_pick_keeper_prefers_the_highest_version():
    from agenticledger.doctor import Install, pick_keeper
    from pathlib import Path as P
    old = Install(script=P("/framework/agenticledger"), interpreter=P("/framework/python3"),
                  version="0.11.0", wins=True)
    new = Install(script=P("/usr/local/bin/agenticledger"), interpreter=P("/good/python"),
                  version="0.11.1.dev27", wins=False)
    assert pick_keeper([old, new]) is new
    # A broken install can never be the keeper, whatever its version.
    broken = Install(script=P("/x"), interpreter=P("/y"), version="9.9.9", error="boom")
    assert pick_keeper([broken, old]) is old


def test_skew_verdict_never_advises_a_downgrade():
    from agenticledger.doctor import Install, diagnose
    from pathlib import Path as P
    shadow = Install(script=P("/framework/agenticledger"), interpreter=P("/f/python3"),
                     version="0.11.0", wins=True)
    verdicts = diagnose([shadow], {"running": True, "version": "0.11.1.dev27"})
    assert any("DOWNGRADE" in v for v in verdicts)
    assert not any("a restart applies it" in v for v in verdicts)
