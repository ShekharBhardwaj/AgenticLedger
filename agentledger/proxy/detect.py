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

# BMAD-METHOD rides on host coding agents and identifies itself only through
# its persona/skill markdown loaded into the system prompt. Markers are kept
# as data so the community can extend them as BMAD evolves. Longest match
# wins so "Test Architect" beats "Architect".
_BMAD_MARKERS = ("bmad-core", "bmad-method", "bmad/bmm", "bmadclaw", "bmad ")
_BMAD_PERSONAS = [
    ("test architect", "bmad:qa"),
    ("scrum master", "bmad:sm"),
    ("product owner", "bmad:po"),
    ("product manager", "bmad:pm"),
    ("ux expert", "bmad:ux"),
    ("architect", "bmad:architect"),
    ("analyst", "bmad:analyst"),
    ("developer", "bmad:dev"),
    ("dev agent", "bmad:dev"),
]


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

    agent_name: Optional[str] = None

    if body:
        meta_user = (body.get("metadata") or {}).get("user_id") or ""
        m = _CC_SESSION_RE.search(meta_user)
        if m:
            session_id = m.group(1)
            framework = framework or "claude-code"
        if framework is None and _system_prompt_startswith(body, "you are claude code"):
            framework = "claude-code"

        # BMAD personas ride on top of a host coding agent — a BMAD marker in
        # the system prompt upgrades the framework tag, and a persona match
        # names the agent (bmad:sm, bmad:dev, ...).
        system_text = _system_text(body)
        if system_text and any(mk in system_text for mk in _BMAD_MARKERS):
            framework = "bmad"
            for marker, persona in _BMAD_PERSONAS:
                if marker in system_text:
                    agent_name = persona
                    break

    if agent_name is None and framework == "claude-code":
        # litellm is a client library, not an agent — only real agent
        # frameworks earn an agent identity from detection.
        agent_name = "claude-code"

    return {
        "framework": framework,
        "agent_name": agent_name,
        "session_id": session_id,
    }


def _system_texts(body: dict) -> list[str]:
    """The system prompt in any of its wire shapes: Anthropic top-level
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
    return texts


def _system_prompt_startswith(body: dict, prefix: str) -> bool:
    return any(t.lower().startswith(prefix) for t in _system_texts(body))


# Fingerprints are matched against a bounded prefix — BMAD activation blocks
# sit at the top of the persona file, and hashing megabytes of context to
# find a marker would be wasted work.
_SYSTEM_SCAN_CAP = 4_000


def _system_text(body: dict) -> str:
    return " ".join(_system_texts(body)).lower()[:_SYSTEM_SCAN_CAP]
