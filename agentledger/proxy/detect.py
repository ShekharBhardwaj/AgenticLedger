"""
Zero-config detection of well-known agent clients from request fingerprints.

Explicit x-agentledger-* headers always take precedence — detection only fills
gaps, so untagged traffic (e.g. Claude Code pointed at the proxy via
ANTHROPIC_BASE_URL alone) lands in coherent, labeled sessions instead of the
shared auto-<date> bucket.

Signals are deliberately conservative and version-tolerant: user-agent
prefixes and stable system-prompt prefixes, never exact strings — client
fingerprints drift across releases.
"""

import re
from typing import Optional

# Claude Code embeds its session UUID in the Anthropic metadata.user_id field:
# "user_<hash>_account_<uuid>_session_<uuid>". The session UUID matches the id
# shown by `claude --resume`, so surfacing it verbatim lets users correlate
# ledger sessions with their local Claude Code sessions.
_CC_SESSION_RE = re.compile(
    r"session_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


def detect_agent(headers, body: Optional[dict]) -> dict:
    """Fingerprint the calling agent. Returns {framework, agent_name, session_id};
    every value may be None. Callers must let explicit headers win."""
    framework: Optional[str] = None
    session_id: Optional[str] = None

    ua = (headers.get("user-agent") or "").lower()
    if ua.startswith("claude-cli/"):
        framework = "claude-code"
    elif ua.startswith("litellm"):
        framework = "litellm"

    if body:
        meta_user = (body.get("metadata") or {}).get("user_id") or ""
        m = _CC_SESSION_RE.search(meta_user)
        if m:
            session_id = m.group(1)
            framework = framework or "claude-code"
        if framework is None and _system_prompt_startswith(body, "you are claude code"):
            framework = "claude-code"

    return {
        "framework": framework,
        # litellm is a client library, not an agent — only claude-code earns
        # an agent identity from detection.
        "agent_name": "claude-code" if framework == "claude-code" else None,
        "session_id": session_id,
    }


def _system_prompt_startswith(body: dict, prefix: str) -> bool:
    """Check the system prompt in any of its wire shapes: Anthropic top-level
    string or content-block list, or an OpenAI leading system message."""
    texts: list[str] = []
    system = body.get("system")
    if isinstance(system, str):
        texts.append(system)
    elif isinstance(system, list):
        texts.extend(
            b.get("text", "") for b in system
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    else:
        messages = body.get("messages")
        if isinstance(messages, list) and messages:
            first = messages[0]
            if isinstance(first, dict) and first.get("role") == "system":
                content = first.get("content")
                if isinstance(content, str):
                    texts.append(content)
    return any(t.lower().startswith(prefix) for t in texts)
