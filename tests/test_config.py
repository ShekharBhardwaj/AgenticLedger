"""agenticledger.toml: one file instead of nine env vars (#48), and the
background service commands around it (#49)."""

import os
import sys

import pytest

from agenticledger.config import TEMPLATE, apply_config, find_config, init_config


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
    """The whole premium-ops promise: start detaches, status answers, stop kills."""
    import socket

    from agenticledger import service

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    (tmp_path / "agenticledger.toml").write_text(
        f'[proxy]\nport = {port}\ndb = "sqlite:///{tmp_path}/svc.db"\n')
    monkeypatch.setattr(service, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(service, "PID_FILE", tmp_path / "state" / "proxy.pid")
    monkeypatch.setattr(service, "LOG_FILE", tmp_path / "state" / "proxy.log")

    assert service.status() == 3  # stopped
    assert service.start() == 0
    try:
        assert service.status() == 0
        assert service.start() == 0  # idempotent: already running
        assert service.logs(lines=5) == 0
    finally:
        assert service.stop() == 0
    assert service.status() == 3
    assert service.stop() == 0  # idempotent: already stopped


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
    assert rows[("Access", "dashboard key")]["value"] == "set (hidden)"
    assert "master-key" not in str(body)
    assert rows[("Budgets", "daily (whole ledger)")]["value"] == "25.0"
    assert rows[("Budgets", "daily (whole ledger)")]["source"] == "file"
    assert "OPEN RELAY" in rows[("Access", "ingest key (relay)")]["value"]
