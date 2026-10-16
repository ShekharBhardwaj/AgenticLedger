"""ReAct: one conversation that keeps going. Every step the model thinks,
calls a tool, reads the result, and the whole exchange stays in its
context for the next step. Run it, then ralph.py, then compare the two in
the ledger.

    python react.py [--max-calls 40]
"""

import argparse
import time

from common import PROMISE, TASK, Recorder, converse, make_workspace, report

SYSTEM = "You are a careful software engineer working with the tools you are given."

PROMPT = TASK + f"""
How you work: one continuous conversation. Think, call a tool, read the
result, and repeat until every test passes. Then reply with exactly
{PROMISE} and nothing else.
"""


class StepCounter:
    """Each model call is one iteration of the ReAct loop, so the ledger
    numbers them: iteration 1, 2, 3 ... all in one session."""

    def __init__(self, rec):
        self.rec, self.n = rec, 0
        self._ask = rec.ask

    def ask(self, messages, system, *, iteration, session_id):
        self.n += 1
        return self._ask(messages, system, iteration=self.n, session_id=session_id)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=40)
    args = ap.parse_args()

    run_id = f"react-{time.strftime('%m%d-%H%M%S')}"
    ws = make_workspace(run_id)
    rec = Recorder(run_id, "react")
    rec.label("ReAct", icon="brain", color="blue")
    counter = StepCounter(rec)
    rec.ask = counter.ask
    started = time.time()

    print("one conversation, step by step")
    messages = [{"role": "user", "content": PROMPT}]
    text, stop = converse(rec, ws, messages, SYSTEM, iteration=1, session_id=run_id,
                          max_calls=args.max_calls, log=print)
    why = "completion promise" if PROMISE in text else stop
    report(rec, ws, started, iterations=counter.n, why=why)


if __name__ == "__main__":
    main()
