"""The cache audit (#113): the discount a run was eligible for and did
not receive.

Repeating text is NOT waste — stateless models require it. Paying full
price for it when the provider sells a repeat-discount is. The audit
never touches traffic; it reads the record and does arithmetic, and the
shipping rule is user zero's: no number without a told-you-why and a
do-this attached.

Two sides, two certainty classes, both printed as what they are:

- RECEIVED is exact: the provider reported cache reads and writes per
  call, and the pricing packs know the rates.
- ELIGIBLE is an estimate when caching was never used, because the
  provider never told us the repeated prefix's token count. The estimate
  states its method inline (text length / 4 chars per token) and the
  exact figure replaces it the moment caching is on and reads appear.

Verdicts, each with its reason and its one-line fix:

    well_cached      the discount is being received; nothing missed
    partially_cached some calls hit the cache, most repeats did not
    never_requested  identical prompt repeated, zero cache traffic ever
    unstable_opening the prompt CHANGES between calls, voiding the match
    too_short        repeated text is below the provider's cache minimum
    not_auditable    capture level strips prompts, or too few calls
"""

from typing import Any

from .pricing import lookup_rates

# Providers whose caching is opt-in: the fix is a request change. Everyone
# else (OpenAI-style) caches automatically, so zero hits with an identical
# recorded opening points at instability, not a missing flag.
_OPT_IN = {"anthropic", "bedrock"}

# The common provider minimum for a cacheable prefix, in tokens. Anthropic
# documents 1024 for most models; OpenAI's automatic caching also starts
# at 1024. Below this the honest verdict is "you're fine".
_MIN_CACHEABLE_TOKENS = 1024

_CHARS_PER_TOKEN = 4  # the estimate's stated method: text length / 4


def _estimate_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN


def _fix_for(provider: str) -> str:
    if provider == "bedrock":
        return ("add a cachePoint block after the stable part of the request "
                "(Converse) or cache_control on the system prompt (InvokeModel)")
    if provider == "anthropic":
        return ('mark the stable prefix with cache_control: {"type": "ephemeral"} '
                "on the system prompt (Claude Code already does this)")
    return ("this provider caches automatically when openings match AND calls "
            "arrive within its cache window (minutes); spread-out calls miss "
            "it, and some gateways strip the cached-token report")


def _divergence(a: str, b: str) -> int:
    """Index of the first differing character between two prompts."""
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit


