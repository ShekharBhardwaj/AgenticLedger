"""
Capture-time data governance: redaction and capture levels.

Agentic Ledger captures full prompts and responses by default. For privacy-sensitive
or regulated deployments, two controls reduce what is stored (and traced/broadcast):

* **Capture level** (``AGENTLEDGER_CAPTURE_LEVEL``):
    - ``full``     — store everything (default), with redaction applied if configured.
    - ``metadata`` — store only metrics/metadata (model, tokens, cost, latency, agent,
                     status); strip messages, system prompt, response content, and tools.

* **Redaction** (``AGENTLEDGER_REDACT`` + ``AGENTLEDGER_REDACT_PATTERNS``): replace PII /
  secrets in captured text with ``[REDACTED:<label>]`` before anything is persisted.

Crucially, these transform only the captured/stored copy — the agent always receives
the real, unmodified upstream response.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

CAPTURE_FULL = "full"
CAPTURE_METADATA = "metadata"
CAPTURE_LEVELS = (CAPTURE_FULL, CAPTURE_METADATA)

# Built-in PII/secret patterns. Conservative — they err toward redacting.
_BUILTIN_PATTERNS = {
    "email":       r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "ssn":         r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b(?:\d[ -]?){13,16}\b",
    "ip":          r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
    "api_key":     r"\b(?:sk|pk|rk)-[A-Za-z0-9]{12,}\b|\bAKIA[0-9A-Z]{16}\b|"
                   r"\bghp_[A-Za-z0-9]{20,}\b|\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
}
BUILTIN_CATEGORIES = tuple(_BUILTIN_PATTERNS)


class Redactor:
    """Applies a set of regex patterns, replacing matches with ``[REDACTED:<label>]``."""

    def __init__(self, categories=(), custom_patterns=()) -> None:
        self._patterns: list[tuple[str, re.Pattern]] = []
        for cat in categories:
            if cat in _BUILTIN_PATTERNS:
                self._patterns.append((cat, re.compile(_BUILTIN_PATTERNS[cat])))
            else:
                logger.warning("Unknown redaction category %r — ignoring", cat)
        for label, pattern in custom_patterns:
            self._patterns.append((label, pattern if hasattr(pattern, "sub") else re.compile(pattern)))

    @property
    def enabled(self) -> bool:
        return bool(self._patterns)

    def redact_text(self, text):
        if not isinstance(text, str):
            return text
        for label, pattern in self._patterns:
            text = pattern.sub(f"[REDACTED:{label}]", text)
        return text

    def scrub(self, value):
        """Recursively redact every string inside a nested dict/list structure."""
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        return value


def build_redactor(redact_spec: str = "", patterns_spec: str = "") -> Optional[Redactor]:
    """Build a Redactor from env-style specs, or None if redaction is disabled.

    redact_spec: "all"/"1"/"on" for every built-in, or a comma list of categories.
    patterns_spec: optional JSON — a {label: regex} map or a list of regexes.
    """
    spec = (redact_spec or "").strip()
    categories: list[str] = []
    if spec:
        if spec.lower() in ("1", "all", "true", "on", "yes"):
            categories = list(BUILTIN_CATEGORIES)
        else:
            categories = [c.strip() for c in spec.split(",") if c.strip()]

    custom: list[tuple[str, re.Pattern]] = []
    pspec = (patterns_spec or "").strip()
    if pspec:
        try:
            data = json.loads(pspec)
            items = data.items() if isinstance(data, dict) else [
                (f"custom_{i}", p) for i, p in enumerate(data)
            ]
            for label, pattern in items:
                custom.append((label, re.compile(pattern)))
        except Exception as exc:
            logger.warning("AGENTLEDGER_REDACT_PATTERNS ignored (invalid): %s", exc)

    if not categories and not custom:
        return None
    return Redactor(categories, custom)


def normalize_capture_level(level: Optional[str]) -> str:
    level = (level or CAPTURE_FULL).strip().lower()
    if level not in CAPTURE_LEVELS:
        logger.warning("Unknown AGENTLEDGER_CAPTURE_LEVEL %r — using 'full'", level)
        return CAPTURE_FULL
    return level


def apply_capture_policy(req, resp, level: str, redactor: Optional[Redactor]) -> None:
    """Mutate the canonical request/response in place per the capture policy.

    Only the captured copy is affected — never the response returned to the agent.
    """
    if level == CAPTURE_METADATA:
        # Keep metrics/metadata; drop all prompt/response content.
        req.messages = []
        req.tools = None
        req.system_prompt = None
        req.tool_results = None
        resp.content = None
        resp.tool_calls = None
        resp.thinking = None
        return

    if redactor is not None and redactor.enabled:
        req.messages = redactor.scrub(req.messages)
        if req.system_prompt:
            req.system_prompt = redactor.redact_text(req.system_prompt)
        if req.tool_results:
            req.tool_results = redactor.scrub(req.tool_results)
        if resp.content:
            resp.content = redactor.redact_text(resp.content)
        if resp.tool_calls:
            resp.tool_calls = redactor.scrub(resp.tool_calls)
        if resp.thinking:
            resp.thinking = redactor.redact_text(resp.thinking)
