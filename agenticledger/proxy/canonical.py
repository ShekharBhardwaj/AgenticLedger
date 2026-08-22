"""The canonicalization layer: where wire content becomes stable meaning.

Clients salt the SAME conversation on every request: Claude Code stamps a
billing-header block whose nonce changes per call, moves cache_control
markers between blocks as the cache window slides, and embeds session-
unique paths. Hashing raw wire bytes therefore made one agent look like
many (#80), one conversation look like many (#89), and a conversation's
first call split from the rest (#90): three hunts for one animal.

This module is the single owner of that problem. The rule it enforces,
grep-able in tests/test_canonical.py:

    No identity signal may hash wire bytes. Loop signatures, thread
    chains, framework detection, and session inference consume the
    stable view only; nothing outside this module calls hashlib on
    request content.

Every volatile-content rule lives in the table below, each entry naming
the real traffic that demanded it and the fixture that pins it. A new
kind of salt is one entry plus one fixture, and every consumer heals at
once.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Optional

# ── The volatile-content table ───────────────────────────────────────────────
#
# Leading text blocks (or, after normalization flattens block arrays to
# newline-joined text, leading LINES) dropped before hashing.
VOLATILE_BLOCK_PREFIXES: tuple[str, ...] = (
    # Claude Code >= 2.1 prepends "x-anthropic-billing-header: cc_version=
    # 2.1.220.<nonce>; cc_entrypoint=…" and the nonce changes per REQUEST.
    # Fixture: tests/fixtures/wire/claude-code-plain-main.json (#80, #89).
    "x-anthropic-billing-header",
)

# Keys removed from content blocks before hashing.
VOLATILE_BLOCK_KEYS: tuple[str, ...] = (
    # Anthropic prompt-cache markers migrate between blocks as the cache
    # window moves, so the same message hashes differently per request.
    # Fixture: tests/fixtures/wire/claude-code-tool-followup.json (#89).
    "cache_control",
)

# Patterns masked in system text before hashing.
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
MASKED_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    # Session-unique scratchpad paths carry a session UUID; two invocations
    # of the same agent differ only there.
    # Fixture: tests/fixtures/wire/claude-code-plain-companion.json (#80).
    (_UUID_RE, "*"),
)

_HASH_CONTENT_CAP = 4_000    # per-message bytes hashed — enough to disambiguate


# ── Hashing ──────────────────────────────────────────────────────────────────

def digest(value: object) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, default=str)[:_HASH_CONTENT_CAP]
    except Exception:
        raw = str(value)[:_HASH_CONTENT_CAP]
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


# ── Stable forms ─────────────────────────────────────────────────────────────

def stable_content(content):
    """Message content minus per-request decoration, in one shape."""
    if not isinstance(content, list):
        return content
    out = []
    for block in content:
        if isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str) and text.startswith(VOLATILE_BLOCK_PREFIXES):
                continue
            if any(k in block for k in VOLATILE_BLOCK_KEYS):
                block = {k: v for k, v in block.items() if k not in VOLATILE_BLOCK_KEYS}
        out.append(block)
    # Shape is volatile too: the SAME message arrives as a block array on
    # one request and as a plain string on the next (Claude Code's injected
    # system messages do this between a conversation's first and second
    # call). Text-only block arrays collapse to the string they mean.
    # Fixture: claude-code-tool-main vs claude-code-tool-followup (#90).
    if out and all(isinstance(b, dict) and b.get("type") == "text"
                   and set(b) <= {"type", "text"} for b in out):
        return "\n".join(b["text"] for b in out)
    return out


def stable_system_text(sys_prompt) -> str:
    """The system prompt's stable text, in any wire shape."""
    if isinstance(sys_prompt, list):
        text = "\n".join(
            (b.get("text", "") if isinstance(b, dict) else str(b))
            for b in sys_prompt)
    else:
        text = sys_prompt if isinstance(sys_prompt, str) else str(sys_prompt)
    lines = [ln for ln in text.split("\n")
             if not ln.startswith(VOLATILE_BLOCK_PREFIXES)]
    text = "\n".join(lines)
    for pattern, replacement in MASKED_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def message_chain(messages: list) -> tuple[str, ...]:
    """Per-message digests over stable content: the thread-stitching key."""
    return tuple(
        digest({"role": m.get("role"), "content": stable_content(m.get("content")),
                "tool_calls": m.get("tool_calls")})
        if isinstance(m, dict) else digest(m)
        for m in messages
    )


def count_turns(messages: list) -> int:
    """User turns — user messages that aren't pure tool-result carriers."""
    turns = 0
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if (
            isinstance(content, list) and content
            and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        ):
            continue
        turns += 1
    return turns


def system_digest(req) -> Optional[str]:
    """Hash of the system prompt's stable content, or None when the
    request carries no system prompt in any wire shape."""
    if req.system_prompt:
        return digest(stable_system_text(req.system_prompt))
    for m in req.messages[:1]:
        if isinstance(m, dict) and m.get("role") == "system":
            return digest(stable_system_text(m.get("content")))
    return None


@dataclass(frozen=True)
class StableView:
    """What a request IS, once the salt is gone. Computed once per
    request; every identity consumer reads from here."""
    system_digest: Optional[str]
    message_chain: tuple[str, ...]
    turns: int


def stable_view(req) -> StableView:
    return StableView(
        system_digest=system_digest(req),
        message_chain=message_chain(req.messages),
        turns=count_turns(req.messages),
    )


# ── Utility-call classification ──────────────────────────────────────────────
#
# Claude Code fires small utility calls at the same endpoint as the main
# loop: startup probes (a max_tokens=1 "quota" ping on the main model) and
# haiku-class title/summary calls (max_tokens in the hundreds). Main calls
# default to 32k-64k max_tokens, but users shrink that arbitrarily via
# CLAUDE_CODE_MAX_OUTPUT_TOKENS — so a small cap alone cannot discriminate:
# it must pair with a haiku-class model, except for the near-zero probes no
# real completion could fit in. (The status summarizer, #102, is the open
# case: main model, cap 1024, non-streaming.)
_PROBE_MAX_TOKENS = 8
_UTILITY_MAX_TOKENS = 1024


def is_utility_call(req, meta: dict) -> bool:
    """True for framework housekeeping calls that must stay out of loop
    inference — chaining them inflates step counts and resets repeat streaks."""
    if meta.get("framework") != "claude-code" or req.max_tokens is None:
        return False
    if req.max_tokens <= _PROBE_MAX_TOKENS:
        return True
    return (
        req.max_tokens <= _UTILITY_MAX_TOKENS
        and "haiku" in (req.model_id or "").lower()
    )
