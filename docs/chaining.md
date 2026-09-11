# Chaining a compressor behind the ledger (optional)

This is entirely optional. Agentic Ledger never compresses or rewrites
your traffic, and nothing here is on by default. If you already run a
context compressor like [Headroom](https://docs.headroomlabs.ai/docs) (a
separate tool that shrinks what your agent sends to the model, to save
tokens), this page shows how to put it behind the ledger so the two work
together. If you do not run one, ignore this page: the ledger works
exactly the same without it.

The ledger's job stays the same either way: record what your agent
actually said, and refuse calls that break a budget or ceiling. The
compressor's job is separate: make the request smaller before it reaches
the model. They line up like this:

```
your agent  ->  the ledger  ->  the compressor  ->  the model provider
                (records,        (shrinks the        (charges for the
                 can say no)      request)            smaller request)
```

## What the words mean

- **Compressor**: a separate tool you choose to run that rewrites a
  request to use fewer tokens before it reaches the model. Headroom is
  one. The ledger is not a compressor and never becomes one.
- **Upstream**: whatever the ledger forwards your agent's calls to. By
  default that is the model provider directly. To insert a compressor,
  you point the ledger's upstream at the compressor instead, and the
  compressor forwards to the provider.
- **The chain**: the ordered path a call travels. Here it is
  agent, then ledger, then compressor, then provider.

## Turning it on (opt in)

You run the compressor yourself (see its own docs). Then point the
ledger's upstream at it, once, when you start the ledger:

```bash
AGENTICLEDGER_UPSTREAM_URL=http://localhost:<compressor-port> agenticledger start
```

Your agent keeps pointing at the ledger exactly as before. To turn it
back off, start the ledger without that variable: the chain is gone and
nothing about your setup changed.

## What each side sees

- The ledger records what your AGENT sent: the full prompt, the run it
  belongs to, and the budgets and ceilings it enforces before anything
  reaches the compressor.
- The provider charges for what the COMPRESSOR sent, which is smaller.
- The difference between those two is the compressor's real savings,
  measured by your own ledger instead of taken on faith from a vendor's
  marketing. Compare the ledger's token counts against the provider's
  usage report for the same period.

## The honest caveats

- **The ledger's cost figure will read higher than your actual bill
  while a compressor is in the chain.** This is expected, not a bug: the
  ledger prices the full request your agent sent, and the provider
  charged for the smaller one the compressor sent. That gap is the
  savings. (Without a compressor, the ledger's cost matches your bill,
  and a mismatch there IS a bug we want reported.)
- A compressor changes what the model actually received. The ledger's
  record stays the truth of what your agent said; the compressor's own
  logs are the truth of what the model read. Keep both when debugging.
- Order matters: the ledger goes FIRST, so its refusals still cost
  nothing and its record is complete even for calls the compressor
  would have shrunk.

## You do not need a compressor to see where you would save

The ledger tells you whether compression would help before you add
anything: a run's Cache tab reports the repeat-discount you were
eligible for and did not receive, with the dollar figure and the fix.
Read that first. If it says a run wastes real money on repeated context,
that is when a compressor earns its place, and the chain above lets your
own numbers confirm it did.

The chain mechanics are covered by tests/test_chaining.py: a call
through two real proxy pipelines arrives intact, is recorded on both
hops, and keeps its run attribution on the front ledger.