def audit_run(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """The audit for one run's calls (oldest first). Pure function over
    stored rows: provider, model_id, system_prompt, cache_read_tokens,
    cache_write_tokens per call."""
    ok = [c for c in calls if c.get("status_code") == 200]
    if len(ok) < 2:
        return {"verdict": "not_auditable",
                "reason": "fewer than two successful calls; repetition needs a repeat",
                "fix": None, "received_usd": _received(ok), "eligible": None}

    prompts = [c.get("system_prompt") or "" for c in ok]
    if not any(prompts):
        return {"verdict": "not_auditable",
                "reason": "the capture level strips prompts, so repetition "
                          "cannot be verified from the record",
                "fix": "raise the capture level to audit caching",
                "received_usd": _received(ok), "eligible": None}

    provider = ok[-1].get("provider") or ""
    model_id = ok[-1].get("model_id") or ""
    rates = lookup_rates(model_id, provider)
    received_usd = _received(ok)
    reads = sum(c.get("cache_read_tokens") or 0 for c in ok)
    writes = sum(c.get("cache_write_tokens") or 0 for c in ok)

    # Group identical prompts; the largest repeated group is the audit's
    # subject. Byte-for-byte identity is what providers match on.
    groups: dict[str, int] = {}
    for text in prompts:
        if text:
            groups[text] = groups.get(text, 0) + 1
    repeated = max(groups.items(), key=lambda kv: kv[1], default=("", 0))
    repeated_text, occurrences = repeated

    if reads > 0:
        # Some cache traffic exists, but "some" must not read as "enough":
        # one cached call in a fifty-call run is not "nothing missed"
        # (caught in review before release). Compare actual reads against
        # what full coverage of the repeats would have read.
        expected = (occurrences - 1) * _estimate_tokens(repeated_text) if occurrences >= 2 else 0
        if expected == 0 or reads >= 0.8 * expected:
            return {"verdict": "well_cached",
                    "reason": f"cache reads on record ({reads:,} tokens); the "
                              "repeat-discount is being received",
                    "fix": None, "received_usd": received_usd, "eligible": None}
        missing = expected - reads
        eligible = None
        if rates is not None:
            eligible = {
                "estimated_usd": round(missing * (rates["input"] - rates["cache_read"]) / 1_000_000, 4),
                "estimated_tokens": missing,
                "occurrences": occurrences,
                "prompt_chars": len(repeated_text),
                "method": f"expected repeat-tokens estimated at text length / "
                          f"{_CHARS_PER_TOKEN} chars per token, minus exact reads; "
                          "exact once coverage is full",
            }
        return {"verdict": "partially_cached",
                "reason": (f"cache reads cover ~{100 * reads // max(expected, 1)}% of the "
                           f"repeats ({reads:,} of ~{expected:,} expected repeat-tokens); "
                           "part of the traffic misses the discount"),
                "fix": _fix_for(provider),
                "received_usd": received_usd, "eligible": eligible}

    if occurrences < 2:
        # No identical prompts, and no cache traffic: the openings differ.
        pos = _divergence(prompts[-2], prompts[-1])
        snippet = prompts[-1][max(0, pos - 20):pos + 20].replace("\n", " ")
        return {"verdict": "unstable_opening",
                "reason": (f"the prompt changes between calls (first difference "
                           f"at character {pos:,}: …{snippet}…), which voids the "
                           "provider's prefix match"),
                "fix": "move the changing part below the stable prefix",
                "received_usd": received_usd, "eligible": None}

    est_tokens = _estimate_tokens(repeated_text)
    if est_tokens < _MIN_CACHEABLE_TOKENS:
        return {"verdict": "too_short",
                "reason": (f"the repeated prompt is ~{est_tokens:,} tokens "
                           f"(estimated at {_CHARS_PER_TOKEN} chars/token), below "
                           f"the ~{_MIN_CACHEABLE_TOKENS:,}-token cache minimum"),
                "fix": None, "received_usd": received_usd, "eligible": None}

    eligible = None
    if rates is not None:
        # What the repeats WOULD have cost cached vs what full price cost:
        # (occurrences - 1) repeats x estimated tokens x (input - cache_read)
        # per-token rate delta. Estimate, method stated, replaced by exact
        # figures once caching is on.
        saved_per_m = rates["input"] - rates["cache_read"]
        est_usd = (occurrences - 1) * est_tokens * saved_per_m / 1_000_000
        eligible = {
            "estimated_usd": round(est_usd, 4),
            "estimated_tokens": est_tokens,
            "occurrences": occurrences,
            "prompt_chars": len(repeated_text),
            "method": f"text length / {_CHARS_PER_TOKEN} chars per token; "
                      "exact once caching is on",
        }

    if provider in _OPT_IN and writes == 0:
        reason = (f"an identical {len(repeated_text):,}-character prompt was "
                  f"sent {occurrences} times and caching was never requested "
                  "(no cache markers on any call)")
    else:
        reason = (f"an identical {len(repeated_text):,}-character prompt was "
                  f"sent {occurrences} times with zero cache hits")
    return {"verdict": "never_requested",
            "reason": reason,
            "fix": _fix_for(provider),
            "received_usd": received_usd,
            "eligible": eligible}


def _received(calls: list[dict[str, Any]]) -> float:
    """EXACT dollars the run's cache reads saved versus full input price."""
    total = 0.0
    for c in calls:
        reads = c.get("cache_read_tokens") or 0
        if not reads:
            continue
        rates = lookup_rates(c.get("model_id") or "", c.get("provider") or "")
        if rates is None:
            continue
        total += reads * (rates["input"] - rates["cache_read"]) / 1_000_000
    return round(total, 4)
