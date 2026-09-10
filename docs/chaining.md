# Chaining a compressor behind the ledger

Tools like [Headroom](https://docs.headroomlabs.ai/docs) shrink what
your agent sends to the model. The ledger records and governs what your
agent actually said. They compose, because both are honest proxies:

```
agent  ->  ledger (record, refuse)  ->  compressor (shrink)  ->  provider
```

## Setup

Point the ledger's upstream at the compressor instead of the provider:

```bash
AGENTICLEDGER_UPSTREAM_URL=http://localhost:<compressor-port> agenticledger start
```

Your agent keeps pointing at the ledger exactly as before. Nothing else
changes.

## What each side sees, stated precisely

- The ledger records what the AGENT sent: full prompts, run identity,
  budgets and ceilings enforced before anything reaches the compressor.
- The provider bills what the COMPRESSOR sent, which is smaller.
- That difference is the compressor's savings, measured by your own
  ledger instead of promised by anyone's marketing: compare the
  ledger's token counts against the provider's usage report for the
  same period.

The chain mechanics are covered by a verified test: a call through two
chained proxies arrives intact, is recorded on both hops, and keeps its
run attribution on the front ledger.

## Caveats, honestly

- The ledger's cost figures price the traffic the agent sent. With a
  compressor downstream, your actual bill is lower; the gap is the
  measurement, not an error. The cache audit reads the provider's
  usage report where present, so cached-token reports pass through.
- A compressor that rewrites prompts changes what the model actually
  received. The ledger's record remains the truth of what your agent
  said; the compressor's logs are the truth of what the model heard.
  Keep both when debugging.
- Chain order matters: the ledger goes FIRST, so its refusals cost
  nothing and its record is complete even for calls the compressor
  would have shrunk.
