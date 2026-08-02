"""Unit tests for agenticledger/proxy/detect.py — zero-config agent detection.

detect_agent fingerprints well-known clients (Claude Code first) from headers
and body so untagged traffic still gets a framework tag, an agent identity,
and a real session id. Explicit headers always win — precedence is enforced
in _extract_meta and covered by the proxy integration tests.
"""

from agenticledger.proxy.detect import detect_agent

_CC_UUID = "3f2a1b0c-4d5e-6f70-8192-a3b4c5d6e7f8"
# claude-cli 1.x flat format
_CC_METADATA_USER = f"user_ab12cd34_account_9f8e7d6c-1a2b-3c4d-5e6f-708192a3b4c5_session_{_CC_UUID}"
# claude-cli 2.x JSON format, as observed on the wire from claude-cli/2.1.220
_CC_METADATA_USER_V2 = (
    '{"device_id":"cbfa40d30f5786d8a860f6771ec29e82a7f07bbd5544e46397e7d74ca19a3826",'
    '"account_uuid":"e9316e9d-aed1-4cff-9a93-e936d862e161",'
    f'"session_id":"{_CC_UUID}"}}'
)


def test_claude_cli_user_agent_detected():
    meta = detect_agent({"user-agent": "claude-cli/2.0.14 (external, cli)"}, None)
    assert meta["framework"] == "claude-code"
    assert meta["agent_name"] == "claude-code"


def test_session_uuid_extracted_from_legacy_metadata_user_id():
    body = {"model": "claude-sonnet-4", "metadata": {"user_id": _CC_METADATA_USER}}
    meta = detect_agent({}, body)
    assert meta["session_id"] == _CC_UUID
    assert meta["framework"] == "claude-code"


def test_session_uuid_extracted_from_json_metadata_user_id():
    """claude-cli 2.x packs metadata.user_id as a JSON blob whose session_id
    key holds the session UUID — the account_uuid must not win instead."""
    body = {"model": "claude-opus-5", "metadata": {"user_id": _CC_METADATA_USER_V2}}
    meta = detect_agent({}, body)
    assert meta["session_id"] == _CC_UUID
    assert meta["framework"] == "claude-code"


def test_json_metadata_tolerates_key_order_and_whitespace():
    body = {"metadata": {"user_id": f'{{ "session_id" : "{_CC_UUID}", "device_id": "ab" }}'}}
    assert detect_agent({}, body)["session_id"] == _CC_UUID


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


def test_billing_header_block_detected_in_sdk_cli_mode():
    """claude -p (sdk-cli) traffic opens with a billing-header block and a
    generic "You are a Claude agent" persona — the cc_* header is the
    fingerprint, as observed from claude-cli/2.1.220."""
    body = {
        "model": "claude-opus-5",
        "system": [
            {"type": "text", "text":
                "x-anthropic-billing-header: cc_version=2.1.220.04c; cc_entrypoint=sdk-cli;"},
            {"type": "text", "text":
                "You are a Claude agent, built on Anthropic's Claude Agent SDK."},
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }
    meta = detect_agent({}, body)
    assert meta["framework"] == "claude-code"
    assert meta["agent_name"] == "claude-code"


def test_billing_header_without_cc_fields_not_claimed():
    body = {"system": "x-anthropic-billing-header: partner=someone-else;"}
    assert detect_agent({}, body)["framework"] is None


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
        {"metadata": {"user_id": '{"session_id": truncated'}},
        {"system": 42},
        {"system": [{"no": "text"}, "raw-string"]},
        {"messages": "not-a-list"},
        {"messages": [None]},
    ):
        detect_agent({}, body)  # must not raise


def test_bmad_persona_detected_from_system_prompt():
    body = {
        "system": [{"type": "text", "text":
            "# dev\n\nCRITICAL: Read the full YAML from .bmad-core/agents/dev.md. "
            "You are James, the Developer. Implement the story."}],
        "messages": [{"role": "user", "content": "implement story 2.3"}],
    }
    meta = detect_agent({}, body)
    assert meta["framework"] == "bmad"
    assert meta["agent_name"] == "bmad:dev"


def test_bmad_test_architect_beats_architect():
    body = {"system": "BMAD-METHOD agent bundle. You are Quinn, the Test Architect."}
    meta = detect_agent({}, body)
    assert meta["framework"] == "bmad"
    assert meta["agent_name"] == "bmad:qa"


def test_bmad_marker_without_persona_still_tags_framework():
    body = {"system": "Loaded from bmad/bmm/agents. Party mode orchestrator."}
    meta = detect_agent({}, body)
    assert meta["framework"] == "bmad"
    assert meta["agent_name"] is None


