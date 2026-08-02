"""
agenticledger.toml — one file instead of nine env vars in one command.

The proxy reads its settings from a TOML file at startup: the file named by
AGENTICLEDGER_CONFIG, else ./agenticledger.toml, else
~/.agenticledger/config.toml. Every value maps to the same environment
variable the proxy has always used, and a variable that is already set in
the environment ALWAYS wins — so Docker/Kubernetes deployments and one-off
`VAR=x agenticledger start` overrides keep working unchanged.

`agenticledger init` writes a fully commented template next to you.

Secrets: prefer the *_file keys — they point at a file whose contents are
the key (the Docker-secrets pattern), so nothing secret lives in the config
file or shell history.
"""

import logging
import os
import stat
import sys
from pathlib import Path
from typing import Any, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on 3.10 CI only
    import tomli as tomllib

logger = logging.getLogger("agenticledger.config")

# Names apply_config actually set (i.e. they came from the file, not the
# user's environment) — lets the settings page answer "where did this value
# come from?" instead of making the user guess.
applied_from_file: set[str] = set()

# What apply_config did in THIS process: whether it ran, and which file it
# read. The settings page reports these instead of searching the disk again
# at request time, so it names the file the proxy loaded at startup even if
# files were added or removed since.
load_attempted = False
loaded_path: Optional[Path] = None

# config key → environment variable. The env pipeline stays the single
# source of truth; the file is just a friendlier way to fill it.
_KEY_MAP: dict[str, dict[str, str]] = {
    "proxy": {
        "port": "AGENTICLEDGER_PORT",
        "host": "AGENTICLEDGER_HOST",
        "upstream_url": "AGENTICLEDGER_UPSTREAM_URL",
        "db": "AGENTICLEDGER_DSN",
        "completion_promise": "AGENTICLEDGER_COMPLETION_PROMISE",
    },
    "keys": {
        "api_key": "AGENTICLEDGER_API_KEY",
        "api_key_file": "AGENTICLEDGER_API_KEY_FILE",
        "ingest_key": "AGENTICLEDGER_INGEST_KEY",
        "ingest_key_file": "AGENTICLEDGER_INGEST_KEY_FILE",
    },
    "budgets": {
        "session": "AGENTICLEDGER_BUDGET_SESSION",
        "agent": "AGENTICLEDGER_BUDGET_AGENT",
        "daily": "AGENTICLEDGER_BUDGET_DAILY",
        "user": "AGENTICLEDGER_BUDGET_USER",
        "status": "AGENTICLEDGER_BUDGET_STATUS",
    },
    "replay": {
        "api_key": "AGENTICLEDGER_REPLAY_API_KEY",
        "api_key_file": "AGENTICLEDGER_REPLAY_API_KEY_FILE",
        "openai_url": "AGENTICLEDGER_REPLAY_OPENAI_URL",
        "openai_key": "AGENTICLEDGER_REPLAY_OPENAI_KEY",
        "openai_key_file": "AGENTICLEDGER_REPLAY_OPENAI_KEY_FILE",
        "anthropic_url": "AGENTICLEDGER_REPLAY_ANTHROPIC_URL",
        "anthropic_key": "AGENTICLEDGER_REPLAY_ANTHROPIC_KEY",
        "anthropic_key_file": "AGENTICLEDGER_REPLAY_ANTHROPIC_KEY_FILE",
    },
}

TEMPLATE = '''\
# Agentic Ledger configuration.
#
# Uncomment what you need; everything commented out keeps its default.
# Environment variables with the same meaning always win over this file,
# so container deployments are unaffected. Start the proxy with
# `agenticledger start` (background) or `agenticledger serve` (foreground).

[proxy]
# port = 8000
# upstream_url = "https://api.openai.com"   # or https://api.anthropic.com, or LM Studio
# db = "sqlite:///agenticledger.db"         # or postgresql://...
# completion_promise = "COMPLETE"           # lets loops declare victory

[keys]
# Prefer *_file: the file's contents are the key, so no secret lives here.
# api_key_file = "~/.agenticledger/api.key"      # dashboard/admin access
# ingest_key_file = "~/.agenticledger/ingest.key"  # closes the open relay

[budgets]
# daily = 25.0        # whole-ledger daily ceiling, USD
# session = 5.0       # per-session ceiling
# user = 10.0         # per-user daily ceiling
# status = 429        # or 402 — HTTP answer when a wall blocks a call

[replay]
# Same-provider replay through the proxy's own upstream:
# api_key_file = "~/.agenticledger/replay.key"
# Cross-provider / free local replay (LM Studio):
# openai_url = "http://localhost:1234"
# openai_key = "lm-studio"

[env]
# Escape hatch: any other AGENTICLEDGER_* variable, verbatim.
# AGENTICLEDGER_RETENTION_DAYS = "30"
'''


def find_config(explicit: Optional[str] = None) -> Optional[Path]:
    """The config file in effect as an ABSOLUTE path, or None. Order:
    AGENTICLEDGER_CONFIG, ./agenticledger.toml, ~/.agenticledger/config.toml.

    Absolute because two shells in different directories can resolve the
    cwd candidate to two different files; a bare "agenticledger.toml" in
    output let an operator edit one file while the proxy read another."""
    candidates = [
        explicit or os.environ.get("AGENTICLEDGER_CONFIG"),
        "agenticledger.toml",
        Path.home() / ".agenticledger" / "config.toml",
    ]
    for cand in candidates:
        if cand and Path(cand).expanduser().is_file():
            return Path(cand).expanduser().resolve()
    return None


