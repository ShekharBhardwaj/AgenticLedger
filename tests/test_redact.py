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
    apply_tool_execution_policy,
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


REAL_KEY_SHAPES = [
    "sk-ant-api03-AbCdEf0123456789_-AbCdEf0123456789",         # Anthropic
    "sk-proj-AbCdEf0123456789AbCdEf0123456789",                # OpenAI project
    "github_pat_11ABCDEFG0123456789abcdefghij",               # GitHub fine-grained
    "gho_AbCdEf0123456789AbCdEf012",                           # GitHub OAuth
    "AIzaSyA-bCdEfGhIjKlMnOpQrStUvWxYz012345",                 # Google (AIza + 35)
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdef0123456789ABCDEF",  # JWT
]


def test_redactor_catches_real_key_shapes():
    """Found live: sk-ant- and sk-proj- keys, JWTs and bearer tokens passed
    through because the pattern wanted twelve alphanumerics right after
    'sk-'. Every shape a provider actually issues must be caught."""
    r = Redactor(categories=["api_key"])
    for secret in REAL_KEY_SHAPES:
        out = r.redact_text(f"token: {secret} end")
        assert secret not in out, secret
        assert "[REDACTED:api_key]" in out, secret
    # A bearer header keeps its scheme and loses only the credential.
    out = r.redact_text("Authorization: Bearer AbCdEf0123456789AbCdEf0123456789")
    assert out == "Authorization: Bearer [REDACTED:api_key]"
    # Ordinary prose and short prefixed words are left alone.
    assert r.redact_text("version 1.2.3 built by sk-team") == "version 1.2.3 built by sk-team"


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


def test_tool_execution_policy_follows_capture_level():
    """Tool arguments escaped both controls (they were popped out before the
    policy ran and saved raw), against the stated metadata guarantee."""
    rows = [{"tool_name": "Bash", "arguments": f"curl -H 'x: {KEY}' https://x", "latency_ms": 5}]
    full = apply_tool_execution_policy([dict(r) for r in rows], "full", Redactor(categories=["api_key"]))
    assert KEY not in full[0]["arguments"] and "[REDACTED:api_key]" in full[0]["arguments"]
    assert full[0]["tool_name"] == "Bash"            # names and timing are metadata
    meta = apply_tool_execution_policy([dict(r) for r in rows], CAPTURE_METADATA, None)
    assert meta[0]["arguments"] is None and meta[0]["tool_name"] == "Bash"
    same = apply_tool_execution_policy([dict(r) for r in rows], "full", None)
    assert same[0]["arguments"] == rows[0]["arguments"]
    assert apply_tool_execution_policy([], "full", None) == []


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


def _two_call_tool_flow(client, session, name, args):
    """Call N issues a tool call, call N+1 feeds the result back: the shape
    the loop engine pairs into one tool_executions row."""
    user = {"role": "user", "content": "fetch the thing"}
    client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": [user]},
                headers={"x-agenticledger-session-id": session})
    client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": [
        user,
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": name, "arguments": args}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]}, headers={"x-agenticledger-session-id": session})
    return client.get(f"/api/sessions/{session}/tools").json()


def test_proxy_redacts_tool_arguments_in_stored_executions(proxy):
    """Tool arguments are content: a key the agent typed into a tool call must
    not reach the tool_executions table in the clear. Found live: they were
    popped out before the capture policy ran and saved raw."""
    from .conftest import openai_tool_call
    args = f'{{"cmd":"curl -H \'x-key: {KEY}\' https://x"}}'
    client = proxy(
        handler=lambda r: httpx.Response(200, json=openai_response(
            tool_calls=[openai_tool_call(name="Bash", arguments=args)])),
        redactor=Redactor(categories=["api_key"]),
    )
    tools = _two_call_tool_flow(client, "s-tool-redact", "Bash", args)
    assert len(tools) == 1 and tools[0]["tool_name"] == "Bash"
    assert KEY not in str(tools[0]["arguments"])
    assert "[REDACTED:api_key]" in str(tools[0]["arguments"])


def test_proxy_metadata_level_drops_tool_arguments(proxy):
    """At the metadata level the tool name stays (it is metadata, like the
    model id) and the arguments go, matching the stated guarantee."""
    from .conftest import openai_tool_call
    args = '{"path":"/etc/secret.txt"}'
    client = proxy(
        handler=lambda r: httpx.Response(200, json=openai_response(
            tool_calls=[openai_tool_call(name="Read", arguments=args)])),
        capture_level="metadata",
    )
    tools = _two_call_tool_flow(client, "s-tool-meta", "Read", args)
    assert len(tools) == 1 and tools[0]["tool_name"] == "Read"
    assert tools[0]["arguments"] in (None, "null")
    assert "secret.txt" not in str(tools[0]["arguments"])


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
