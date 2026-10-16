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

## Record the two runs

You need a running ledger and an Anthropic key. The scripts talk to the
model through the ledger, so the ledger sees every call.

```bash
pip install anthropic httpx
export ANTHROPIC_API_KEY=...        # passed through the ledger untouched
export AGENTICLEDGER_URL=http://localhost:8000
python ralph.py
python react.py
```

Each script prints a result line (tests pass or not, iterations, model
calls, seconds) and a link to its run. Both runs are filed under the
`loop-anatomy` project with their own icon: a wrench for Ralph, a brain
for ReAct.

Running on Bedrock? Point `AGENTICLEDGER_URL` at a ledger set up for
Bedrock (see `docs/integrations/bedrock.md`) and swap the client in
`common.py` for `anthropic.AnthropicBedrock(base_url=LEDGER)`.

## Present: ten minutes

Open the ledger, Loop Lens, and pick the two runs with the compare button.

**Anatomy, five minutes.** Press Play. The two lanes replay the recorded
calls from the same start, each at its own pace.

- The pile on the left is what the model read on that call. Watch Ralph's
  pile empty and refill at each iteration (the amber "fresh context"
  sweep), while ReAct's pile climbs call after call.
- The green chip is the model's move: which tool it called. Under the
  lane, the dashed amber return path says "conversation ends: context
  dropped"; the solid one says "result joins the conversation". That is
  the whole difference between the two loops, drawn from the record.
- The sentence under each name is derived from the calls: how many
  conversations, iterations, calls, and how the context behaved.
- Click any chip in the strip to jump to that call; double-click to open
  the conversation and read the actual prompt and tool results.

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
