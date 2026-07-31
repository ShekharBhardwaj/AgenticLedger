"""
Aggregated spend reports — the data behind the web app's Reports view and
the daily digest webhook.

The store supplies raw per-day / per-model / per-agent aggregates; this
module derives the numbers that need pricing knowledge, chiefly cache
savings: what the cached prompt traffic would have cost at full input
rates minus what it actually cost under each provider's convention
(Anthropic: reads 0.1x and writes 1.25x alongside input_tokens; OpenAI:
the cached subset of prompt_tokens at 0.5x). Savings are signed — heavy
cache writes that are never read back can make caching a net cost, and
the report says so rather than clamping to zero.
"""

from typing import Any, Optional

from .pricing import compute_cost


def _cache_savings(
    model_id: str,
    provider: Optional[str],
    tokens_in: int,
    cache_read: int,
    cache_write: int,
) -> float:
    """Signed savings for one model's aggregate cache traffic. Linear in
    token counts, so computing on sums equals summing per call."""
    if not cache_read and not cache_write:
        return 0.0
    if provider == "anthropic":
        # Without caching, reads and writes would all be plain input tokens.
        without = compute_cost(model_id, tokens_in + cache_read + cache_write, 0)
        actual = compute_cost(
            model_id, tokens_in, 0,
            cache_read_tokens=cache_read, cache_write_tokens=cache_write,
            provider="anthropic",
        )
    elif provider == "openai":
        # Cached tokens are a subset of prompt tokens — the no-cache prompt
        # is the same size, just billed at the full rate.
        without = compute_cost(model_id, tokens_in, 0)
        actual = compute_cost(
            model_id, tokens_in, 0,
            cache_read_tokens=cache_read, cache_write_tokens=cache_write,
            provider="openai",
        )
    else:
        return 0.0
    if without is None or actual is None:  # unpriced model
        return 0.0
    return without - actual


def build_report(
    daily: list[dict[str, Any]],
    models: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    days: int,
) -> dict[str, Any]:
    """Assemble the /api/reports payload from the store's raw aggregates."""
    for row in models:
        row["cache_savings_usd"] = _cache_savings(
            row.get("model_id") or "",
            row.get("provider"),
            int(row.get("tokens_in") or 0),
            int(row.get("cache_read_tokens") or 0),
            int(row.get("cache_write_tokens") or 0),
        )

    totals = {
        "total_cost_usd": sum(float(r.get("cost_usd") or 0) for r in daily),
        "call_count": sum(int(r.get("call_count") or 0) for r in daily),
        "error_calls": sum(int(r.get("error_calls") or 0) for r in daily),
        "tokens_in": sum(int(r.get("tokens_in") or 0) for r in daily),
        "tokens_out": sum(int(r.get("tokens_out") or 0) for r in daily),
        "cache_read_tokens": sum(int(r.get("cache_read_tokens") or 0) for r in daily),
        "cache_write_tokens": sum(int(r.get("cache_write_tokens") or 0) for r in daily),
        "cache_savings_usd": sum(float(r.get("cache_savings_usd") or 0) for r in models),
    }
    return {"days": days, "totals": totals, "daily": daily, "models": models, "agents": agents}


def digest_text(report: dict[str, Any], hours: int = 24) -> str:
    """One-message summary of the report window — Slack-incoming-webhook
    friendly (plain `text` with mrkdwn), readable in any generic receiver."""
    t = report["totals"]
    lines = [
        f"*Agentic Ledger — last {hours}h*",
        f"Spend: ${t['total_cost_usd']:.2f} across {t['call_count']} calls"
        + (f" ({t['error_calls']} errored)" if t["error_calls"] else ""),
        f"Tokens: {t['tokens_in']:,} in / {t['tokens_out']:,} out",
    ]
    if t["cache_read_tokens"] or t["cache_write_tokens"]:
        verb = "saved" if t["cache_savings_usd"] >= 0 else "cost an extra"
        lines.append(
            f"Prompt cache {verb} ${abs(t['cache_savings_usd']):.2f} "
            f"({t['cache_read_tokens']:,} reads / {t['cache_write_tokens']:,} writes)"
        )
    top_models = sorted(
        report["models"], key=lambda r: float(r.get("cost_usd") or 0), reverse=True
    )[:3]
    if top_models:
        lines.append("Top models: " + ", ".join(
            f"{m['model_id']} ${float(m.get('cost_usd') or 0):.2f}" for m in top_models
        ))
    top_agents = sorted(
        report["agents"], key=lambda r: float(r.get("cost_usd") or 0), reverse=True
    )[:3]
    if top_agents:
        lines.append("Top agents: " + ", ".join(
            f"{a['agent_name']} ${float(a.get('cost_usd') or 0):.2f}" for a in top_agents
        ))
    return "\n".join(lines)


def estimate_whatif(rows: list[dict[str, Any]], target_model: str,
                    target_provider: str) -> Optional[dict[str, Any]]:
    """Reprice captured token counts against another model — no API calls.
    Returns None when the target model has no pricing. Cache tokens are
    repriced under the target provider's convention; the result is an
    estimate (tokenizers differ between model families) and says so."""
    actual = 0.0
    estimated = 0.0
    counted = 0
    for row in rows:
        tokens_in = int(row.get("tokens_in") or 0)
        tokens_out = int(row.get("tokens_out") or 0)
        if tokens_in == 0 and tokens_out == 0:
            continue  # blocked/partial calls carry no token economics
        cost = compute_cost(
            target_model, tokens_in, tokens_out,
            cache_read_tokens=row.get("cache_read_tokens"),
            cache_write_tokens=row.get("cache_write_tokens"),
            provider=target_provider,
        )
        if cost is None:
            return None
        estimated += cost
        actual += float(row.get("cost_usd") or 0)
        counted += 1
    return {
        "target_model": target_model,
        "target_provider": target_provider or None,
        "calls": counted,
        "actual_cost_usd": round(actual, 6),
        "estimated_cost_usd": round(estimated, 6),
        "delta_usd": round(estimated - actual, 6),
        "note": "estimate: token counts from the original captures; "
                "tokenizers and cache behavior differ between model families",
    }
