"""Ralph: a fresh conversation every iteration. The model has no memory of
the last iteration; the files on disk (and a progress notebook it writes)
are the only things that carry over. Run it, then react.py, then compare
the two in the ledger.

    python ralph.py [--max-iterations 8] [--max-calls 12]
"""

import argparse
import time

from common import PROMISE, TASK, Recorder, converse, make_workspace, report

SYSTEM = "You are a careful software engineer working with the tools you are given."

PROMPT = TASK + f"""
How you work: this is a loop that starts you over with an empty memory on
every iteration. progress.md is your notebook. Read it first, run the
tests, fix ONE thing, then write to progress.md what you did and what is
still failing, and stop. If every test passes, reply with exactly
{PROMISE} and nothing else.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-iterations", type=int, default=8)
    ap.add_argument("--max-calls", type=int, default=12, help="per iteration")
    args = ap.parse_args()

    run_id = f"ralph-{time.strftime('%m%d-%H%M%S')}"
    ws = make_workspace(run_id)
    rec = Recorder(run_id, "ralph")
    rec.label("Ralph", icon="wrench", color="orange")
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
        if stop.startswith("refused"):
            why = stop
            break
    else:
        it = args.max_iterations

    report(rec, ws, started, iterations=it, why=why)


if __name__ == "__main__":
    main()