def apply_config(explicit: Optional[str] = None) -> Optional[Path]:
    """Load the config file (if any) into the environment via setdefault —
    an env var that is already set always wins. Returns the path used."""
    global load_attempted, loaded_path
    path = find_config(explicit)
    load_attempted = True
    loaded_path = path
    if path is None:
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"Could not read {path}: {exc}") from None
    for section, keys in data.items():
        if not isinstance(keys, dict):
            logger.warning("%s: top-level key %r ignored (expected a [section])", path, section)
            continue
        for key, value in keys.items():
            env_name = _KEY_MAP.get(section, {}).get(key)
            if env_name is None and section == "env":
                env_name = key
                if not env_name.startswith("AGENTICLEDGER_"):
                    logger.warning("%s: [env] %r ignored (only AGENTICLEDGER_* allowed)", path, key)
                    continue
            if env_name is None:
                logger.warning("%s: unknown key [%s] %s ignored", path, section, key)
                continue
            if key.endswith("_file") or key.endswith("_FILE"):
                value = str(Path(str(value)).expanduser())
            if env_name not in os.environ:
                os.environ[env_name] = _plain(value)
                applied_from_file.add(env_name)
    return path


def _plain(value: Any) -> str:
    """TOML value → the string the env pipeline expects."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value == int(value):
        return str(value)  # keep 25.0 as-is; float parsers accept it
    return str(value)


def _split_key(dotted: str) -> tuple[str, str]:
    if "." not in dotted:
        raise SystemExit(
            f"Use section.key — e.g. proxy.upstream_url, not {dotted!r}.\n"
            f"Known sections: {', '.join(_KEY_MAP)}")
    section, _, key = dotted.partition(".")
    if section not in _KEY_MAP and section != "env":
        raise SystemExit(f"Unknown section {section!r}. "
                         f"Known sections: {', '.join(_KEY_MAP)}, env")
    if section != "env" and key not in _KEY_MAP[section]:
        known = ", ".join(_KEY_MAP[section])
        raise SystemExit(f"Unknown key {key!r} in [{section}]. Known: {known}")
    return section, key


def _toml_value(raw: str) -> str:
    """Render a shell-supplied value as TOML: numbers and booleans bare,
    everything else quoted."""
    low = raw.strip().lower()
    if low in ("true", "false"):
        return low
    try:
        float(raw)
        return raw.strip()
    except ValueError:
        return '"' + raw.replace('\\', '\\\\').replace('"', '\\"') + '"'


def set_value(dotted: str, value: Optional[str], path: Optional[str] = None) -> Path:
    """Set (or with value=None, comment out) one key, editing the file in
    place so the template's comments survive — a commented-out line for the
    same key is uncommented and reused rather than duplicated.

    When no config file exists anywhere, the new file is created at
    ~/.agenticledger/config.toml, the path every directory falls back to.
    Creating ./agenticledger.toml here instead would bring cwd-dependence
    back: a later `config set` or service start from another folder would
    resolve a different file. An existing ./agenticledger.toml is an
    explicit choice and keeps being edited in place."""
    section, key = _split_key(dotted)
    target = Path(path).expanduser() if path else (
        find_config() or Path.home() / ".agenticledger" / "config.toml")
    target = target.resolve()
    if not target.is_file():
        init_config(str(target))
    lines = target.read_text(encoding="utf-8").splitlines()

    new_line = f"{key} = {_toml_value(value)}" if value is not None else None
    in_section = False
    section_start = -1
    last_in_section = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_section = stripped == f"[{section}]"
            if in_section:
                section_start = i
            continue
        if not in_section:
            continue
        last_in_section = i
        # Match the key whether it is live or still commented out.
        bare = stripped.lstrip("#").strip()
        if bare.split("=")[0].strip() == key:
            if new_line is None:
                lines[i] = f"# {bare}" if not stripped.startswith("#") else line
            else:
                lines[i] = new_line
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return target

    if new_line is None:
        return target  # nothing to unset
    if section_start < 0:
        lines += ["", f"[{section}]", new_line]
    else:
        insert_at = (last_in_section + 1) if last_in_section > section_start else (section_start + 1)
        lines.insert(insert_at, new_line)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def get_value(dotted: str, path: Optional[str] = None) -> Optional[str]:
    """The value currently in the file (not the effective value — the
    settings page and `status` speak for what is actually running)."""
    section, key = _split_key(dotted)
    target = Path(path).expanduser().resolve() if path else find_config()
    if target is None or not target.is_file():
        return None
    data = tomllib.loads(target.read_text(encoding="utf-8"))
    val = (data.get(section) or {}).get(key)
    return None if val is None else str(val)


def init_config(path: str = "agenticledger.toml") -> Path:
    """Write the commented template. Refuses to overwrite an existing file."""
    target = Path(path).expanduser().resolve()
    if target.exists():
        raise SystemExit(f"{target} already exists — not overwriting it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE, encoding="utf-8")
    with os.fdopen(os.open(target, os.O_RDONLY), "r"):
        pass  # ensure it landed
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)  # 600: it may hold budgets/paths
    return target
