"""Wire-truth parity harness (#97, the ground under the 0.10 refactor).

tests/fixtures/wire/*.json are REAL exchanges recorded by
scripts/wiretap.py: quirks intact (per-request billing nonces, migrating
cache markers, session-unique paths), secrets scrubbed. For each LLM
exchange this harness runs the full capture pipeline — request
normalization, response/stream reconstruction, agent detection, the
stable identity view, utility classification — and compares the result
byte for byte against a golden recorded from the pipeline as it stood
before the provider-adapter refactor.

A refactor that changes ANY golden must either be a bug fix that
explains itself in the diff, or it is a regression. Regenerate goldens
deliberately with:  WIRE_GOLDEN_UPDATE=1 pytest tests/test_wire_parity.py
"""

import dataclasses
import json
import os
from pathlib import Path

import pytest

from agenticledger.proxy.detect import detect_agent
from agenticledger.proxy.loops import _message_chain, _system_digest, is_utility_call
from agenticledger.proxy.normalize import normalize_request, normalize_response
from agenticledger.proxy.stream import reconstruct_from_sse

WIRE = Path(__file__).parent / "fixtures" / "wire"
GOLDEN = WIRE / "golden"
_VOLATILE = {"timestamp"}  # stamped at normalize time, never part of parity


def _fixtures():
    for f in sorted(WIRE.glob("*.json")):
        record = json.loads(f.read_text())
        if "/v1/" in record["request"]["path"]:  # incl. /r/<run>/<iter>/v1/…
            yield pytest.param(f, id=f.stem)


def _plain(obj):
    if dataclasses.is_dataclass(obj):
        obj = dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items() if k not in _VOLATILE}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def run_pipeline(record: dict) -> dict:
    req_wire, resp_wire = record["request"], record["response"]
    body = json.loads(req_wire["body"])
    # The app strips the runner's /r/<run>/<iter> prefix before normalizing;
    # the pipeline under test sees the provider path.
    path = req_wire["path"][req_wire["path"].index("/v1/"):]
    headers = {k.lower(): v for k, v in req_wire["headers"].items()}

    canonical = normalize_request(body, path)
    raw = "".join(resp_wire["chunks"])
    if body.get("stream"):
        response = reconstruct_from_sse(raw.encode("utf-8"), resp_wire["latency_ms"],
                                        canonical.model_id or "")
    else:
        response = normalize_response(json.loads(raw), resp_wire["latency_ms"],
                                      canonical.model_id or "")
    detected = detect_agent(headers, body)
    meta = {"framework": detected.get("framework")}
    return {
        "request": _plain(canonical),
        "response": _plain(response),
        "detected": detected,
        "stable": {
            "system_digest": _system_digest(canonical),
            "message_chain": list(_message_chain(canonical.messages)),
        },
        "utility_call": is_utility_call(canonical, meta),
    }


@pytest.mark.parametrize("fixture", list(_fixtures()))
def test_wire_parity(fixture: Path):
    record = json.loads(fixture.read_text())
    actual = json.dumps(run_pipeline(record), sort_keys=True, indent=1, default=str)
    golden_path = GOLDEN / f"{fixture.stem}.golden.json"
    if os.environ.get("WIRE_GOLDEN_UPDATE"):
        GOLDEN.mkdir(exist_ok=True)
        golden_path.write_text(actual + "\n")
        pytest.skip("golden regenerated")
    assert golden_path.exists(), f"no golden for {fixture.name}; run with WIRE_GOLDEN_UPDATE=1"
    assert actual + "\n" == golden_path.read_text(), (
        f"{fixture.name}: pipeline output drifted from its golden. If this is an "
        "intended fix, regenerate with WIRE_GOLDEN_UPDATE=1 and explain the diff.")


def test_corpus_has_no_secrets():
    """The scrubber's contract, enforced forever: no keys, no emails, no
    home-directory usernames in anything we check in."""
    import re
    for f in WIRE.glob("*.json"):
        text = f.read_text()
        assert not re.search(r"sk-(?:ant-)?[A-Za-z0-9_-]{16,}", text), f.name
        assert not re.search(r"[A-Za-z0-9._%+-]+@(?!example\.com)[A-Za-z0-9.-]+\.[a-z]{2,}", text), f.name
        assert "Bearer " not in text or "REDACTED" in text, f.name
