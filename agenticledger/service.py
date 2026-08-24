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
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import apply_config, find_config

STATE_DIR = Path.home() / ".agenticledger"
PID_FILE = STATE_DIR / "proxy.pid"
LOG_FILE = STATE_DIR / "proxy.log"
# The absolute path of the config file the background proxy read at start
# (empty when it started without one). `config set` compares against this
# to warn when an edit lands in a file the running proxy never saw.
CONFIG_STATE_FILE = STATE_DIR / "proxy.config"


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
    if not env.get("AGENTICLEDGER_DSN"):
        default_db = STATE_DIR / "agenticledger.db"
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
        print(f"Already running (pid {pid}) — dashboard: http://localhost:{port}/app")
        return 0

    STATE_DIR.mkdir(exist_ok=True)
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
    PID_FILE.parent.mkdir(exist_ok=True)
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


def remote() -> int:
    """Print the pairing link for reaching the dashboard from another device."""
    import os
    if os.environ.get("AGENTICLEDGER_API_KEY"):
        print("AGENTICLEDGER_API_KEY is set, so remote visitors use that key")
        print("(or a minted token) — there is no separate remote key.")
        return 0
    key_file = STATE_DIR / "remote.key"
    try:
        key = key_file.read_text().strip()
    except OSError:
        print("No remote key yet. It is created the first time the ledger")
        print("starts. Start it, then run this again:  agenticledger start")
        return 1
    port = effective_port()
    ip = _lan_ip()
    print("From this machine, the dashboard needs no key:")
    print(f"  http://localhost:{port}/app")
    print()
    print("From your phone or another machine on the same network (or")
    print("tailnet), open the pairing link:")
    if ip:
        print(f"  http://{ip}:{port}/app?api_key={key}")
    else:
        print(f"  http://<this-machine's-address>:{port}/app?api_key={key}")
    print()
    print("The link carries the key: share it only with your own devices.")
    print(f"To rotate it, delete {key_file} and restart.")
    return 0


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


def share(stop: bool = False) -> int:
    """One command, dashboard in your pocket: https tunnel you own, key
    enforced, QR to pair. No relay of ours — the tunnel is Cloudflare's
    standard quick tunnel, started and stopped on this machine."""
    import shutil
    import subprocess
    import time as _time
    if stop:
        return _share_stop()
    if not _alive(_read_pid()):
        print("The ledger is not running — start it first:  agenticledger start")
        return 1
    if os.environ.get("AGENTICLEDGER_API_KEY"):
        key = None   # explicit-key mode: visitors use that key or a minted token
    else:
        try:
            key = (STATE_DIR / "remote.key").read_text().strip()
        except OSError:
            print("No remote key found — this build predates the remote guard.")
            print("Upgrade and restart, then run share again.")
            return 1
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        print("share needs the cloudflared tool (a tunnel you own; no account")
        print("needed). Install it, then rerun:")
        print("  brew install cloudflared")
        return 1
    if SHARE_PID_FILE.exists():
        _share_stop()
    port = effective_port()
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
