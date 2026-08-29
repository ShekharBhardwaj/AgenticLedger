"""
Background service mode — the proxy without a hostage terminal.

`agenticledger start` detaches the proxy into the background: it keeps
running when the terminal closes, logs to ~/.agenticledger/proxy.log, and
records its pid in ~/.agenticledger/proxy.pid. Unless a database is
configured, the service stores captures in ~/.agenticledger/agenticledger.db
(an absolute path, so the data does not depend on where `start` was run).
`status` answers "is it up and healthy?", `stop` shuts it down cleanly,
`logs` shows what it said. `serve` remains the foreground mode
(containers, debugging).
"""

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import apply_config, find_config

# Captured BEFORE any apply_config() runs: whether the USER's own shell
# set a DSN. apply_config writes the config file's values into os.environ,
# and _port() runs it early in start() — so reading the environment later
# cannot tell a deliberate export from the config file's db. That confusion
# pointed a scratch instance at the real database twice.
_EXPLICIT_DSN = os.environ.get("AGENTICLEDGER_DSN")

STATE_DIR = Path.home() / ".agenticledger"
PID_FILE = STATE_DIR / "proxy.pid"
LOG_FILE = STATE_DIR / "proxy.log"
# The selected instance (#108). None = the default, everyday ledger, whose
# state lives at the STATE_DIR root exactly as before. A named instance
# gets its own state directory — pid, log, port record, share tunnel, and
# its own default database — so a second ledger (a demo rig, a per-project
# recorder) runs BESIDE the everyday one instead of displacing it.
INSTANCE: Optional[str] = None
# The absolute path of the config file the background proxy read at start
# (empty when it started without one). `config set` compares against this
# to warn when an edit lands in a file the running proxy never saw.
CONFIG_STATE_FILE = STATE_DIR / "proxy.config"


def use_instance(name: str) -> None:
    """Select a named instance: every state path moves to its directory.
    The default database moves with them (_child_env derives it from the
    pidfile's home), so instances never share a SQLite file."""
    global INSTANCE, PID_FILE, LOG_FILE, CONFIG_STATE_FILE, SHARE_PID_FILE, SHARE_LOG_FILE
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", name):
        raise SystemExit(f"instance names are lowercase letters, digits, and dashes (got {name!r})")
    INSTANCE = name
    base = STATE_DIR / "instances" / name
    PID_FILE = base / "proxy.pid"
    LOG_FILE = base / "proxy.log"
    CONFIG_STATE_FILE = base / "proxy.config"
    SHARE_PID_FILE = base / "share.pid"
    SHARE_LOG_FILE = base / "share.log"


def instances() -> list[str]:
    """Named instances that exist on this machine (running or not)."""
    try:
        return sorted(d.name for d in (STATE_DIR / "instances").iterdir() if d.is_dir())
    except OSError:
        return []


def _port_state_file() -> Path:
    """Where the RUNNING proxy's port is recorded — always beside the pid
    file (tests relocate PID_FILE; the port record must follow, or it
    writes into a home directory a fresh CI runner does not have). The
    service inherits its env at start, but status/doctor run later in
    shells without that env; probing the default port then misreads a
    healthy service on another port as down (found live during a demo).
    """
    return PID_FILE.parent / "proxy.port"


def _port() -> int:
    apply_config()
    return int(os.environ.get("AGENTICLEDGER_PORT", "8000"))


