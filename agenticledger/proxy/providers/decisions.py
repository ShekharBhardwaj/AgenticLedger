"""OpenRouter's Decisions wire (`/api/alpha/decisions`): a decision model
such as TypeSafe's Jev answers questions about a state instead of chatting.
Request: `model`, `state`, `questions` (each with a type, instructions and
criteria). Response: `answers` keyed by question id (choice, confidence,
probabilities, or a score, or a yes/no number), plus `usage` with the
cost OpenRouter charged. The record keeps the questions as the system
prompt, the state as the user message, the answers as the reply, and the
charged cost as the cost: exact, not estimated."""

import json
from typing import Optional

from ..normalize import CanonicalRequest, CanonicalResponse
from ..pricing import compute_cost
from .base import build_request


def _questions_text(questions: dict) -> str:
    lines = []
    for qid, q in (questions or {}).items():
        if not isinstance(q, dict):
            continue
        lines.append(f"{qid} ({q.get('type', '?')}): {_flat(q.get('instructions'))}")
        criteria = q.get("criteria")
        if isinstance(criteria, dict):
            for name, desc in criteria.items():
                lines.append(f"  {name}: {_flat(desc)}")
        elif isinstance(criteria, list):
            for i, desc in enumerate(criteria):
                lines.append(f"  {i}: {_flat(desc)}")
    return "\n".join(lines)


def _flat(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, default=str)


def _answers_text(answers: dict) -> str:
    lines = []
    for qid, a in (answers or {}).items():
        if not isinstance(a, dict):
            lines.append(f"{qid}: {_flat(a)}")
            continue
        kind = a.get("type")
        if kind == "choice":
            line = f"{qid}: {a.get('choice')}"
        elif kind == "score":
            line = f"{qid}: score {a.get('score')}"
        elif kind == "noul":
            line = f"{qid}: {a.get('noul')}"
        else:
            line = f"{qid}: {_flat(a)}"
        if a.get("confidence") is not None:
            line += f" (confidence {a['confidence']})"
        if isinstance(a.get("probabilities"), dict):
            probs = ", ".join(f"{k} {v}" for k, v in a["probabilities"].items())
            line += f" [{probs}]"
        lines.append(line)
    return "\n".join(lines)


class DecisionsProvider:
    name = "openrouter"
    wire = "openrouter-decisions"
    attribution_story = (
        "Tags ride the inbound URL (/r/<run>/<iter>/api/alpha/decisions) and the "
        "x-agenticledger-* headers, which the SDK's default headers can carry; "
        "both are stripped before forwarding and the body is never touched."
    )
    binary_stream = False

    def matches_path(self, path: str) -> bool:
        return "alpha/decisions" in path

    def captures_path(self, path: str) -> bool:
        return self.matches_path(path)

    def matches_response(self, body: dict) -> bool:
        return isinstance(body.get("answers"), dict) and "usage" in body

    def matches_stream(self, first_chunk: Optional[dict]) -> bool:
        return False

    def streams(self, path: str, body: dict) -> bool:
        return False

    def stream_error(self, raw: bytes) -> Optional[str]:
        return None

    def reconstruct_stream(self, raw: bytes, latency_ms: float, model_id: str) -> CanonicalResponse:
        return CanonicalResponse(content=None, tool_calls=None, stop_reason=None,
                                 tokens_in=None, tokens_out=None, latency_ms=latency_ms)

    def upstream_default(self) -> Optional[str]:
        return "https://openrouter.ai"

    def normalize_request(self, body: dict, path: str) -> CanonicalRequest:
        model = body.get("model", "unknown")
        system = _questions_text(body.get("questions") or {})
        state = body.get("state")
        messages = [{"role": "user", "content": state if isinstance(state, str)
                     else json.dumps(state, indent=2, default=str)}]
        if system:
            messages = [{"role": "system", "content": system}] + messages
        return build_request(messages=messages, model=model, provider=self.name,
                             system_prompt=system or None, body=body)

    def normalize_response(self, body: dict, latency_ms: float, model_id: str) -> CanonicalResponse:
        usage = body.get("usage") or {}
        tokens_in = usage.get("input_tokens")
        tokens_out = usage.get("output_tokens")
        cost = usage.get("cost")
        if not isinstance(cost, (int, float)):
            cost = compute_cost(model_id, tokens_in, tokens_out, provider=self.name)
        return CanonicalResponse(
            content=_answers_text(body.get("answers") or {}) or None,
            tool_calls=None,
            stop_reason="end_turn",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=float(cost) if cost is not None else None,
        )
