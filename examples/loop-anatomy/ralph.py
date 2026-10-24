"""Ralph: a fresh conversation every iteration. The model has no memory of
the last iteration; the files on disk (and a notebook it writes) are the
only things that carry over. Run it, then react.py, then compare the two
in the ledger.

    python ralph.py [--case textkit|dispute] [--max-iterations 8] [--max-calls 12]
"""

import argparse
import os
import sys
import time

if "--case" in sys.argv:
    os.environ["LOOP_CASE"] = sys.argv[sys.argv.index("--case") + 1]

from common import (PROMISE, RALPH_RULE, SYSTEM, TASK, Recorder, converse,  # noqa: E402
                    judge, label_name, make_workspace, report, run_name)

PROMPT = TASK + RALPH_RULE


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=os.environ.get("LOOP_CASE", "textkit"))
    ap.add_argument("--max-iterations", type=int, default=8)
    ap.add_argument("--max-calls", type=int, default=12, help="per iteration")
    args = ap.parse_args()

    run_id = run_name("ralph")
    ws = make_workspace(run_id)
    rec = Recorder(run_id, "ralph")
    rec.label(label_name("Ralph"), icon="wrench", color="orange")
    started = time.time()
    why = "max iterations"

    for it in range(1, args.max_iterations + 1):
        print(f"iteration {it}: fresh conversation")
        messages = [{"role": "user", "content": PROMPT}]
        text, stop = converse(rec, ws, messages, SYSTEM, iteration=it,
                              session_id=f"{run_id}-i{it}", max_calls=args.max_calls, log=print)
        if PROMISE in text:
            why = "completion promise"
            break
        if judge(ws)[0]:
            why = "done, judged by the case"
            break
        if stop.startswith("refused"):
            why = stop
            break
    else:
        it = args.max_iterations

    report(rec, ws, started, iterations=it, why=why)


if __name__ == "__main__":
    main()
