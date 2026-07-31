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

Cross-provider replay: a call captured from one provider can be rebuilt in
the other provider's wire format — Anthropic tool_use/tool_result content
blocks become OpenAI tool_calls/role-"tool" messages and vice versa, tool
schemas swap between input_schema and function.parameters, and the system
prompt moves between the top-level key and an inline message. Targets are
configured per provider (AGENTICLEDGER_REPLAY_OPENAI_URL/KEY etc.); an
OpenAI-style local server (LM Studio) makes local replay free. Calls
carrying images are refused with a clear reason rather than mangled;
thinking blocks are dropped (they are the original model's private
reasoning, not reusable input).

Metadata-level captures (OTLP ingest, capture_level=metadata) have no
messages and are not replayable.
"""

import json
from typing import Any, Optional


class NotTranslatable(Exception):
    """This capture cannot be faithfully expressed in the target format."""


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
        # Restore the top-level system verbatim from the captured synthetic
        # system message — string or content-block form both pass through —
        # falling back to the flattened system_prompt column.
        sys_msg = next(
            (m for m in messages if isinstance(m, dict) and m.get("role") == "system"),
            None,
        )
        if sys_msg is not None and sys_msg.get("content"):
            body["system"] = sys_msg["content"]
        elif record.get("system_prompt"):
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


# ── Cross-provider translation ───────────────────────────────────────────────

def _block_text(content: Any) -> str:
    """Flatten a string-or-content-blocks value to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "image":
                    raise NotTranslatable("the conversation contains images")
                if block.get("type") in ("text", None) and "text" in block:
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _anthropic_to_openai(record: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in record.get("messages") or []:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            out.append({"role": "system", "content": _block_text(content)})
        elif role == "user":
            if isinstance(content, list):
                # Tool results must directly answer the previous assistant
                # tool_calls in OpenAI's shape; leftover text becomes its
                # own user message.
                texts = []
                for block in content:
                    if not isinstance(block, dict):
                        texts.append(str(block))
                    elif block.get("type") == "tool_result":
                        out.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": _block_text(block.get("content")),
                        })
                    elif block.get("type") == "image":
                        raise NotTranslatable("the conversation contains images")
                    elif block.get("type") == "text":
                        texts.append(block.get("text", ""))
                if texts:
                    out.append({"role": "user", "content": "\n".join(texts)})
            else:
                out.append({"role": "user", "content": _block_text(content)})
        elif role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input") or {}),
                            },
                        })
                    # thinking blocks: the original model's private reasoning —
                    # dropped, they are not reusable input for another model.
            else:
                text_parts.append(_block_text(content))
            entry: dict[str, Any] = {"role": "assistant",
                                     "content": "\n".join(text_parts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
    body: dict[str, Any] = {"model": model, "messages": out}
    if record.get("max_tokens"):
        body["max_tokens"] = record["max_tokens"]
    if record.get("temperature") is not None:
        body["temperature"] = record["temperature"]
    if record.get("tools"):
        body["tools"] = [{
            "type": "function",
            "function": {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object"},
            },
        } for t in record["tools"]]
    return "v1/chat/completions", body


def _openai_to_anthropic(record: dict[str, Any], model: str) -> tuple[str, dict[str, Any]]:
    out: list[dict[str, Any]] = []
    system_parts: list[str] = []
    pending_results: list[dict[str, Any]] = []

    def flush_results() -> None:
        if pending_results:
            out.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for msg in record.get("messages") or []:
        role = msg.get("role")
        if role == "system":
            system_parts.append(_block_text(msg.get("content")))
            continue
        if role == "tool":
            # Consecutive tool answers merge into one Anthropic user turn.
            pending_results.append({
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id", ""),
                "content": _block_text(msg.get("content")),
            })
            continue
        flush_results()
        if role == "user":
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        raise NotTranslatable("the conversation contains images")
            out.append({"role": "user", "content": _block_text(content)})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = _block_text(msg.get("content")) if msg.get("content") else ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError as exc:
                    raise NotTranslatable(
                        f"tool call {fn.get('name')!r} has non-JSON arguments") from exc
                blocks.append({"type": "tool_use", "id": tc.get("id", ""),
                               "name": fn.get("name", ""), "input": args})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
    flush_results()

    body: dict[str, Any] = {
        "model": model,
        "messages": out,
        "max_tokens": record.get("max_tokens") or 4096,
    }
    if system_parts:
        body["system"] = "\n".join(p for p in system_parts if p)
    if record.get("temperature") is not None:
        body["temperature"] = record["temperature"]
    if record.get("tools"):
        body["tools"] = [{
            "name": (t.get("function") or {}).get("name", t.get("name", "")),
            "description": (t.get("function") or {}).get("description", ""),
            "input_schema": (t.get("function") or {}).get("parameters")
                            or {"type": "object"},
        } for t in record["tools"]]
    return "v1/messages", body


def build_cross_request(record: dict[str, Any], model: str,
                        target_provider: str) -> tuple[str, dict[str, Any]]:
    """(path, body) for replaying a capture on the OTHER provider's format.
    Raises NotTranslatable with a human-readable reason when it can't be
    done faithfully."""
    source = record.get("provider")
    if (source, target_provider) == ("anthropic", "openai"):
        return _anthropic_to_openai(record, model)
    if (source, target_provider) == ("openai", "anthropic"):
        return _openai_to_anthropic(record, model)
    raise NotTranslatable(
        f"no translation from {source!r} captures to {target_provider!r}")
