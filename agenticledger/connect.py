"""
`agenticledger connect <framework>` — wire a framework to the ledger without
memorizing anyone's config schema.

Every framework needs the same one idea (point its base URL at the proxy),
but each hides it behind its own file, its own schema, and its own traps —
Docker installs where localhost means the container, providers that demand
model arrays with exact field names. A person shouldn't have to remember
any of that, so this command does. Each writer encodes a shape validated
against the real tool; when a framework's schema drifts, its own validator
message is the backstop, and everything written is printed so nothing is
mysterious. Existing files are backed up first.
"""

import json
import shutil
from pathlib import Path
from typing import Optional

FRAMEWORKS = ("claude-code", "bmad", "openclaw")


def _proxy_url(port: str) -> str:
    return f"http://localhost:{port}"


def connect(framework: str, port: str = "8000", app_id: Optional[str] = None) -> int:
    if framework in ("claude-code", "bmad"):
        return _connect_claude_settings(port, app_id or framework)
    if framework == "openclaw":
        return _connect_openclaw(port)
    print(f"Unknown framework {framework!r}. Known: {', '.join(FRAMEWORKS)}")
    return 2


def _merge_json(path: Path, mutate) -> dict:
    """Load-or-create a JSON file, back it up if it exists, apply mutate."""
    existing = {}
    if path.is_file():
        existing = json.loads(path.read_text(encoding="utf-8"))
        shutil.copy(path, str(path) + ".ledger-bak")
    mutate(existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return existing


def _connect_claude_settings(port: str, app_id: str) -> int:
    """Claude Code and BMAD (which rides on Claude Code): one settings file
    in the CURRENT project, merged — existing settings survive."""
    path = Path(".claude/settings.json")

    def mutate(cfg: dict) -> None:
        env = cfg.setdefault("env", {})
        env["ANTHROPIC_BASE_URL"] = _proxy_url(port)
        env["ANTHROPIC_CUSTOM_HEADERS"] = f"x-agenticledger-app-id: {app_id}"

    _merge_json(path, mutate)
    print(f"Wrote {path} — Claude Code in this project now goes through the "
          f"ledger at {_proxy_url(port)}, tagged app '{app_id}'.")
    print("Restart Claude Code in this project to apply.")
    return 0


def _connect_openclaw(port: str) -> int:
    """OpenClaw: override the native anthropic provider in
    ~/.openclaw/openclaw.json. Two traps this encodes so nobody has to
    remember them:
    - a Docker install (workspace path under /home/node) must reach the host
      as host.docker.internal, never localhost;
    - the provider override requires a models array of {id, name} objects —
      derived here from the models the config already uses."""
    path = Path.home() / ".openclaw" / "openclaw.json"
    if not path.is_file():
        print(f"{path} not found — is OpenClaw installed? (Run it once first.)")
        return 1
    cfg = json.loads(path.read_text(encoding="utf-8"))

    workspace = str(((cfg.get("agents") or {}).get("defaults") or {})
                    .get("workspace") or "")
    dockerized = workspace.startswith("/home/")
    host = "host.docker.internal" if dockerized else "127.0.0.1"
    # Path-segment attribution: OpenClaw cannot send custom headers, so the
    # run name rides in the URL itself.
    base_url = f"http://{host}:{port}/r/openclaw-main/1"

    model_ids = []
    defaults = ((cfg.get("agents") or {}).get("defaults") or {})
    for ref in (defaults.get("models") or {}):
        if ref.startswith("anthropic/"):
            model_ids.append(ref.split("/", 1)[1])
    primary = ((defaults.get("model") or {}).get("primary") or "")
    if primary.startswith("anthropic/") and primary.split("/", 1)[1] not in model_ids:
        model_ids.append(primary.split("/", 1)[1])
    if not model_ids:
        model_ids = ["claude-opus-4-20250514"]

    def pretty(mid: str) -> str:
        return mid.replace("-", " ").title().replace("Claude ", "Claude ")

    def mutate(c: dict) -> None:
        providers = c.setdefault("models", {}).setdefault("providers", {})
        providers["anthropic"] = {
            "baseUrl": base_url,
            "models": [{"id": m, "name": pretty(m)} for m in model_ids],
        }

    _merge_json(path, mutate)
    print(f"Wrote {path} (backup: openclaw.json.ledger-bak)")
    print(f"  anthropic provider → {base_url}")
    print(f"  models: {', '.join(model_ids)}")
    if dockerized:
        print("  (Docker install detected — using host.docker.internal, since "
              "localhost inside a container means the container.)")
    print("Restart OpenClaw to apply (docker restart <container>, or however "
          "you run it). Traffic lands under run 'openclaw-main'.")
    return 0
