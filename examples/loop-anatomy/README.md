# Loop anatomy: Ralph vs ReAct, recorded

Two agent loops, one task, one model, one toolbox. The only thing that
differs is the loop's shape, and Agentic Ledger records both so you can
replay them side by side.

- **Ralph** (`ralph.py`): a fresh conversation every iteration. The model
  remembers nothing from the last round; the files on disk and a
  `progress.md` notebook it writes are the only memory.
- **ReAct** (`react.py`): one conversation that keeps going. Think, call a
  tool, read the result, repeat; every exchange stays in the context.

The task is small and real: `fixture/textkit.py` has four helpers with
planted bugs, and `fixture/test_textkit.py` fails eight tests until they
are fixed. Each run works on its own copy under `runs/`.

## The cases

Two tasks ship with the kit, each in `cases/<name>/`:

- **dispute** (the one to present): a disputed card transaction. The agent
  reads a folder of invented bank documents (dispute form, statements,
  merchant record, network rules, policy, prior disputes, a bulletin, two
  noise forms) and writes a decision memo: provisional credit of 482.00 by
  26 March 2026 under DP-4.2 and reason code 10.4, no escalation.
  `cases/dispute/check.py` is the judge of done; `ANSWER-KEY.md` explains
  the answer and the traps. Tools: `list_docs`, `search`, `read_doc`,
  `write_file`.
- **textkit**: four planted bugs in a small Python module; the unit tests
  are the judge. Tools: `list_files`, `read_file`, `write_file`,
  `run_tests`.

Same task text to both loops in either case; the only difference is the
"how you work" paragraph each loop adds (`RALPH_RULE`, `REACT_RULE` in
the case module). The Compare page shows that paragraph as the one
difference.

## Run the demo pair (no key, no cost, as long as you like)

```bash
cd examples/loop-anatomy && ./run-demo.sh
```

This starts a scripted model that answers from a script (only the
model's words are scripted; every document read, every search, the memo
and its judgement are real), starts a named ledger instance `demo` with
it as the upstream (own port 8020, own database; your everyday ledger is
not touched), then runs Ralph and ReAct on the dispute case through it.
Watch http://localhost:8020/app while it runs: the calls arrive live.
Both loops run at the same time, so both lanes move live and the race is
visible: ReAct finishes first (51 calls in one conversation, fast and
expensive), Ralph well behind (75 calls over 13 rounds, slow and cheap).
`LOOP_DELAY=6 ./run-demo.sh` sets the seconds per model call (default
8: about 7 minutes for ReAct and 10 for Ralph; at 6, about 5 and 7.5). `./run-demo.sh --stop` stops
the demo ledger and the scripted model.

Ralph's rounds 4 and 6 are scripted as forgetting rounds: each writes a
note that leaves out something the next round needs, so rounds 5 and 7
have to open a document again. That is the Ralph failure mode on purpose. The runs are plain `ralph-*`
and `react-*`, recorded under the model id `gpt-6-astra` and priced at
GPT-6 Astra's standard rates (10 in, 50 out per million tokens); the
words in them come from the script, not from that model, and that is
yours to say out loud.

## Record a real pair

**With Claude Code (a subscription is enough, no API key).** The shell
scripts run `claude -p` through the ledger's run wrapper on the textkit
case. Ralph launches a fresh `claude -p` per iteration; ReAct is one
`claude -p` conversation.

```bash
cd examples/loop-anatomy && ./ralph-claude.sh && ./react-claude.sh
```

Claude Code carries about 55k tokens of its own system prompt and tool
definitions into every call and adds a couple of small housekeeping calls
per conversation; the ledger records them as they are.

**With an API key.** The Python scripts talk to the model directly
through the ledger with the case's own tools:

```bash
export ANTHROPIC_API_KEY=...        # passed through the ledger untouched
export AGENTICLEDGER_URL=http://localhost:8000
python ralph.py --case dispute
python react.py --case dispute
```

Either way each run prints a result line (the judge's verdict,
iterations, calls, seconds) and a link to its run. Record a pair with one
method, not one of each: the comparison is only fair when the model and
the tools match.

## Present: ten minutes

Open the ledger, Loop Lens, and pick the two runs with the compare button.

**Anatomy, five minutes.** Each lane is a stage with the model's pad on
the left, the world on the right, and a gate between them. The pad is
the exact context the model could see on that call, card by card, from
the stored messages: the prompt, what the model said, the tools it
called, what came back. The world lists the tools it was offered and
the files its calls have touched. A small ring says which move is
happening: read, think, act, observe.

Press Play. On every call the model's own cards drop onto the pad, the
tool call crosses the gate, the tool lights up, and the result crosses
back and joins the pad. In ReAct the pad only ever grows; the token
count in its header climbs call after call. In Ralph the pad is marked
dropped at the end of every iteration and the next call starts with an
empty pad; the only thing that carries over is what the model wrote to
progress.md, and you can watch that file light up as it is written at
the end of one iteration and read at the start of the next.

Under the stage, the reel is the map: one column per call, as tall as
the context read, on one scale for both lanes. ReAct is a staircase;
Ralph is a comb with a dashed cut at every iteration. The line under
each name is the ledger's own verdict, worked out from the calls, not
from the label: "Fresh context every iteration (Ralph style)" against
"One continuous conversation (ReAct style)".

Drag the reel to scrub, click a conversation bar to jump to its start,
double-click the pad to open the conversation and read every card in
full. Slow it down with the speed buttons when the room needs a beat.

**Results, five minutes.** Scroll to the table.

- Cost, calls, tokens in and out, cache reads, flags, duration, with the
  delta between the two. Iterations and calls are shape, not quality, so
  they carry no color; money, time and flags do.
- Cost per iteration on a shared scale: Ralph pays a similar price each
  round; ReAct's rounds get dearer as the context grows.
- Prompt drift shows the one paragraph that differs between the two
  opening instructions: the "how you work" rule. Same task, same tools,
  same model.
- The scripts' own result lines say whether the tests passed. The ledger
  does not judge that; it records what the loop did.

## What is fair about this

Same task text (`fixture/TASK.md`), same system prompt, same four tools,
same model, same stopping rule (the `<promise>DONE</promise>` line, with a
call cap as the safety net). Change one of them and the ledger's prompt
drift panel will show it.