def _child_env() -> dict:
    """Environment for the spawned proxy.

    The proxy's own default DSN is relative (sqlite:///agenticledger.db),
    so a background service inheriting the caller's cwd would put the
    database wherever `start` happened to be run; restarting from another
    directory then reads a fresh empty file and the old captures look
    lost. If neither the environment nor the config file names a database,
    pin it to an absolute path in STATE_DIR so the data lands in the same
    place every time. Explicit AGENTICLEDGER_DSN and `serve` keep their
    old behavior.
    """
    apply_config()  # the config file's db value, if any, is in os.environ now
    env = dict(os.environ)
    if INSTANCE:
        # The proxy wears its name: /health reports it and the dashboard
        # shows it, so a scratch ledger can never impersonate the real one.
        env["AGENTICLEDGER_INSTANCE"] = INSTANCE
    if INSTANCE and not _EXPLICIT_DSN:
        # The config file describes THE everyday ledger; a named instance
        # inheriting its db would share one SQLite file between two writers.
        # Found live: user zero's scratch instance opened the real database.
        # Only a deliberate AGENTICLEDGER_DSN on the start command overrides.
        if env.get("AGENTICLEDGER_DSN"):
            print(f"note: the config file's db is the everyday ledger's; "
                  f"instance [{INSTANCE}] keeps its own database.", file=sys.stderr)
        env["AGENTICLEDGER_DSN"] = f"sqlite:///{PID_FILE.parent / 'agenticledger.db'}"
    elif not env.get("AGENTICLEDGER_DSN"):
        default_db = PID_FILE.parent / "agenticledger.db"
        env["AGENTICLEDGER_DSN"] = f"sqlite:///{default_db}"
        stray = Path("agenticledger.db")
        if stray.is_file():
            print(
                f"note: found {stray.resolve()} but the background service no longer "
                f"uses it; data goes to {default_db}. To keep the old file, set "
                f"AGENTICLEDGER_DSN or [proxy] db in agenticledger.toml.",
                file=sys.stderr,
            )
    return env


def _record_loaded_config() -> None:
    """Remember which config file the proxy being started will read."""
    loaded = find_config()
    try:
        CONFIG_STATE_FILE.parent.mkdir(exist_ok=True)
        CONFIG_STATE_FILE.write_text(str(loaded) if loaded else "", encoding="utf-8")
    except OSError:
        pass  # the warning in `config set` just stays silent


def running_proxy_config() -> tuple[bool, Optional[Path]]:
    """Which config file is the background proxy using right now?

    Returns (known, path). known is False when the proxy is not running or
    was started by a version that did not record this. path is the file the
    proxy read at start, or None when it started without one."""
    if not _alive(_read_pid()):
        return False, None
    try:
        text = CONFIG_STATE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False, None
    return True, (Path(text) if text else None)


def effective_port() -> int:
    """The port the running service actually answers on: the one recorded
    at start while the process lives, else this shell's configuration."""
    if _alive(_read_pid()):
        try:
            return int(_port_state_file().read_text().strip())
        except (OSError, ValueError):
            pass
    return _port()


def _read_pid() -> Optional[int]:
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _health(port: int) -> Optional[dict]:
    try:
        resp = httpx.get(f"http://localhost:{port}/health", timeout=2.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def start() -> int:
    port = _port()
    pid = _read_pid()
    if _alive(pid):
        tag = f" [{INSTANCE}]" if INSTANCE else ""
        print(f"Already running{tag} (pid {pid}) — dashboard: http://localhost:{effective_port()}/app")
        return 0

    STATE_DIR.mkdir(exist_ok=True)
    # The instance's whole state home, before anything opens a file in it.
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if INSTANCE and not os.environ.get("AGENTICLEDGER_PORT"):
        print("A named instance needs its own port (the default ledger has 8000):")
        print(f"  agenticledger start --name {INSTANCE} --port 8003")
        return 1
    _record_loaded_config()
    kwargs: dict = {"stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
    if os.name == "posix":
        kwargs["start_new_session"] = True  # survives the terminal closing
    else:  # pragma: no cover - Windows
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
    with open(LOG_FILE, "ab") as log:
        # The child inherits the fd; closing our handle after spawn is fine.
        proc = subprocess.Popen([sys.executable, "-m", "agenticledger.proxy"],
                                stdout=log, env=_child_env(), **kwargs)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))
    _port_state_file().write_text(str(port))

    # Wait for it to answer, so "start" means started — not "maybe".
    for _ in range(40):
        time.sleep(0.25)
        health = _health(port)
        if health:
            print(f"Agentic Ledger v{health.get('version', '?')} running in the background "
                  f"(pid {proc.pid})")
            print(f"  dashboard: http://localhost:{port}/app")
            print(f"  logs:      agenticledger logs   ({LOG_FILE})")
            print( "  stop:      agenticledger stop")
            return 0
        if proc.poll() is not None:
            break
    print("The proxy did not come up. Last log lines:", file=sys.stderr)
    _print_log_tail(15, file=sys.stderr)
    PID_FILE.unlink(missing_ok=True)
    return 1


