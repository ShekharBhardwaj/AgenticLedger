# Cost accuracy: how to check the ledger against your bill

The ledger's standing claim: **if our numbers disagree with your provider
console, that is a bug we want reported.** This page is the method for
holding us to it.

## What guards accuracy in CI

- Golden cost tests pin the math to known rates for representative calls
  on every commit, including both prompt-cache conventions and gateway
  model ids (`tests/test_pricing_packs.py`).
- Prices are reviewable data files (`agenticledger/pricing_data/`), and
  a price change must arrive together with its golden change in the same
  PR: the pair is the review surface.
- The full test suite runs against SQLite and real Postgres on every
  push; the store cannot round or truncate differently per backend
  (costs are double precision on both).

## The five-minute parity check

1. Pick a full past day (yesterday, UTC) with real traffic.
2. In the dashboard, open Reports, note the day's spend for one model in
   the Spend-per-day bar and the By-model table.
3. In your provider console, open the usage page for the same UTC day
   and the same model:
   - Anthropic: Console → Usage (set the same date; the console reports
     in UTC).
   - OpenAI: Platform → Usage → filter by model.
4. Compare. Agreement should be within rounding (see below).

## Legitimate differences to rule out first

- **Timezone.** The ledger buckets days in UTC; make sure the console
  view is the same day in UTC, not your local day.
- **Traffic that never crossed the proxy.** The console bills your whole
  key; the ledger records what flowed through it. A phone app, another
  machine, or a session started before you wired the proxy will show in
  the console only.
- **Subscription seats.** Claude Code on a subscription plan bills no
  API dollars; the ledger still computes the calls' USD value. That is a
  feature (it prices your usage), but it will not match a $0 console.
- **Unpriced models.** A model missing from the packs is recorded with
  cost unknown, never $0, and the proxy logs one loud warning naming it.
  Unknown-cost calls are excluded from spend totals; add the model to a
  pack (docs/pricing.md) and the calls reprice on the next report load.
- **Introductory and negotiated rates.** Packs carry list prices, with
  dated notes where a rate is temporary. Private rates belong in
  `AGENTICLEDGER_PRICING` overrides.
- **Rounding.** Costs are computed per call and rounded at 8 decimal
  places; a day of thousands of calls can differ from the console by
  fractions of a cent.

## When it still disagrees

That is the bug we asked for. File it with: the UTC day, the model, both
totals, and one example `action_id` (the call's stored tokens make the
math checkable by hand: the formula is
`(billable_in * in + cache_read * read + cache_write * write + out * out_rate) / 1e6`
with the provider conventions described in docs/pricing.md).
https://github.com/ShekharBhardwaj/AgenticLedger/issues