def test_bmad_on_claude_code_host_wins_framework():
    """BMAD persona running inside Claude Code: bmad tag beats the host tag."""
    body = {
        "system": [{"type": "text", "text": "You are Claude Code, Anthropic's official CLI."},
                   {"type": "text", "text": "Activation: .bmad-core/agents/sm.md — Bob, the Scrum Master."}],
        "messages": [{"role": "user", "content": "draft the next story"}],
        "metadata": {"user_id": _CC_METADATA_USER},
    }
    meta = detect_agent({"user-agent": "claude-cli/2.0.14"}, body)
    assert meta["framework"] == "bmad"
    assert meta["agent_name"] == "bmad:sm"
    assert meta["session_id"] == _CC_UUID  # session inference still works


# ── BMAD v6: personas ship as host-tool skills ───────────────────────────────

def _cc(messages):
    return {"user-agent": "claude-cli/2.1.220"}, {"messages": messages}


def test_bmad_v6_skill_invocation_names_the_persona():
    """The strongest signal BMAD v6 leaves: a Skill tool call naming the
    persona that is actually running."""
    headers, body = _cc([
        {"role": "user", "content": "plan a clock CLI"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Skill",
             "input": {"skill": "bmad-spec", "args": "plan a tiny feature"}}]},
    ])
    d = detect_agent(headers, body)
    assert d["framework"] == "bmad"
    assert d["agent_name"] == "bmad:spec"


def test_bmad_v6_uses_the_latest_invocation():
    """Each request carries the whole history; the persona in force is the
    last one invoked, not the first."""
    headers, body = _cc([
        {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": "bmad-agent-analyst"}}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Skill", "input": {"skill": "bmad-agent-dev"}}]},
    ])
    assert detect_agent(headers, body)["agent_name"] == "bmad:dev"


def test_bmad_v6_skill_listing_tags_the_framework_without_a_persona():
    headers, body = _cc([
        {"role": "user", "content":
            "<system-reminder>available skills: bmad-build, bmad-help, "
            "bmad-agent-dev, bmad-prd</system-reminder> hello"},
    ])
    d = detect_agent(headers, body)
    assert d["framework"] == "bmad" and d["agent_name"] == "bmad"


def test_talking_about_bmad_is_not_running_bmad():
    """A conversation that merely mentions BMAD must stay claude-code —
    otherwise every support chat about the framework gets mislabelled."""
    headers, body = _cc([
        {"role": "user", "content": "should I try bmad-method for this project?"},
        {"role": "assistant", "content": "BMAD is worth a look."},
    ])
    d = detect_agent(headers, body)
    assert d["framework"] == "claude-code"
    assert d["agent_name"] == "claude-code"


def test_bmad_v4_system_prompt_personas_still_win():
    headers = {"user-agent": "claude-cli/2.1.220"}
    body = {"system": "You are the Scrum Master agent from bmad-core.",
            "messages": [{"role": "user", "content": "next story"}]}
    d = detect_agent(headers, body)
    assert d["framework"] == "bmad" and d["agent_name"] == "bmad:sm"


# ── OpenClaw ─────────────────────────────────────────────────────────────────
#
# OpenClaw cannot send custom headers, so its self-identifying system prompt
# is the only fingerprint there is. Verified against live captures: every
# call opens with "You are a personal assistant running inside OpenClaw."

def test_openclaw_system_prompt_names_the_agent():
    got = detect_agent({}, {
        "system": "You are a personal assistant running inside OpenClaw.\n## Tooling\n- exec: Run shell commands",
        "messages": [{"role": "user", "content": "what did I spend?"}],
    })
    assert got["framework"] == "openclaw"
    assert got["agent_name"] == "openclaw"


def test_talking_about_openclaw_is_not_running_openclaw():
    got = detect_agent({}, {
        "messages": [{"role": "user",
                      "content": "I'm running inside OpenClaw, how do I track costs?"}],
    })
    assert got["framework"] is None
    assert got["agent_name"] is None


def test_bmad_riding_on_openclaw_upgrades_to_bmad():
    """A BMAD persona hosted by OpenClaw is BMAD work, same as on claude-code."""
    got = detect_agent({}, {
        "system": ("You are a personal assistant running inside OpenClaw.\n"
                   "bmad-core activation: you are the Developer persona."),
        "messages": [{"role": "user", "content": "implement the story"}],
    })
    assert got["framework"] == "bmad"
    assert got["agent_name"] == "bmad:dev"
