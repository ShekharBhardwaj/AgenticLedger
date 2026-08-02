"""agenticledger.toml: one file instead of nine env vars (#48), and the
background service commands around it (#49)."""

import os
import sys

import pytest

from agenticledger.config import (
    TEMPLATE,
    apply_config,
    find_config,
    get_value,
    init_config,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Empty temp cwd, no AGENTICLEDGER_* before OR after — apply_config
    writes os.environ directly, so restore everything ourselves rather than
    relying on monkeypatch (which only tracks its own changes)."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("AGENTICLEDGER_")}
    for name in saved:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
    yield
    for name in [k for k in os.environ if k.startswith("AGENTICLEDGER_")]:
        del os.environ[name]
    os.environ.update(saved)


def test_config_file_fills_the_environment(tmp_path):
    (tmp_path / "agenticledger.toml").write_text("""
[proxy]
port = 8123
upstream_url = "https://api.anthropic.com"

[budgets]
daily = 25.0

[keys]
api_key = "from-file"

[replay]
openai_url = "http://localhost:1234"
openai_key = "lm-studio"

[env]
AGENTICLEDGER_RETENTION_DAYS = "30"
""")
    used = apply_config()
    assert used is not None and used.name == "agenticledger.toml"
    assert os.environ["AGENTICLEDGER_PORT"] == "8123"
    assert os.environ["AGENTICLEDGER_UPSTREAM_URL"] == "https://api.anthropic.com"
    assert os.environ["AGENTICLEDGER_BUDGET_DAILY"] == "25.0"
    assert os.environ["AGENTICLEDGER_API_KEY"] == "from-file"
    assert os.environ["AGENTICLEDGER_REPLAY_OPENAI_KEY"] == "lm-studio"
    assert os.environ["AGENTICLEDGER_RETENTION_DAYS"] == "30"


def test_environment_always_wins_over_the_file(tmp_path, monkeypatch):
    (tmp_path / "agenticledger.toml").write_text('[proxy]\nport = 8123\n')
    monkeypatch.setenv("AGENTICLEDGER_PORT", "9000")
    apply_config()
    assert os.environ["AGENTICLEDGER_PORT"] == "9000"


def test_unknown_and_non_ledger_keys_are_ignored(tmp_path):
    (tmp_path / "agenticledger.toml").write_text("""
[proxy]
warp_speed = true

[env]
PATH = "/tmp/evil"
""")
    apply_config()
    assert "AGENTICLEDGER_WARP_SPEED" not in os.environ
    assert os.environ.get("PATH") != "/tmp/evil"  # [env] is AGENTICLEDGER_* only


def test_explicit_config_env_var_points_elsewhere(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere.toml"
    other.write_text('[proxy]\nport = 8777\n')
    monkeypatch.setenv("AGENTICLEDGER_CONFIG", str(other))
    assert apply_config() == other
    assert os.environ["AGENTICLEDGER_PORT"] == "8777"


def test_no_config_file_is_fine():
    assert find_config() is None or find_config().name == "config.toml"
    # apply_config on a machine with only the home-dir fallback must not raise.
    apply_config()


def test_init_writes_template_once(tmp_path):
    target = init_config(str(tmp_path / "agenticledger.toml"))
    assert target.read_text() == TEMPLATE
    if os.name == "posix":
        assert oct(target.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(SystemExit):
        init_config(str(target))


def test_template_itself_parses_and_applies(tmp_path):
    """The file we hand users must load cleanly, before and after edits."""
    init_config(str(tmp_path / "agenticledger.toml"))
    assert apply_config() is not None  # all-commented template = valid, no-op
    (tmp_path / "agenticledger.toml").write_text(
        TEMPLATE.replace("# port = 8000", "port = 8042"))
    apply_config()
    assert os.environ["AGENTICLEDGER_PORT"] == "8042"


def test_key_file_paths_expand_home(tmp_path):
    (tmp_path / "agenticledger.toml").write_text(
        '[keys]\napi_key_file = "~/.agenticledger/api.key"\n')
    apply_config()
    assert os.environ["AGENTICLEDGER_API_KEY_FILE"].startswith(os.path.expanduser("~"))
    assert "~" not in os.environ["AGENTICLEDGER_API_KEY_FILE"]


@pytest.mark.skipif(os.name != "posix", reason="daemonization is POSIX-only in tests")
def test_service_start_status_stop_roundtrip(tmp_path, monkeypatch):
    """The whole premium-ops promise: start detaches, status answers, stop
    kills. With no db configured anywhere, the database must land in the
    state dir (absolute), not in whatever directory start was run from."""
    import socket

    from agenticledger import service

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (tmp_path / "agenticledger.toml").write_text(f'[proxy]\nport = {port}\n')
    monkeypatch.setattr(service, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "state" / "proxy.pid")
    monkeypatch.setattr(service, "LOG_FILE", tmp_path / "state" / "proxy.log")
    monkeypatch.setattr(service, "CONFIG_STATE_FILE", tmp_path / "state" / "proxy.config")

    assert service.status() == 3  # stopped
    assert service.start() == 0
    try:
        assert service.status() == 0
        assert service.start() == 0  # idempotent: already running
        assert service.logs(lines=5) == 0
        assert (tmp_path / "state" / "agenticledger.db").is_file()
        assert not (tmp_path / "agenticledger.db").exists()  # nothing in cwd
        # start recorded which config file the proxy read, absolute.
        recorded = (tmp_path / "state" / "proxy.config").read_text().strip()
        assert recorded == str((tmp_path / "agenticledger.toml").resolve())
        assert service.running_proxy_config() == (True, (tmp_path / "agenticledger.toml").resolve())
    finally:
        assert service.stop() == 0
    assert service.status() == 3
    assert service.stop() == 0  # idempotent: already stopped


def test_service_pins_default_db_to_the_state_dir(tmp_path, monkeypatch):
    """A relative default db + inherited cwd meant the data moved with the
    directory `start` was run from; a restart elsewhere looked like data
    loss. The service pins the default to an absolute path instead."""
    from agenticledger import service

    # Immunity to the machine running the suite: ambient env, the real
    # home config, and cwd contents must not reach the assertions.
    monkeypatch.delenv("AGENTICLEDGER_DSN", raising=False)
    (tmp_path / "empty.toml").write_text("")
    monkeypatch.setenv("AGENTICLEDGER_CONFIG", str(tmp_path / "empty.toml"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(service, "STATE_DIR", tmp_path / "state")
    dsn = service._child_env()["AGENTICLEDGER_DSN"]
    assert dsn == f"sqlite:///{tmp_path / 'state' / 'agenticledger.db'}"
    # The path part is absolute once the store parses the URL.
    assert os.path.isabs(dsn.split("sqlite:///", 1)[1])


def test_service_default_db_yields_to_explicit_env(monkeypatch):
    from agenticledger import service

    monkeypatch.setenv("AGENTICLEDGER_DSN", "sqlite:///explicit.db")
    assert service._child_env()["AGENTICLEDGER_DSN"] == "sqlite:///explicit.db"


def test_service_default_db_yields_to_the_config_file(tmp_path):
    from agenticledger import service

    (tmp_path / "agenticledger.toml").write_text('[proxy]\ndb = "sqlite:///from-file.db"\n')
    assert service._child_env()["AGENTICLEDGER_DSN"] == "sqlite:///from-file.db"


def test_service_notes_a_stranded_db_in_cwd(tmp_path, monkeypatch, capsys):
    """An agenticledger.db sitting in cwd used to BE the database; now the
    service ignores it. Say so, and say how to keep it, instead of leaving
    the user staring at an empty dashboard."""
    from agenticledger import service

    monkeypatch.delenv("AGENTICLEDGER_DSN", raising=False)
    (tmp_path / "empty.toml").write_text("")
    monkeypatch.setenv("AGENTICLEDGER_CONFIG", str(tmp_path / "empty.toml"))
    monkeypatch.chdir(tmp_path)  # the stray check reads cwd; make it ours
    (tmp_path / "agenticledger.db").write_text("")
    monkeypatch.setattr(service, "STATE_DIR", tmp_path / "state")
    service._child_env()
    err = capsys.readouterr().err
    assert "agenticledger.db" in err and "no longer" in err
    assert "AGENTICLEDGER_DSN" in err  # the way out is named, not implied

    # With a db configured, the note would be wrong; it must stay quiet.
    monkeypatch.setenv("AGENTICLEDGER_DSN", "sqlite:///explicit.db")
    service._child_env()
    assert capsys.readouterr().err == ""


def test_cli_wires_the_subcommands(tmp_path, capsys):
    from agenticledger.cli import main

    assert main(["init", "--path", str(tmp_path / "c.toml")]) == 0
    assert "agenticledger start" in capsys.readouterr().out
    assert (tmp_path / "c.toml").is_file()


# The proxy fixture used across the suite sets env directly; make sure the
# _clean_env fixture here didn't leak into other files via import order.
assert "proxy" not in dir(sys.modules[__name__])


def test_settings_page_is_admin_only_and_masks_secrets(proxy, monkeypatch, tmp_path):
    """#50: read-only settings — admin sees what's running, never the secrets."""
    (tmp_path / "agenticledger.toml").write_text("[budgets]\ndaily = 25.0\n")
    apply_config()
    monkeypatch.setenv("AGENTICLEDGER_API_KEY", "master-key")
    client = proxy(budget_daily=25.0)
    master = {"x-agenticledger-api-key": "master-key"}

    assert client.get("/api/settings").status_code == 401
    viewer = client.post("/api/tokens", json={"name": "v", "role": "viewer"},
                         headers=master).json()["token"]
    assert client.get("/api/settings",
                      headers={"Authorization": f"Bearer {viewer}"}).status_code == 403

    body = client.get("/api/settings", headers=master).json()
    rows = {(r["section"], r["label"]): r for r in body["rows"]}
    # The config-file row shows the full path of the file this process
    # loaded — a bare relative name let two shells mean two different files.
    assert rows[("Proxy", "config file")]["value"] == str(
        (tmp_path / "agenticledger.toml").resolve())
    assert rows[("Access", "dashboard key")]["value"] == "set (hidden)"
    assert "master-key" not in str(body)
    assert rows[("Budgets", "daily (whole ledger)")]["value"] == "25.0"
    assert rows[("Budgets", "daily (whole ledger)")]["source"] == "file"
    assert "OPEN RELAY" in rows[("Access", "ingest key (relay)")]["value"]
    # The page explains itself: plain words + where to set it (#50 follow-up).
    upstream = rows[("Proxy", "upstream")]
    assert "forwarded" in upstream["means"]
    assert "[proxy] upstream_url" in upstream["set_with"]
    assert "AGENTICLEDGER_UPSTREAM_URL" in upstream["set_with"]
    assert all(r["means"] for r in body["rows"])


def test_config_set_get_unset_roundtrip(tmp_path):
    """`agenticledger config set` edits the file in place, reusing the
    template's own commented line instead of duplicating the key."""
    from agenticledger.cli import main

    target = tmp_path / "agenticledger.toml"
    init_config(str(target))
    assert main(["config", "set", "proxy.upstream_url", "https://api.anthropic.com"]) == 0
    text = target.read_text()
    assert text.count("upstream_url") == 1          # reused the commented line
    assert 'upstream_url = "https://api.anthropic.com"' in text
    assert "# Uncomment what you need" in text      # comments survived
    assert get_value("proxy.upstream_url") == "https://api.anthropic.com"

    # Numbers and booleans land unquoted so TOML types stay right.
    main(["config", "set", "budgets.daily", "25"])
    assert apply_config() and os.environ["AGENTICLEDGER_BUDGET_DAILY"] == "25"

    assert main(["config", "unset", "proxy.upstream_url"]) == 0
    assert get_value("proxy.upstream_url") is None


def test_config_rejects_unknown_keys_with_a_useful_message(tmp_path):
    from agenticledger.cli import main

    init_config(str(tmp_path / "agenticledger.toml"))
    for bad in ("upstream_url", "proxy.warp_speed", "nonsense.key"):
        with pytest.raises(SystemExit) as exc:
            main(["config", "set", bad, "x"])
        assert "Known" in str(exc.value) or "section.key" in str(exc.value)


def test_config_set_creates_the_home_file_when_missing(tmp_path, monkeypatch, capsys):
    """With no config file anywhere, `config set` used to create
    ./agenticledger.toml, which brought cwd-dependence back: a later
    `config set` or service start from another folder resolved a different
    file (observed live on 0.8.2). The new file now lands at
    ~/.agenticledger/config.toml, the path every directory falls back to."""
    from agenticledger.cli import main

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workdir = tmp_path / "project"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    assert main(["config", "set", "proxy.port", "9001"]) == 0
    created = home / ".agenticledger" / "config.toml"
    assert created.is_file()
    assert not (workdir / "agenticledger.toml").exists()  # nothing in cwd
    assert get_value("proxy.port") == "9001"
    # The output names the file with its full path, so the operator can see
    # exactly where the setting landed.
    assert str(created.resolve()) in capsys.readouterr().out


def test_config_set_still_edits_an_existing_local_file(tmp_path, monkeypatch):
    """An agenticledger.toml already sitting in the current directory is an
    explicit choice; `config set` keeps editing it instead of starting a
    second file in the home directory."""
    from agenticledger.cli import main

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    local = tmp_path / "agenticledger.toml"
    init_config(str(local))

    assert main(["config", "set", "proxy.port", "9002"]) == 0
    assert "port = 9002" in local.read_text()
    assert not (home / ".agenticledger" / "config.toml").exists()


def test_config_commands_name_the_file_with_its_full_path(tmp_path, capsys):
    """A bare "agenticledger.toml:" in output let a budget get set via one
    directory's file and unset via another's while the operator believed
    both hit the same file. Every config command now prints the full path."""
    from agenticledger.cli import main

    resolved = str((tmp_path / "agenticledger.toml").resolve())
    init_config(str(tmp_path / "agenticledger.toml"))
    capsys.readouterr()

    assert main(["config", "set", "budgets.daily", "0.01"]) == 0
    assert capsys.readouterr().out.startswith(f"{resolved}: budgets.daily = 0.01")

    assert main(["config", "get", "budgets.daily"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "0.01"     # stdout stays script-friendly
    assert resolved in captured.err           # the file is named on stderr

    assert main(["config", "get", "proxy.port"]) == 0
    assert resolved in capsys.readouterr().out  # "(not set in <full path>)"

    assert main(["config", "unset", "budgets.daily"]) == 0
    assert capsys.readouterr().out.startswith(f"{resolved}: budgets.daily (unset)")

    assert main(["config", "path"]) == 0
    assert capsys.readouterr().out.strip() == resolved


def test_config_set_warns_when_the_running_proxy_loaded_another_file(
        tmp_path, monkeypatch, capsys):
    """The trap this closes: the running proxy loaded one file, the edit
    landed in another, and nothing said so — the budget wall stayed up
    while the operator believed it was lifted."""
    from agenticledger import service
    from agenticledger.cli import main

    edited = (tmp_path / "agenticledger.toml").resolve()
    other = (tmp_path / "elsewhere" / "agenticledger.toml").resolve()
    other.parent.mkdir()
    init_config(str(edited))
    init_config(str(other))
    monkeypatch.setattr(service, "CONFIG_STATE_FILE", tmp_path / "state" / "proxy.config")
    monkeypatch.setattr(service, "_read_pid", lambda: 12345)
    monkeypatch.setattr(service, "_alive", lambda pid: True)
    service.CONFIG_STATE_FILE.parent.mkdir()

    # Proxy loaded a different file than the one this edit touches.
    service.CONFIG_STATE_FILE.write_text(str(other))
    assert main(["config", "set", "budgets.daily", "1"]) == 0
    out = capsys.readouterr().out
    assert "Warning" in out and str(other) in out

    # Proxy started without any config file.
    service.CONFIG_STATE_FILE.write_text("")
    assert main(["config", "set", "budgets.daily", "2"]) == 0
    assert "Warning" in capsys.readouterr().out

    # Editing the very file the proxy loaded: no warning.
    service.CONFIG_STATE_FILE.write_text(str(edited))
    assert main(["config", "set", "budgets.daily", "3"]) == 0
    assert "Warning" not in capsys.readouterr().out

    # No proxy running: no warning either.
    monkeypatch.setattr(service, "_alive", lambda pid: False)
    service.CONFIG_STATE_FILE.write_text(str(other))
    assert main(["config", "unset", "budgets.daily"]) == 0
    assert "Warning" not in capsys.readouterr().out
