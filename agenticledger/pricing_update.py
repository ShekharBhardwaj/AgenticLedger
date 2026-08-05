"""`agenticledger pricing update`: refresh price packs without upgrading.

Fetches the current packs from the repository and installs them under
~/.agenticledger/pricing/, where they override the built-in packs at
load time. Explicitly user-initiated: the ledger never fetches anything
on its own, and this command is the only network call the CLI makes.

Downloaded data is strictly validated before a single byte is
installed; a bad or truncated response changes nothing.
"""

import json
import sys
import urllib.request
from pathlib import Path

USER_PACK_DIR = Path.home() / ".agenticledger" / "pricing"

_LISTING_URL = ("https://api.github.com/repos/ShekharBhardwaj/AgenticLedger/"
                "contents/agenticledger/pricing_data")
_ALLOWED_MODEL_KEYS = {"input", "output", "cache_read", "cache_write", "note"}


class PackValidationError(ValueError):
    pass


def validate_pack(name: str, pack: dict) -> int:
    """The same strictness the test suite applies to the repo's packs.
    Returns the model count; raises with the exact problem named."""
    if not isinstance(pack, dict) or not pack.get("provider"):
        raise PackValidationError(f"{name}: missing provider")
    models = pack.get("models")
    if not isinstance(models, dict) or not models:
        raise PackValidationError(f"{name}: no models")
    for pattern, spec in models.items():
        where = f"{name}: {pattern!r}"
        if pattern != pattern.lower():
            raise PackValidationError(f"{where}: patterns are lowercase")
        if not isinstance(spec, dict):
            raise PackValidationError(f"{where}: not an object")
        unknown = set(spec) - _ALLOWED_MODEL_KEYS
        if unknown:
            raise PackValidationError(f"{where}: unknown keys {sorted(unknown)}")
        for key in ("input", "output"):
            if not isinstance(spec.get(key), (int, float)) or spec[key] < 0:
                raise PackValidationError(f"{where}: bad {key}")
    return len(models)


def _fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def update(fetch=_fetch_json, out: Path = None) -> int:
    """Fetch, validate everything first, then install atomically-enough:
    nothing is written until every pack passed."""
    out = out or USER_PACK_DIR
    listing = fetch(_LISTING_URL)
    entries = [e for e in listing
               if isinstance(e, dict) and e.get("name", "").endswith(".json")]
    if not entries:
        print("No packs found at the repository; nothing changed.",
              file=sys.stderr)
        return 1

    validated: list[tuple[str, dict, int]] = []
    for entry in entries:
        pack = fetch(entry["download_url"])
        count = validate_pack(entry["name"], pack)  # raises on bad data
        validated.append((entry["name"], pack, count))

    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, pack, count in validated:
        tmp = out / f".{name}.tmp"
        tmp.write_text(json.dumps(pack, indent=2) + "\n")
        tmp.replace(out / name)
        total += count

    print(f"Installed {len(validated)} pack(s), {total} models, to {out}.")
    print("These override the built-in prices. The proxy reads prices at "
          "startup, so restart it to apply: agenticledger stop && "
          "agenticledger start.")
    print(f"To return to the built-in prices, delete {out}.")
    return 0
