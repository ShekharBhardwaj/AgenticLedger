"""Tests for capture-time governance: redaction (redact.py) and capture levels.

Governance transforms only the stored/traced/broadcast copy — the agent always
receives the real upstream response.
"""

import httpx2 as httpx

from agenticledger.proxy.redact import (
    BUILTIN_CATEGORIES,
    CAPTURE_METADATA,
    Redactor,
    apply_capture_policy,
    build_redactor,
    normalize_capture_level,
)

from .conftest import openai_response

EMAIL = "alice@example.com"
KEY = "sk-abcdef0123456789ABCDEF"


# ── Redactor units ────────────────────────────────────────────────────────────

def test_redactor_replaces_known_pii():
    r = Redactor(categories=["email", "api_key"])
    out = r.redact_text(f"contact {EMAIL} using {KEY}")
    assert EMAIL not in out and KEY not in out
    assert "[REDACTED:email]" in out and "[REDACTED:api_key]" in out


def test_redactor_scrubs_nested_structures():
    r = Redactor(categories=["email"])
    scrubbed = r.scrub([{"role": "user", "content": f"my email is {EMAIL}"}])
    assert EMAIL not in scrubbed[0]["content"]
    assert "[REDACTED:email]" in scrubbed[0]["content"]


def test_redactor_disabled_when_no_patterns():
    assert not Redactor().enabled
    assert Redactor(categories=["email"]).enabled


def test_build_redactor_specs():
    assert build_redactor("") is None
    assert build_redactor("off") is None or not build_redactor("off").enabled
    assert build_redactor("all").enabled
    assert len(build_redactor("all")._patterns) == len(BUILTIN_CATEGORIES)
    custom = build_redactor("", '{"badword": "secret"}')
    assert custom.redact_text("this is secret") == "this is [REDACTED:badword]"


def test_normalize_capture_level():
    assert normalize_capture_level("metadata") == "metadata"
    assert normalize_capture_level("FULL") == "full"
    assert normalize_capture_level("nonsense") == "full"
    assert normalize_capture_level(None) == "full"


# ── apply_capture_policy units ────────────────────────────────────────────────

class _Req:
    def __init__(self):
        self.messages = [{"role": "user", "content": f"email {EMAIL}"}]
        self.tools = [{"name": "t"}]
        self.system_prompt = f"system {EMAIL}"
        self.tool_results = [{"content": EMAIL}]


class _Resp:
    def __init__(self):
        self.content = f"reply {EMAIL}"
        self.tool_calls = [{"name": "t", "arguments": EMAIL}]
        self.thinking = f"pondering {EMAIL}"


def test_metadata_level_strips_all_content():
    req, resp = _Req(), _Resp()
    apply_capture_policy(req, resp, CAPTURE_METADATA, None)
    assert req.messages == [] and req.tools is None and req.system_prompt is None
    assert req.tool_results is None and resp.content is None and resp.tool_calls is None


def test_full_level_with_redactor_redacts_content():
    req, resp = _Req(), _Resp()
    apply_capture_policy(req, resp, "full", Redactor(categories=["email"]))
    assert EMAIL not in req.messages[0]["content"]
    assert EMAIL not in req.system_prompt
    assert EMAIL not in resp.content
    assert EMAIL not in resp.tool_calls[0]["arguments"]


# ── End-to-end through the proxy ──────────────────────────────────────────────

def test_proxy_redacts_stored_copy_but_not_agent_response(proxy):
    client = proxy(
        handler=lambda r: httpx.Response(200, json=openai_response(content=f"the key is {KEY}")),
        redactor=Redactor(categories=["email", "api_key"]),
    )
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": f"reach me at {EMAIL}"}]},
        headers={"x-agenticledger-session-id": "s-redact"},
    )
    # The agent still receives the real, unredacted upstream response.
    assert KEY in resp.json()["choices"][0]["message"]["content"]

    # But the stored copy is redacted.
    stored = client.get("/session/s-redact").json()[0]
    assert EMAIL not in str(stored["messages"])
    assert "[REDACTED:email]" in str(stored["messages"])
    assert KEY not in (stored["content"] or "")
    assert "[REDACTED:api_key]" in stored["content"]


def test_proxy_metadata_level_keeps_metrics_drops_content(proxy):
    client = proxy(
        handler=lambda r: httpx.Response(200, json=openai_response(content="secret reply")),
        capture_level="metadata",
    )
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "secret prompt"}]},
        headers={"x-agenticledger-session-id": "s-meta"},
    )
    stored = client.get("/session/s-meta").json()[0]
    # Content is gone …
    assert stored["messages"] == []
    assert stored["content"] is None
    # … but metrics/metadata remain.
    assert stored["model_id"] == "gpt-4o"
    assert stored["tokens_in"] is not None
    assert stored["cost_usd"] is not None
