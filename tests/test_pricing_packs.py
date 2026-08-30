"""Pricing packs: the data files a contributor edits, held to a strict
schema, plus golden cost assertions pinning the math to known rates.

The runtime loader is forgiving (a bad pack logs and is skipped so the
proxy never dies over a typo); THIS file is the strict gate. A pricing PR
that misspells a key or drops a model fails here, in CI, with a message
naming the file.
"""

import json
from importlib import resources

import pytest

from agenticledger.proxy.pricing import _PRICES, compute_cost

ALLOWED_KEYS = {"input", "output", "cache_read", "cache_write", "note"}


def _packs():
    root = resources.files("agenticledger.pricing_data")
    return [e for e in sorted(root.iterdir(), key=lambda e: e.name)
            if e.name.endswith(".json")]


def test_every_pack_parses_and_follows_the_schema():
    assert _packs(), "no pricing packs found"
    for entry in _packs():
        pack = json.loads(entry.read_text())  # strict: a bad pack raises here
        assert pack.get("provider"), f"{entry.name}: missing provider"
        assert pack.get("models"), f"{entry.name}: no models"
        for pattern, spec in pack["models"].items():
            where = f"{entry.name}: {pattern!r}"
            assert pattern == pattern.lower(), f"{where}: patterns are lowercase"
            unknown = set(spec) - ALLOWED_KEYS
            assert not unknown, f"{where}: unknown keys {unknown} (typo?)"
            for key in ("input", "output"):
                assert key in spec, f"{where}: missing {key}"
                assert isinstance(spec[key], (int, float)), f"{where}: {key} not a number"
                assert spec[key] >= 0, f"{where}: negative {key}"


def test_all_packs_loaded_into_the_table():
    total = sum(len(json.loads(e.read_text())["models"]) for e in _packs())
    assert len(_PRICES) >= total


# ── Golden costs: the accuracy harness's foundation ──────────────────────────
# Exact expected dollars for representative calls. If a provider changes a
# price, the pack changes AND the golden here changes in the same PR — the
# pair is the review surface.

GOLDENS = [
    # (model, tokens_in, tokens_out, cache_read, cache_write, provider, expected_usd)
    ("gpt-4o", 1_000_000, 1_000_000, None, None, "openai", 12.50),
    ("gpt-4o-mini", 1_000_000, 0, None, None, "openai", 0.15),
    ("claude-opus-5", 1_000_000, 1_000_000, None, None, "anthropic", 30.00),
    ("claude-sonnet-5", 1_000_000, 0, None, None, "anthropic", 2.00),
    # Anthropic cache: reads 0.1x input, writes 1.25x input, billed on top.
    ("claude-opus-5", 100, 0, 1_000_000, 0, "anthropic", 0.5005),
    ("claude-opus-5", 0, 0, 0, 1_000_000, "anthropic", 6.25),
    # OpenAI cache: cached tokens are a SUBSET of tokens_in at 0.5x.
    ("gpt-4o", 1_000_000, 0, 500_000, 0, "openai", 1.875),
    ("gemini-2.5-flash", 1_000_000, 1_000_000, None, None, "", 2.80),
    ("deepseek-v4-flash", 1_000_000, 1_000_000, 500_000, None, "deepseek", 0.3514),
    ("mistral-small-latest", 1_000_000, 1_000_000, 500_000, None, "mistral", 0.6825),
]


@pytest.mark.parametrize("model,tin,tout,cr,cw,provider,expected", GOLDENS)
def test_golden_costs(model, tin, tout, cr, cw, provider, expected):
    got = compute_cost(model, tin, tout, cache_read_tokens=cr,
                       cache_write_tokens=cw, provider=provider)
    assert got == pytest.approx(expected, abs=1e-9), (
        f"{model}: expected ${expected}, ledger computed ${got}")


def test_gateway_ids_resolve_to_the_same_rate():
    plain = compute_cost("claude-3-5-sonnet", 1000, 1000, provider="anthropic")
    for gateway_id in (
        "anthropic/claude-3.5-sonnet",                       # OpenRouter
        "us.anthropic.claude-3-5-sonnet-20241022-v2:0",      # Bedrock
    ):
        assert compute_cost(gateway_id, 1000, 1000, provider="anthropic") == plain


def test_longest_pattern_wins():
    mini = compute_cost("gpt-4o-mini", 1_000_000, 0, provider="openai")
    assert mini == 0.15  # not the 2.50 of the shorter "gpt-4o" pattern
