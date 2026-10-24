"""Check a decision.md against the answer key: amount, deadline, basis,
escalation. Exit 0 when every fact is right, 1 otherwise, printing what
was missed. Used by the demo scripts as the judge of done.

    python check.py path/to/decision.md
"""

import re
import sys
from pathlib import Path


def check(text: str) -> list[str]:
    t = text.lower()
    misses = []
    amounts = re.findall(r"\$?\s?(\d[\d,]*\.\d{2})", text)
    if "482.00" not in amounts:
        misses.append("amount 482.00 not stated")
    for wrong in ("611.95", "626.94"):
        if wrong in amounts:
            misses.append(f"wrong total {wrong} credited")
    for line in t.splitlines():
        if "129.95" in line and "credit" in line and "not credit" not in line and "not be credit" not in line:
            misses.append("the 2 March 129.95 charge was credited")
            break
    if not re.search(r"26(th)?\s+march\s+2026|2026-03-26|march\s+26,?\s+2026", t):
        misses.append("deadline 26 March 2026 not stated")
    if "dp-4.2" not in t:
        misses.append("clause DP-4.2 not cited")
    if "10.4" not in t:
        misses.append("reason code 10.4 not cited")
    if re.search(r"escalat\w*[^.\n]*(required|needed|yes)", t) and not re.search(r"(no|not)\s+(escalation|required|needed)", t):
        misses.append("escalation wrongly required")
    if not re.search(r"escalat", t):
        misses.append("escalation decision missing")
    return misses


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "decision.md")
    if not path.is_file():
        print(f"{path} does not exist")
        return 1
    misses = check(path.read_text())
    if misses:
        print("DECISION INCOMPLETE OR WRONG")
        for m in misses:
            print(" -", m)
        return 1
    print("DECISION CORRECT: 482.00 by 26 March 2026 under DP-4.2 / 10.4, no escalation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
