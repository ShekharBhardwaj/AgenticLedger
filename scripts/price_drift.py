"""Weekly price-drift check against the community price database.

Compares OUR pricing packs (agenticledger/pricing_data/*.json) against
LiteLLM's community-maintained model_prices_and_context_window.json
(MIT, github.com/BerriAI/litellm), which hundreds of contributors keep
current. Only models our packs carry are compared; drift is reported,
never auto-applied — a human reviews the PR, because prices are money.

Run locally:  python scripts/price_drift.py
CI runs it weekly and opens a review PR when drift is found.

Conservative by design: a drift is reported only when the community file
gives an unambiguous answer (an exact key match, or every dated variant
of the model agreeing on the price). Disagreeing variants are listed as
"ambiguous" for human eyes rather than asserted as drift.
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

LITELLM_URL = ("https://raw.githubusercontent.com/BerriAI/litellm/main/"
               "model_prices_and_context_window.json")
PACK_DIR = Path(__file__).resolve().parent.parent / "agenticledger" / "pricing_data"


def _norm(s: str) -> str:
    return s.lower().replace(".", "-")


def load_packs() -> dict[str, dict]:
    ours: dict[str, dict] = {}
    for pack_file in sorted(PACK_DIR.glob("*.json")):
        pack = json.loads(pack_file.read_text())
        for pattern, spec in pack.get("models", {}).items():
            ours[pattern] = {"input": float(spec["input"]),
                             "output": float(spec["output"]),
                             "pack": pack_file.name}
    return ours


def community_prices(raw: dict, pattern: str) -> tuple[str, tuple[float, float] | None]:
    """(verdict, (in_per_M, out_per_M)|None) for one of our patterns.

    verdict: "exact" | "unanimous" | "ambiguous" | "missing".
    LiteLLM prices are per token; ours are per million.
    """
    def price_of(entry) -> tuple[float, float] | None:
        i, o = entry.get("input_cost_per_token"), entry.get("output_cost_per_token")
        if i is None or o is None:
            return None
        # A zero price on a paid model is a free-tier or placeholder entry
        # in the community file, not a reprice — not comparable evidence.
        if i <= 0 or o <= 0:
            return None
        return (round(i * 1_000_000, 6), round(o * 1_000_000, 6))

    exact = raw.get(pattern) or raw.get(_norm(pattern))
    if exact and (p := price_of(exact)):
        return "exact", p

    matches = []
    want = _norm(pattern)
    for key, entry in raw.items():
        if want in _norm(key) and (p := price_of(entry)):
            matches.append(p)
    if not matches:
        return "missing", None
    if len(set(matches)) == 1:
        return "unanimous", matches[0]
    return "ambiguous", None


def apply_drift(pattern: str, pack_name: str, theirs: tuple[float, float]) -> None:
    """Write the community values onto the model's line in its pack.
    Line-targeted so the hand-formatted file keeps its shape. The golden
    test then fails on purpose until a human updates it: pack change and
    golden change travel together, and that pairing is the review."""
    path = PACK_DIR / pack_name
    text = path.read_text()
    line_re = re.compile(
        r'("%s"\s*:\s*\{[^}]*"input"\s*:\s*)[0-9.]+([^}]*"output"\s*:\s*)[0-9.]+'
        % re.escape(pattern))
    new_text, n = line_re.subn(r"\g<1>%s\g<2>%s" % (theirs[0], theirs[1]), text)
    if n != 1:
        raise SystemExit(f"could not target {pattern} in {pack_name}")
    path.write_text(new_text)


def main() -> int:
    apply_mode = "--apply" in sys.argv
    with urllib.request.urlopen(LITELLM_URL, timeout=30) as resp:
        raw = json.load(resp)
    ours = load_packs()

    drifts, ambiguous, missing = [], [], []
    for pattern, spec in sorted(ours.items()):
        verdict, theirs = community_prices(raw, pattern)
        if verdict in ("exact", "unanimous"):
            if (spec["input"], spec["output"]) != theirs:
                drifts.append((pattern, spec, theirs, verdict))
        elif verdict == "ambiguous":
            ambiguous.append(pattern)
        else:
            missing.append(pattern)

    if drifts:
        print("## Price drift detected\n")
        print("| model | pack | ours (in/out per M) | community (in/out per M) | confidence |")
        print("|---|---|---|---|---|")
        for pattern, spec, theirs, verdict in drifts:
            print(f"| `{pattern}` | {spec['pack']} "
                  f"| {spec['input']} / {spec['output']} "
                  f"| {theirs[0]} / {theirs[1]} | {verdict} |")
        print("\nReview against the provider's own pricing page before "
              "merging; update the golden in tests/test_pricing_packs.py "
              "in the same commit, and bump the pack's checked date.")
        if apply_mode:
            for pattern, spec, theirs, _ in drifts:
                apply_drift(pattern, spec["pack"], theirs)
            print(f"\napplied {len(drifts)} drift(s) to the packs")
    else:
        print("No drift: every comparable model agrees with the community file.")

    if ambiguous:
        print(f"\nAmbiguous (variants disagree, human eyes needed): "
              f"{', '.join(ambiguous)}")
    if missing:
        print(f"\nNot in the community file (nothing to compare): "
              f"{', '.join(missing)}")

    return 1 if drifts else 0


if __name__ == "__main__":
    sys.exit(main())
