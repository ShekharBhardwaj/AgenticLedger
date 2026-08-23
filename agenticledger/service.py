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
# The port the RUNNING proxy answers on. The service inherits its env at
# start, but status/doctor run later in shells without that env — probing
# the default port then misreads a healthy service as down (found live
# during a demo on a non-default port).
PORT_STATE_FILE = STATE_DIR / "proxy.port"


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
            return int(PORT_STATE_FILE.read_text().strip())
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
    PORT_STATE_FILE.write_text(str(port))

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
