"""
Zero-config detection of well-known agent clients from request fingerprints.

Explicit x-agenticledger-* headers always take precedence — detection only fills
gaps, so untagged traffic (e.g. Claude Code pointed at the proxy via
ANTHROPIC_BASE_URL alone) lands in coherent, labeled sessions instead of the
shared auto-<date> bucket.

Signals are deliberately conservative and version-tolerant: user-agent
prefixes and stable system-prompt prefixes, never exact strings — client
fingerprints drift across releases.
"""

import json
import re
from typing import Optional

# Claude Code embeds its session UUID in the Anthropic metadata.user_id field.
# Two wire formats exist: claude-cli 1.x sent a flat string
# "user_<hash>_account_<uuid>_session_<uuid>"; 2.x sends a JSON blob
# '{"device_id":"<hex>","account_uuid":"<uuid>","session_id":"<uuid>"}'.
# The session UUID matches the id shown by `claude --resume`, so surfacing it
# verbatim lets users correlate ledger sessions with their local Claude Code
# sessions. Regexes over the raw string tolerate both shapes plus key
# reordering/whitespace drift that a strict JSON parse would not.
_UUID_PAT = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CC_SESSION_RE = re.compile(rf"session_({_UUID_PAT})")
_CC_SESSION_JSON_RE = re.compile(rf'"session_id"\s*:\s*"({_UUID_PAT})"')

# BMAD-METHOD rides on host coding agents and identifies itself only through
# its persona/skill markdown loaded into the system prompt. Markers are kept
# as data so the community can extend them as BMAD evolves. Longest match
# wins so "Test Architect" beats "Architect".
# BMAD v4/v5 shipped personas as system prompts, so a marker there was
# enough. v6 ships them as host-tool skills instead: nothing distinctive
# reaches the system prompt, and the persona shows up as a skill
# invocation in the conversation. Both generations are detected.
_BMAD_MARKERS = ("bmad-core", "bmad-method", "bmad/bmm", "bmadclaw", "bmad ")

# {"skill": "bmad-spec"} inside a tool call — the strongest signal there is,
# and it names the persona that is actually running. The whole conversation
# history rides along in each request, so the LAST invocation is the one in
# force for this call.
_BMAD_SKILL_CALL_RE = re.compile(r'"skill"\s*:\s*"(bmad-[a-z0-9_-]+)"', re.I)
# The host tool lists installed skills to the model; two or more distinct
# bmad-* skill names is a machine-generated listing, not someone chatting
# about BMAD.
_BMAD_SKILL_NAME_RE = re.compile(r'\bbmad-(?:agent-)?[a-z][a-z0-9-]{2,24}\b', re.I)


def _bmad_persona_from_skill(skill: str) -> str:
    """bmad-agent-dev → bmad:dev, bmad-spec → bmad:spec."""
    name = skill.lower().removeprefix("bmad-").removeprefix("agent-")
    return f"bmad:{name}" if name else "bmad"


def _bmad_from_skills(body: dict) -> tuple[bool, Optional[str]]:
    """(is_bmad, persona) from BMAD v6's skill traffic."""
    try:
        blob = json.dumps(body.get("messages") or "")
    except (TypeError, ValueError):
        return False, None
    invocations = _BMAD_SKILL_CALL_RE.findall(blob)
    if invocations:
        return True, _bmad_persona_from_skill(invocations[-1])
    names = {n.lower() for n in _BMAD_SKILL_NAME_RE.findall(blob)}
    return (len(names) >= 2), None
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
        m = _CC_SESSION_JSON_RE.search(meta_user) or _CC_SESSION_RE.search(meta_user)
        if m:
            session_id = m.group(1)
            framework = framework or "claude-code"
        # claude-cli 2.x prepends an "x-anthropic-billing-header: cc_version=…;
        # cc_entrypoint=…" system block, and in sdk-cli (-p) mode the persona
        # line is "You are a Claude agent" — the billing header is the stable
        # body-side fingerprint there.
        if framework is None and (
            _system_prompt_startswith(body, "you are claude code")
            or _has_cc_billing_header(body)
        ):
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
        else:
            is_bmad, persona = _bmad_from_skills(body)
            if is_bmad:
                framework = "bmad"
                # Before the first skill runs we know the project is BMAD but
                # not which persona — say "bmad" rather than leaving the
                # agent column blank.
                agent_name = persona or "bmad"

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


def _has_cc_billing_header(body: dict) -> bool:
    """A system block like "x-anthropic-billing-header: cc_version=2.1.220.04c;
    cc_entrypoint=cli;" — cc_* fields are Claude Code's own billing tags.
    Field order is not guaranteed, so require the header prefix plus any cc_
    field rather than an exact prefix through cc_version."""
    return any(
        t.lower().startswith("x-anthropic-billing-header:") and "cc_" in t
        for t in _system_texts(body)
    )


# Fingerprints are matched against a bounded prefix — BMAD activation blocks
# sit at the top of the persona file, and hashing megabytes of context to
# find a marker would be wasted work.
_SYSTEM_SCAN_CAP = 4_000


def _system_text(body: dict) -> str:
    return " ".join(_system_texts(body)).lower()[:_SYSTEM_SCAN_CAP]
