# Model pricing: how to add or correct a model

Prices live in plain JSON files, one per provider, in
[`agenticledger/pricing_data/`](../agenticledger/pricing_data/). Updating
them needs no Python: edit one file, run the tests, open a PR. This is a
great first contribution, and it genuinely matters: every model priced is
a user whose budgets and reports work.

## Add a model

1. Open the provider's file (or add a new `provider.json` next to them).
2. Add one line under `"models"`. Prices are USD per million tokens:

```json
"gpt-6-mini": {"input": 0.20, "output": 1.60}
```

3. If the provider bills prompt caching at rates that do not follow its
   usual convention, add them explicitly (also USD per million):

```json
"my-model": {"input": 1.00, "output": 2.00, "cache_read": 0.10, "cache_write": 1.25}
```

   Without explicit cache rates, the convention applies automatically:
   Anthropic bills cache reads at 0.1x and writes at 1.25x the input
   rate; OpenAI bills cached tokens at 0.5x input and does not bill
   writes.

4. Add a golden assertion in `tests/test_pricing_packs.py` if you changed
   an existing price: the pack change and the golden change in the same
   PR are the review surface.
5. Run `pytest tests/test_pricing_packs.py`. The schema test names the
   exact file and key if something is off.

## How matching works

Patterns are matched as substrings of the model id, longest match wins,
with dots and dashes treated as equal. That is what lets a gateway id
like `us.anthropic.claude-3-5-sonnet-20241022-v2:0` or
`anthropic/claude-3.5-sonnet` resolve to the plain `claude-3-5-sonnet`
rate without its own entry. Add the most specific pattern that is stable
across gateways, usually the model family name without date suffixes.

## Freshness: the drift bot and the checked date

Every pack carries a top-level "checked" date: when a human last
verified its prices against the provider's page. A weekly GitHub Action
compares our packs against the community price database (LiteLLM's
file) and opens a review PR when a comparable model's price moved; the
PR stays red until its golden assertions are updated, which is the
review working as designed. When you touch a pack, bump its checked
date.

## Notes and effective dates

JSON has no comments, so packs carry a top-level `"notes"` list and each
model accepts a `"note"` string (for example an introductory rate with
its end date). Notes are for humans; the loader ignores them.

## Refreshing prices without upgrading

`agenticledger pricing update` fetches the current packs from the
repository into `~/.agenticledger/pricing/`, where they override the
built-ins at startup. Everything is validated before a single byte is
installed, and a bad response changes nothing. This is the only network
call the CLI makes, and only when you run it: the ledger never fetches
anything on its own. Restart the proxy to apply; delete the folder to
return to the built-in prices.

## Runtime overrides (users, not contributors)

`AGENTICLEDGER_PRICING` (inline JSON) and `AGENTICLEDGER_PRICING_FILE`
still override everything at startup, for private models and negotiated
rates. Overrides use the list form: `{"my-model": [in, out]}` or
`[in, out, cache_read, cache_write]`.
