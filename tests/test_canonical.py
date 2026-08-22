"""The canonicalization contract (#99, contract 1): wire content becomes
stable meaning in exactly one place, and every identity signal derives
from the stable view. Enforced here, not just documented."""

import json
from pathlib import Path

from agenticledger.proxy import canonical
from agenticledger.proxy.normalize import normalize_request

PROXY = Path(__file__).parent.parent / "agenticledger" / "proxy"
WIRE = Path(__file__).parent / "fixtures" / "wire"


def _canonical_from_fixture(name: str):
    record = json.loads((WIRE / f"{name}.json").read_text())
    body = json.loads(record["request"]["body"])
    path = record["request"]["path"]
    return normalize_request(body, path[path.index("/v1/"):])


def test_no_identity_module_hashes_wire_bytes():
    """Grep-able invariant: outside canonical.py, the identity modules
    never touch hashlib. A new volatile quirk is one table entry here,
    never a local workaround elsewhere."""
    identity_modules = ["loops.py", "detect.py"]
    identity_modules += [str(p.relative_to(PROXY)) for p in PROXY.glob("providers/*.py")]
    for name in identity_modules:
        source = (PROXY / name).read_text()
        assert "hashlib" not in source, f"{name} hashes on its own; use canonical"


def test_same_agent_hashes_the_same_across_salted_invocations():
    """Two real Claude Code invocations, each with its own billing nonce
    and session paths, must share one stable system digest. This is the
    corpus proving the table."""
    a = canonical.stable_view(_canonical_from_fixture("claude-code-plain-main"))
    b = canonical.stable_view(_canonical_from_fixture("claude-code-tool-main"))
    assert a.system_digest == b.system_digest
    # And the raw prompts genuinely differed (the nonce), or this proves nothing.
    raw_a = _canonical_from_fixture("claude-code-plain-main").system_prompt
    raw_b = _canonical_from_fixture("claude-code-tool-main").system_prompt
    assert raw_a != raw_b


def test_a_conversation_keeps_its_chain_prefix_across_requests():
    """The tool round's follow-up request must extend the opener's chain:
    cache markers moved and the nonce changed in between (#89)."""
    opener = canonical.stable_view(_canonical_from_fixture("claude-code-tool-main"))
    follow = canonical.stable_view(_canonical_from_fixture("claude-code-tool-followup"))
    n = len(opener.message_chain)
    assert follow.message_chain[:n] == opener.message_chain


def test_volatile_table_entries_are_applied():
    text = ("x-anthropic-billing-header: cc_version=2.1.220.abc; cc_entrypoint=cli;\n"
            "You are the worker. Scratch: /tmp/11111111-1111-4111-8111-111111111111/x")
    stable = canonical.stable_system_text(text)
    assert "billing-header" not in stable
    assert "11111111-1111" not in stable and "/tmp/*/x" in stable
    blocks = [{"type": "text", "text": "x-anthropic-billing-header: nonce"},
              {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}]
    assert canonical.stable_content(blocks) == "hello"  # text-only arrays collapse to their string


def test_every_volatile_rule_names_its_fixture():
    """Each entry in the table must cite the real traffic that demanded it,
    so the next maintainer can replay the reason."""
    source = (PROXY / "canonical.py").read_text()
    table = source[source.index("VOLATILE_BLOCK_PREFIXES"):source.index("_HASH_CONTENT_CAP")]
    rules = table.count('"x-anthropic-billing-header"') + table.count('"cache_control"') + table.count("(_UUID_RE,")
    assert rules == 3
    assert table.count("Fixture: tests/fixtures/wire/") == 3