def stop() -> int:
    pid = _read_pid()
    if not _alive(pid):
        PID_FILE.unlink(missing_ok=True)
        print("Not running.")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        time.sleep(0.25)
        if not _alive(pid):
            PID_FILE.unlink(missing_ok=True)
            print(f"Stopped (pid {pid}).")
            return 0
    os.kill(pid, signal.SIGKILL)  # it had five seconds to be graceful
    PID_FILE.unlink(missing_ok=True)
    print(f"Force-stopped (pid {pid}).")
    return 0


def _lan_ip() -> Optional[str]:
    """This machine's address on the local network, or None when offline.
    A UDP connect never sends a packet; it only asks the OS which interface
    would carry one."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("203.0.113.1", 1))
            return sock.getsockname()[0]
    except OSError:
        return None


SHARE_PID_FILE = STATE_DIR / "share.pid"
SHARE_LOG_FILE = STATE_DIR / "share.log"


def _share_stop() -> int:
    try:
        pid = int(SHARE_PID_FILE.read_text().strip())
    except (OSError, ValueError):
        print("No share tunnel is running.")
        return 0
    import signal
    from contextlib import suppress
    with suppress(OSError):
        os.kill(pid, signal.SIGTERM)
    SHARE_PID_FILE.unlink(missing_ok=True)
    print(f"Share tunnel stopped (pid {pid}). The old link is dead.")
    return 0


def _print_qr(url: str) -> None:
    """A QR code in the terminal, so pairing is point-a-camera. Optional:
    without the qrcode package the link alone still works."""
    try:
        import qrcode
    except ImportError:
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)


def _pairing_key() -> Optional[str]:
    if os.environ.get("AGENTICLEDGER_API_KEY"):
        return None   # explicit-key mode: visitors use that key or a minted token
    try:
        return (STATE_DIR / "pairing.key").read_text().strip()
    except OSError:
        return None


def share(stop: bool = False, wifi: bool = False, rotate: bool = False) -> int:
    """One verb for "get my dashboard onto another device".

    Default: an https tunnel you own (cloudflared quick tunnel, no account),
    pairing link with the key built in, QR in the terminal — works from
    anywhere. --wifi skips the tunnel and prints the same-network link.
    --rotate mints a fresh key (every paired device un-pairs at once).
    --stop closes the tunnel. No relay of ours, ever.
    """
    import shutil
    import subprocess
    import time as _time
    if stop:
        return _share_stop()
    if rotate:
        if os.environ.get("AGENTICLEDGER_API_KEY"):
            print("AGENTICLEDGER_API_KEY is set — rotate that key instead; the")
            print("auto-generated pairing key is not in use.")
            return 1
        (STATE_DIR / "pairing.key").unlink(missing_ok=True)
        print("Pairing key rotated: the old key is dead on the next restart.")
        print("Restart to mint the new one, then share again:")
        print("  agenticledger stop && agenticledger start && agenticledger share")
        return 0
    if not _alive(_read_pid()):
        print("The ledger is not running — start it first:  agenticledger start")
        return 1
    key = _pairing_key()
    if key is None and not os.environ.get("AGENTICLEDGER_API_KEY"):
        print("No pairing key found — this build predates the remote guard.")
        print("Upgrade and restart, then run share again.")
        return 1
    port = effective_port()

    if wifi:
        ip = _lan_ip()
        if not ip:
            print("No network address found — is this machine online?")
            return 1
        pairing = (f"http://{ip}:{port}/app?api_key={key}" if key
                   else f"http://{ip}:{port}/app")
        print("Your dashboard, for devices on the same wifi (or tailnet):")
        print(f"  {pairing}")
        print()
        _print_qr(pairing)
        print("Point your phone's camera at the code, or open the link.")
        if key:
            print("The link carries the key: share it only with your own devices.")
        print("Note: this link is plain http. For https (and for devices not")
        print("on this network), run:  agenticledger share")
        return 0

    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        print("share needs the cloudflared tool (a tunnel you own; no account")
        print("needed). Install it, then rerun:")
        print("  brew install cloudflared")
        print()
        print("Or, for devices on the same wifi, no tunnel needed:")
        print("  agenticledger share --wifi")
        return 1
    if SHARE_PID_FILE.exists():
        _share_stop()
    with open(SHARE_LOG_FILE, "wb") as log:
        proc = subprocess.Popen(
            [cloudflared, "tunnel", "--url", f"http://localhost:{port}"],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            start_new_session=True)
    SHARE_PID_FILE.write_text(str(proc.pid))
    print("Opening the tunnel", end="", flush=True)
    url = None
    import re
    for _ in range(60):
        _time.sleep(0.5)
        print(".", end="", flush=True)
        try:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com",
                          SHARE_LOG_FILE.read_text())
        except OSError:
            m = None
        if m:
            url = m.group(0)
            break
        if proc.poll() is not None:
            break
    print()
    if not url:
        print("The tunnel did not come up. Last log lines:")
        try:
            for line in SHARE_LOG_FILE.read_text().splitlines()[-5:]:
                print(f"  {line}")
        except OSError:
            pass
        SHARE_PID_FILE.unlink(missing_ok=True)
        return 1
    pairing = f"{url}/app?api_key={key}" if key else f"{url}/app"
    print()
    print("Your dashboard, from anywhere, over https:")
    print(f"  {pairing}")
    print()
    _print_qr(pairing)
    print("Point your phone's camera at the code, or open the link.")
    if key:
        print("The link carries the key: share it only with your own devices.")
    else:
        print("Visitors sign in with your AGENTICLEDGER_API_KEY or a minted token.")
    print("Close the door with:  agenticledger share --stop")
    return 0


def status() -> int:
    if INSTANCE is None:
        others = [n for n in instances()]
        if others:
            print(f"named instances: {', '.join(others)} (status --name <name>)")
    port = effective_port()
    pid = _read_pid()
    running = _alive(pid)
    health = _health(port) if running else None
    if not running:
        print("Stopped." + (f" (stale pid file: {PID_FILE})" if pid else ""))
        return 3
    if health is None:
        print(f"Process alive (pid {pid}) but not answering on port {port} — "
              f"check `agenticledger logs`.")
        return 1
    ready = "ok"
    try:
        r = httpx.get(f"http://localhost:{port}/readyz", timeout=2.0)
        ready = r.json().get("store", "?")
    except Exception:
        ready = "?"
    print(f"Running (pid {pid}) — v{health.get('version', '?')} on port {port}, "
          f"store {ready}")
    print(f"  dashboard: http://localhost:{port}/app")
    if health.get("bedrock"):
        print(f"  bedrock:   {health['bedrock']}")
    return 0


def logs(lines: int = 50, follow: bool = False) -> int:
    if not LOG_FILE.exists():
        print(f"No log yet ({LOG_FILE}).")
        return 0
    _print_log_tail(lines)
    if follow:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(0, os.SEEK_END)
            try:
                while True:
                    line = fh.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    return 0


def _print_log_tail(lines: int, file=sys.stdout) -> None:
    try:
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in content[-lines:]:
        print(line, file=file)
