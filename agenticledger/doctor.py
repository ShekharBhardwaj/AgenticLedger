"""agenticledger doctor — the whole truth of this machine, in plain words.

Born from a day of "which python owns this": a stale copy in the system
Python shadowed the real install on PATH, its dependencies were compiled
for the wrong architecture, and every symptom looked like the product
being broken. Doctor answers all of it in one command:

- every `agenticledger` on PATH, in resolution order, and who shadows whom
- which interpreter owns each install, its version, and whether it can
  actually run (an import probe catches wrong-architecture wheels and
  missing dependencies)
- the background service: running or not, what version is serving, and
  whether it is older than the code on disk
- a verdict per finding, each with the exact command that fixes it

Exit 0 when healthy, 1 when anything needs attention.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_PROBE = (
    "from importlib.metadata import version;"
    "import fastapi, pydantic_core, httpx;"  # the heavy, breakable imports
    "print(version('agentic-ledger'))"
)


@dataclass
class Install:
    script: Path                 # the console script on PATH
    interpreter: Optional[Path]  # from its shebang
    version: Optional[str] = None
    error: Optional[str] = None  # head of the import failure, if any
    note: Optional[str] = None   # e.g. "runs as x86_64" on a Rosetta install
    wins: bool = False           # first on PATH: the one the shell runs

    @property
    def broken(self) -> bool:
        return self.error is not None


def _shebang(script: Path) -> Optional[Path]:
    """The interpreter a console script runs on, from its first line."""
    try:
        first = script.open("rb").readline().decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    parts = first[2:].split()
    if not parts:
        return None
    # "#!/usr/bin/env python3" form: the interpreter is the second word.
    exe = parts[1] if parts[0].endswith("/env") and len(parts) > 1 else parts[0]
    return Path(exe)


def _run_probe(cmd: list[str]) -> tuple[Optional[str], Optional[str]]:
    try:
        out = subprocess.run(  # noqa: S603 — probing the user's own installs
            cmd, capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if out.returncode == 0:
        return out.stdout.strip() or None, None
    tail = (out.stderr or "").strip().splitlines()
    # Full last line: callers search it (the architecture words sit deep
    # inside dlopen errors); display truncates later.
    return None, (tail[-1] if tail else f"exit {out.returncode}")


def _probe(interpreter: Path) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(version, error, note): ask the owning interpreter whether this
    install can actually run. Wrong-architecture wheels and missing
    dependencies surface here instead of at 2am. On a Mac, an install
    built for the OTHER architecture (a Rosetta environment probed from a
    native shell, or vice versa) is retried under that architecture: if
    it runs there, it is healthy, and the note says which world it lives
    in instead of crying wolf."""
    version, error = _run_probe([str(interpreter), "-c", _PROBE])
    if error and "incompatible architecture" in error:
        m = re.search(r"have '([a-z0-9_]+)'", error)
        if m:
            arch = m.group(1)
            version, retry_error = _run_probe(
                ["arch", f"-{arch}", str(interpreter), "-c", _PROBE])
            if not retry_error:
                return version, None, f"runs as {arch} (a Rosetta-world install)"
    return version, (error[:200] if error else None), None


def find_installs(path_env: Optional[str] = None) -> list[Install]:
    """Every agenticledger on PATH, in the order the shell resolves them."""
    seen: set[Path] = set()
    installs: list[Install] = []
    for entry in (path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep):
        if not entry:
            continue
        script = Path(entry) / "agenticledger"
        try:
            resolved = script.resolve()
        except OSError:
            continue
        if not script.is_file() or not os.access(script, os.X_OK) or resolved in seen:
            continue
        seen.add(resolved)
        installs.append(Install(script=script, interpreter=_shebang(resolved)))
    if installs:
        installs[0].wins = True
    return installs


def diagnose(installs: list[Install], service_state: dict) -> list[str]:
    """Plain-words verdicts, each carrying its fix. Pure function so the
    reasoning is testable without a real machine."""
    verdicts: list[str] = []
    winner = next((i for i in installs if i.wins), None)

    if not installs:
        verdicts.append(
            "no agenticledger on PATH. Install one: pip install agentic-ledger")
        return verdicts

    for inst in installs:
        if inst.broken:
            fix = (f"{inst.interpreter} -m pip uninstall -y agentic-ledger"
                   if inst.interpreter else f"remove {inst.script}")
            role = "THE ONE YOUR SHELL RUNS" if inst.wins else "shadowed"
            verdicts.append(
                f"broken install at {inst.script} ({role}): {inst.error}. "
                f"Remove it: {fix}")

    if winner and len(installs) > 1:
        others = [i for i in installs if not i.wins]
        for other in others:
            if winner.broken and not other.broken:
                verdicts.append(
                    f"a broken copy at {winner.script} is shadowing the "
                    f"working install at {other.script}. Remove the broken "
                    f"one (above), then `hash -r`.")
            elif not winner.broken and not other.broken and winner.version != other.version:
                verdicts.append(
                    f"two working installs disagree: your shell runs "
                    f"{winner.script} (v{winner.version}); {other.script} "
                    f"has v{other.version}. Keep one; remove the other with "
                    f"its own interpreter's pip.")

    if winner and not winner.broken:
        running = service_state.get("running")
        served = service_state.get("version")
        if not running:
            verdicts.append(
                "the background service is not running. Start it: "
                "agenticledger start")
        elif served and winner.version and served != winner.version:
            verdicts.append(
                f"the running proxy serves v{served} but the installed code "
                f"is v{winner.version} — a restart applies it: "
                f"agenticledger stop && agenticledger start")

    return verdicts


def doctor_command() -> int:
    from agenticledger import service

    print("agenticledger doctor\n")

    installs = find_installs()
    print("Installs on PATH (shell resolves top first):")
    if not installs:
        print("  (none)")
    for inst in installs:
        inst.version, inst.error, inst.note = (
            _probe(inst.interpreter) if inst.interpreter
            else (None, "no shebang", None))
        marker = "->" if inst.wins else "  "
        state = f"v{inst.version}" if inst.version else f"BROKEN: {inst.error}"
        if inst.note:
            state += f" ({inst.note})"
        print(f" {marker} {inst.script}")
        print(f"      interpreter: {inst.interpreter or '?'}")
        print(f"      {state}")

    pid = service._read_pid()
    alive = service._alive(pid)
    port = service._port()
    health = service._health(port) if alive else None
    service_state = {
        "running": bool(alive and health),
        "version": (health or {}).get("version"),
        "port": port,
    }
    print("\nBackground service:")
    if service_state["running"]:
        print(f"  running (pid {pid}) — v{service_state['version']} on "
              f"port {port}, dashboard http://localhost:{port}")
    elif alive:
        print(f"  process alive (pid {pid}) but not answering on port {port} "
              f"— see agenticledger logs")
        service_state["running"] = False
    else:
        print("  not running" + (f" (stale pid file: {service.PID_FILE})" if pid else ""))

    verdicts = diagnose(installs, service_state)
    print("\nVerdict:")
    if not verdicts:
        print("  healthy: one install, and it is the one running.")
        return 0
    for v in verdicts:
        print(f"  - {v}")
    return 1
