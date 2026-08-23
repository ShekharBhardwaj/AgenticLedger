# The four-minute demo (recording script)

A recording kit for the release video: a timed narration script plus a
driver that generates each scene's traffic on cue. Everything on screen
is the real ledger doing its real job on real wire traffic. The only
fake thing is the model on the other end: a puppet upstream that answers
instantly and costs nothing, so a take is $0, deterministic, works
offline, and resets in five seconds for the next take. Costs, threads,
flags, refusals: all computed live by the proxy from the traffic the
driver sends.

## Setup (before recording, ~2 minutes)

Three terminals, plus a browser. Every terminal starts with a `cd` to
the repo root (the commands below assume you are there), and uses
`python3` (plain `python` does not exist on a stock Mac).

Terminal 1, the puppet model:

```bash
cd <path-to>/AgenticLedger
python3 demo/mock_upstream.py
```

Terminal 2, a fresh demo ledger on port 8003 (never your real one):

```bash
cd <path-to>/AgenticLedger
rm -f /tmp/demo-ledger.db
AGENTICLEDGER_PORT=8003 \
AGENTICLEDGER_DSN=sqlite:////tmp/demo-ledger.db \
AGENTICLEDGER_UPSTREAM_URL=http://localhost:9911 \
.venv/bin/python -m agenticledger.proxy
```

(The proxy must run on the repo's venv: `.venv/bin/python`, not the
system `python3`, which does not have the dependencies. The puppet and
the driver are dependency-free, so plain `python3` is fine for those.)

Terminal 3, the driver, idle for now. Browser: http://localhost:8003
(the DEMO ledger; your real one on :8000 stays out of frame) on Loop
Lens, big font, dark room.

Record the browser window only; keep terminal 3 on a second screen (or
splice its commands in as overlays). Dry-run the whole script once
before recording; reset between takes with the `rm -f` line and a proxy
restart.

## The script

**0:00. Cold open (35s).** No slides. The empty Loop Lens.

> "Someone left Claude Code running overnight and woke up to a $6,000
> bill. Nothing was watching, and nothing could say no. This is the
> flight recorder for AI agents: a proxy your agent's calls pass
> through. Zero code changes: you point a base URL at it. Watch."

**0:35. Act 1: the live loop and the spend meter (50s).** Terminal 3:

```bash
python3 demo/drive.py live 2
```

A `night-shift` tile appears; click it.

> "An overnight loop, one iteration every few seconds. Every call lands
> here the moment it happens: model, tokens, latency, cost. And this
> line is the bill before the bill: burning this much an hour, this
> much by 8am at this pace."

Point at the burn line. Then click **+ cost ceiling**, type `0.05`,
Enter.

> "So give it a number it cannot cross."

Within a few knocks the refusals land amber, `$0.0000`, and the meter
track glows. Point at the frozen spend figure.

> "The proxy is refusing the calls. The agent is still trying; it costs
> nothing. Raise the ceiling or clear it, calls flow again. The ceiling
> survives restarts; it lives in the ledger, not in memory."

Click **clear**; the next call sails through clean. Let the driver run
out quietly.

**1:25. Act 2: two ways to run an agent (55s).** Terminal 3:

```bash
python3 demo/drive.py react
python3 demo/drive.py ralph
```

Open the `react-style` run (it wears a `flagged` badge).

> "Two philosophies. ReAct: one long conversation, the agent thinks and
> calls tools in a single thread. The ledger stitched those six calls
> into a thread and timed every tool. And look: it caught the agent
> re-running the same failing test three times. That is the stuck-loop
> flag: your agent does not crash, it loops, and every loop costs
> money."

Show the Flags panel: "Stuck loop suspected", the step it fired on.

> "Ralph: the opposite bet. Fresh context every iteration, no memory,
> just a prompt in a loop. Four clean iterations, no flags."

**2:20. Act 3: the argument, settled by numbers (60s).** In the
sidebar, click ⇆ on `react-style`, then ⇆ on `ralph-style`.

> "So which is better? Stop arguing, read the ledger."

Walk the compare table top to bottom, deltas colored:

> "Ralph cost 41% more here and burned three times the output tokens;
> fresh context is not free. But: zero flags against one stuck loop,
> and half the wall-clock. The ribbons show where react's money went:
> one amber tower, the stuck iteration. And down here, prompt drift:
> the exact diff between what the two runs were told. 'Did the new
> prompt help' is a number now, not a feeling."

**3:20. Close (40s).** Click Sessions, then Reports.

> "Everything files itself: runs group their sessions, projects group
> the work, reports scope to either. The marks tell you whose wire each
> call rode: OpenAI, Anthropic, Bedrock direct with the ledger doing
> the signing. And all of it is local-first: this page never left this
> machine. One pip install, and your agents are on the record."

```bash
pip install agentic-ledger
```

End on the Loop Lens with the tiles.

## Between takes

- The driver prints every call's status; a refused knock shows the
  reason. Nothing in the acts depends on timing tighter than ~3s.
- Skipped the ceiling beat? The wall also demos from **⊘ block calls**
  on any run, same amber language.
- For the next take: `rm -f /tmp/demo-ledger.db`, restart the
  terminal-2 proxy, and the board is factory-new in five seconds.
