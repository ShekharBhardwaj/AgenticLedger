"""Unit tests for agentledger/proxy/detect.py — zero-config agent detection.

detect_agent fingerprints well-known clients (Claude Code first) from headers
and body so untagged traffic still gets a framework tag, an agent identity,
and a real session id. Explicit headers always win — precedence is enforced
in _extract_meta and covered by the proxy integration tests.
"""

from agentledger.proxy.detect import detect_agent

_CC_UUID = "3f2a1b0c-4d5e-6f70-8192-a3b4c5d6e7f8"
_CC_METADATA_USER = f"user_ab12cd34_account_9f8e7d6c-1a2b-3c4d-5e6f-708192a3b4c5_session_{_CC_UUID}"


def test_claude_cli_user_agent_detected():
    meta = detect_agent({"user-agent": "claude-cli/2.0.14 (external, cli)"}, None)
    assert meta["framework"] == "claude-code"
    assert meta["agent_name"] == "claude-code"


def test_session_uuid_extracted_from_metadata_user_id():
    body = {"model": "claude-sonnet-4", "metadata": {"user_id": _CC_METADATA_USER}}
    meta = detect_agent({}, body)
    assert meta["session_id"] == _CC_UUID
    assert meta["framework"] == "claude-code"


def test_system_prompt_prefix_detected_in_block_list_form():
    """Claude Code sends the system prompt as content blocks with cache_control."""
    body = {
        "model": "claude-sonnet-4",
        "system": [
            {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude.",
             "cache_control": {"type": "ephemeral"}},
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    meta = detect_agent({}, body)
    assert meta["framework"] == "claude-code"


def test_system_prompt_prefix_detected_in_string_form():
    body = {"system": "You are Claude Code, Anthropic's official CLI for Claude."}
    assert detect_agent({}, body)["framework"] == "claude-code"


def test_openai_style_leading_system_message_checked():
    body = {"messages": [{"role": "system", "content": "You are Claude Code, etc."}]}
    assert detect_agent({}, body)["framework"] == "claude-code"


def test_litellm_client_tagged_without_agent_identity():
    """LiteLLM is a client library, not an agent — framework tag only."""
    meta = detect_agent({"user-agent": "litellm/1.72.0"}, None)
    assert meta["framework"] == "litellm"
    assert meta["agent_name"] is None


def test_unknown_traffic_detects_nothing():
    meta = detect_agent(
        {"user-agent": "python-httpx/0.27"},
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert meta == {"framework": None, "agent_name": None, "session_id": None}


def test_malformed_body_shapes_tolerated():
    """None/odd-typed fields must never raise."""
    for body in (
        None,
        {},
        {"metadata": None},
        {"metadata": {"user_id": None}},
        {"system": 42},
        {"system": [{"no": "text"}, "raw-string"]},
        {"messages": "not-a-list"},
        {"messages": [None]},
    ):
        detect_agent({}, body)  # must not raise
