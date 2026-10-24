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


# The hook events the ledger records: what ran, what was asked or
# denied, sub-agents, compaction, the session's start and end. PreToolUse
# is left out on purpose (PostToolUse carries the same input plus the
# result), as is anything that fires per keystroke or per message shown.
HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
               "PermissionRequest", "PermissionDenied", "SubagentStart", "SubagentStop",
               "Stop", "PreCompact", "PostCompact", "SessionEnd")
_HOOK_MARK = "_agenticledger"


def connect(framework: str, port: str = "8000", app_id: Optional[str] = None,
            hooks: bool = False, remove_hooks: bool = False) -> int:
    if framework == "claude-code" and (hooks or remove_hooks):
        return _connect_claude_hooks(port, remove=remove_hooks)
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


def hook_entry(url: str) -> dict:
    """One http hook pointing at the ledger. The ingest key, when the
    ledger has one, rides a header filled from the environment at hook
    time (Claude Code interpolates only variables listed in
    allowedEnvVars), so the settings file never holds the secret."""
    return {
        "type": "http",
        "url": url,
        "timeout": 5,
        "headers": {"x-agenticledger-ingest-key": "$AGENTICLEDGER_INGEST_KEY"},
        "allowedEnvVars": ["AGENTICLEDGER_INGEST_KEY"],
        _HOOK_MARK: True,
    }


def _connect_claude_hooks(port: str, remove: bool = False) -> int:
    """Claude Code hooks for the on-machine record, written to the USER
    settings (~/.claude/settings.json) so they follow the person into
    every project. They work even when IT pins Claude Code's model traffic
    to a gateway, because hooks never touch the model path. Entries the
    ledger wrote are marked and replaced on re-run; other hooks are kept."""
    path = Path.home() / ".claude" / "settings.json"
    url = f"{_proxy_url(port)}/v1/hooks/claude-code"

    def mutate(cfg: dict) -> None:
        hooks = cfg.setdefault("hooks", {})
        for event in list(hooks.keys()):
            groups = hooks.get(event) or []
            kept = []
            for group in groups:
                if not isinstance(group, dict):
                    kept.append(group)
                    continue
                inner = [h for h in (group.get("hooks") or [])
                         if not (isinstance(h, dict) and h.get(_HOOK_MARK))]
                if inner or not group.get("hooks"):
                    group = dict(group, hooks=inner) if group.get("hooks") is not None else group
                    kept.append(group)
            if kept:
                hooks[event] = kept
            else:
                hooks.pop(event, None)
        if remove:
            if not hooks:
                cfg.pop("hooks", None)
            return
        for event in HOOK_EVENTS:
            hooks.setdefault(event, []).append({"hooks": [hook_entry(url)]})

    _merge_json(path, mutate)
    if remove:
        print(f"Removed the ledger's hooks from {path}.")
        return 0
    print(f"Wrote {path} (backup: settings.json.ledger-bak)")
    print(f"  {len(HOOK_EVENTS)} hooks -> {url}")
    print("  recorded: tools run (input, output, duration, failures), permissions asked")
    print("  or denied, sub-agents, compaction, session start and end.")
    print("  If the ledger has an ingest key, export AGENTICLEDGER_INGEST_KEY in the")
    print("  shell that starts Claude Code; the hooks read it from there.")
    print("Open a new Claude Code session to apply. Undo: agenticledger connect claude-code --remove-hooks")
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
