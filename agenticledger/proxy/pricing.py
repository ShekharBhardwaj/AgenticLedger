"""
Per-token pricing for common models.
Cost is computed at capture time and stored with each call.

The prices themselves live in agenticledger/pricing_data/*.json — plain
data files, one per provider, that a contributor can update without
touching Python. See docs/pricing.md for how to add or correct a model.

User overrides (merged over the built-in table at startup):

  AGENTICLEDGER_PRICING       Inline JSON — useful for Docker env vars
                            e.g. '{"gpt-4o": [2.50, 10.00], "my-model": [1.00, 2.00]}'
                            A 4-element form also sets cache pricing:
                            '{"my-model": [in, out, cache_read, cache_write]}'

  AGENTICLEDGER_PRICING_FILE  Path to a JSON file with the same format
                            e.g. /etc/agenticledger/pricing.json

Prompt-cache pricing: when a model has no explicit cache entry, provider
conventions apply — Anthropic bills cache reads at 0.1x and cache writes at
1.25x the input rate (and reports them *outside* usage.input_tokens);
OpenAI bills cached prompt tokens at 0.5x the input rate (reported as a
*subset* of usage.prompt_tokens) and does not bill cache writes.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Prices per million tokens, loaded from agenticledger/pricing_data/*.json.
_PRICES: dict[str, tuple[float, float]] = {}

# (cache_read_per_million, cache_write_per_million) in USD — explicit entries
# only; models without one fall back to provider-convention multipliers in
# compute_cost(). Populated from packs and 4-element pricing overrides.
_CACHE_PRICES: dict[str, tuple[float, float]] = {}

_PACK_KEYS = {"input", "output", "cache_read", "cache_write", "note"}


def _load_packs() -> None:
    """Load every pricing pack. A malformed pack is skipped with a loud
    warning rather than taking the proxy down — but the test suite parses
    packs strictly, so a bad PR fails CI before it can ship."""
    from importlib import resources

    try:
        root = resources.files("agenticledger.pricing_data")
    except Exception as exc:  # packs missing from an unusual install
        logger.warning("pricing packs unavailable: %s", exc)
        return
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".json"):
            continue
        try:
            pack = json.loads(entry.read_text())
            for pattern, spec in pack.get("models", {}).items():
                _PRICES[pattern.lower()] = (float(spec["input"]), float(spec["output"]))
                if "cache_read" in spec or "cache_write" in spec:
                    _CACHE_PRICES[pattern.lower()] = (
                        float(spec.get("cache_read", 0.0)),
                        float(spec.get("cache_write", 0.0)),
                    )
        except Exception as exc:
            logger.warning("pricing pack %s could not be loaded: %s", entry.name, exc)


_load_packs()


def _load_user_packs() -> None:
    """Packs installed by `agenticledger pricing update` override the
    built-ins pattern by pattern. Same forgiving posture as the built-in
    loader; the update command validated strictly before installing."""
    from pathlib import Path

    user_dir = Path.home() / ".agenticledger" / "pricing"
    if not user_dir.is_dir():
        return
    for entry in sorted(user_dir.glob("*.json")):
        try:
            pack = json.loads(entry.read_text())
            for pattern, spec in pack.get("models", {}).items():
                _PRICES[pattern.lower()] = (float(spec["input"]), float(spec["output"]))
                if "cache_read" in spec or "cache_write" in spec:
                    _CACHE_PRICES[pattern.lower()] = (
                        float(spec.get("cache_read", 0.0)),
                        float(spec.get("cache_write", 0.0)),
                    )
        except Exception as exc:
            logger.warning("user pricing pack %s could not be loaded: %s",
                           entry.name, exc)


_load_user_packs()

# Provider-convention cache multipliers applied to the input rate.
_CACHE_READ_MULT  = {"anthropic": 0.10, "openai": 0.50}
_CACHE_WRITE_MULT = {"anthropic": 1.25, "openai": 0.0}


def _load_overrides() -> None:
    """Merge user-supplied pricing overrides into _PRICES at startup."""
    overrides: dict[str, list] = {}

    env_json = os.environ.get("AGENTICLEDGER_PRICING", "").strip()
    if env_json:
        try:
            overrides.update(json.loads(env_json))
        except Exception as exc:
            logger.warning("AGENTICLEDGER_PRICING is not valid JSON, ignoring: %s", exc)

    pricing_file = os.environ.get("AGENTICLEDGER_PRICING_FILE", "").strip()
    if pricing_file:
        try:
            with open(pricing_file) as f:
                overrides.update(json.load(f))
        except Exception as exc:
            logger.warning("AGENTICLEDGER_PRICING_FILE could not be loaded, ignoring: %s", exc)

    for model, price in overrides.items():
        try:
            _PRICES[model.lower()] = (float(price[0]), float(price[1]))
            if len(price) >= 4:
                _CACHE_PRICES[model.lower()] = (float(price[2]), float(price[3]))
        except Exception:
            logger.warning(
                "Invalid pricing entry %r: %r — expected [input, output] or "
                "[input, output, cache_read, cache_write]", model, price,
            )

_load_overrides()

# Models already warned about — one loud line per model, not per call.
_unpriced_warned: set[str] = set()


def compute_cost(
    model_id: str,
    tokens_in: Optional[int],
    tokens_out: Optional[int],
    cache_read_tokens: Optional[int] = None,
    cache_write_tokens: Optional[int] = None,
    provider: str = "",
) -> Optional[float]:
    """Return estimated cost in USD, or None if model is not in the pricing table.

    Matches the model id against the pricing table by substring, preferring the
    LONGEST (most specific) matching pattern. This ensures a model like
    "gpt-4o-mini" is priced at its own rate rather than the more general
    "gpt-4o" rate that is also a substring of its id.

    Cache accounting follows the provider's reporting convention:
    - "anthropic": usage.input_tokens EXCLUDES cache traffic, so cache reads
      and writes are billed on top of tokens_in.
    - "openai" (and default): cached tokens are a SUBSET of tokens_in, so the
      cached portion is re-billed at the discounted rate instead.
    """
    if tokens_in is None and tokens_out is None:
        return None
    # Gateways rewrite model ids ("anthropic/claude-3.5-sonnet" on OpenRouter,
    # "us.anthropic.claude-3-5-sonnet-20241022-v2:0" on Bedrock). Substring
    # matching absorbs prefixes/suffixes; unifying dots and dashes absorbs the
    # punctuation variants ("claude-3.5-sonnet" vs "claude-3-5-sonnet").
    model_lower = model_id.lower().replace(".", "-")
    best_pattern: Optional[str] = None
    best_len = -1
    for pattern in _PRICES:
        if pattern.replace(".", "-") in model_lower and len(pattern) > best_len:
            best_pattern = pattern
            best_len = len(pattern)
    if best_pattern is None:
        if model_id not in _unpriced_warned:
            _unpriced_warned.add(model_id)
            # model_id arrives in the request body — strip newlines so a
            # crafted id can't forge extra lines in the proxy log
            safe_id = model_id.replace("\r", " ").replace("\n", " ")
            logger.warning(
                "No pricing for model %r — cost recorded as unknown (not $0). "
                "Budgets and alerts will not see this spend. Add a rate via "
                "AGENTICLEDGER_PRICING='{\"%s\": [in_per_M, out_per_M]}'.",
                safe_id, safe_id,
            )
        return None
    in_price, out_price = _PRICES[best_pattern]

    explicit = _CACHE_PRICES.get(best_pattern)
    if explicit is not None:
        read_price, write_price = explicit
    else:
        conv = provider if provider in _CACHE_READ_MULT else "openai"
        read_price = in_price * _CACHE_READ_MULT[conv]
        write_price = in_price * _CACHE_WRITE_MULT[conv]

    reads = cache_read_tokens or 0
    writes = cache_write_tokens or 0
    # Anthropic reports cache traffic outside tokens_in; OpenAI reports the
    # cached subset inside it, so the base portion is tokens_in minus reads.
    billable_in = (tokens_in or 0) if provider == "anthropic" else max((tokens_in or 0) - reads, 0)

    cost = (
        billable_in * in_price
        + reads * read_price
        + writes * write_price
        + (tokens_out or 0) * out_price
    ) / 1_000_000
    return round(cost, 8)


def has_price(model_id: str) -> bool:
    """True when the pricing table knows this model id (substring match,
    the same rule compute_cost uses)."""
    model_lower = (model_id or "").lower().replace(".", "-")
    return any(pattern.replace(".", "-") in model_lower for pattern in _PRICES)


def infer_provider(model_id: str) -> str:
    """Best-effort provider guess from a model name — used by cost what-if
    and cross-provider replay to pick the right cache convention and wire
    format. Unknown families return "" (plain input/output pricing)."""
    m = (model_id or "").lower()
    if m.startswith("claude") or "anthropic" in m:
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt", "text-embedding", "davinci")):
        return "openai"
    return ""
