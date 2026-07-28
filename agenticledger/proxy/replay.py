"""
Replay — re-execute a captured call against a live model.

The ledger stores every request in near-provider form, so a captured call
can be rebuilt and sent again: same messages, system prompt, tools, and
sampling parameters, optionally on a different model from the same
provider. The replayed call is stored as a new ledger row linked to the
original (parent_action_id, framework="replay", session
"replay-<original-id-prefix>"), so replays are themselves auditable and
their cost is accounted like any other call.

Replay needs its own credential: the proxy never stores the agent's auth
headers, so re-execution uses AGENTICLEDGER_REPLAY_API_KEY. Unset means
the feature is off and the endpoint answers 409 with a hint.

Scope (v1): providers "openai" (chat completions) and "anthropic"
(messages), same-provider model swaps only. Metadata-level captures (OTLP
ingest, capture_level=metadata) have no messages and are not replayable.
"""

from typing import Any, Optional


def build_replay_request(record: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
    """(path, body) that re-executes a captured call in its provider's format.

    Stored messages include the system prompt inline (normalize_request
    prepends a synthetic system message for Anthropic captures) — Anthropic
    replays strip it back out to the top-level `system` key; OpenAI replays
    send messages as stored.
    """
    provider = record.get("provider")
    messages = record.get("messages") or []
    if provider == "anthropic":
        body: dict[str, Any] = {
            "model": model,
            "messages": [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")],
            "max_tokens": record.get("max_tokens") or 4096,
        }
        if record.get("system_prompt"):
            body["system"] = record["system_prompt"]
        path = "v1/messages"
    else:
        body = {"model": model, "messages": messages}
        if record.get("max_tokens"):
            body["max_tokens"] = record["max_tokens"]
        path = "v1/chat/completions"
    if record.get("temperature") is not None:
        body["temperature"] = record["temperature"]
    if record.get("tools"):
        body["tools"] = record["tools"]
    return path, body


def replay_auth_headers(provider: Optional[str], api_key: str) -> dict[str, str]:
    if provider == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    return {"authorization": f"Bearer {api_key}"}


def replayable_reason(record: dict[str, Any]) -> Optional[str]:
    """None when the record can be replayed, else a human-readable reason."""
    if record.get("provider") not in ("openai", "anthropic"):
        return "unsupported provider (replay handles openai and anthropic captures)"
    if not record.get("messages"):
        return "metadata-only capture — no messages were stored for this call"
    if (record.get("stop_reason") or "") == "count_tokens":
        return "count_tokens metering calls are not replayable"
    return None
