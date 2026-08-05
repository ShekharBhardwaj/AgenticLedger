"""`agenticledger pricing update`: fetch, validate strictly, install
atomically-enough. Network data never reaches the cost math unvalidated,
and a bad response changes nothing on disk."""

import json

import pytest

from agenticledger.pricing_update import PackValidationError, update, validate_pack
from agenticledger.proxy import pricing


def _good_pack():
    return {"provider": "test", "models": {
        "test-model": {"input": 1.0, "output": 2.0},
        "test-mini": {"input": 0.1, "output": 0.2, "note": "cheap"}}}


def test_validate_pack_accepts_the_real_schema():
    assert validate_pack("t.json", _good_pack()) == 2


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p.pop("provider"), "provider"),
    (lambda p: p["models"].__setitem__("BAD-CASE", {"input": 1, "output": 2}), "lowercase"),
    (lambda p: p["models"]["test-model"].__setitem__("inputs", 1), "unknown keys"),
    (lambda p: p["models"]["test-model"].__setitem__("input", -1), "bad input"),
    (lambda p: p["models"]["test-model"].__setitem__("output", "x"), "bad output"),
])
def test_validate_pack_names_the_problem(mutate, needle):
    pack = _good_pack()
    mutate(pack)
    with pytest.raises(PackValidationError, match=needle):
        validate_pack("t.json", pack)


def _fetcher(packs: dict[str, dict]):
    listing = [{"name": n, "download_url": f"mem://{n}"} for n in packs]

    def fetch(url: str):
        if url.startswith("mem://"):
            return packs[url[6:]]
        return listing
    return fetch


def test_update_installs_validated_packs(tmp_path, capsys):
    out = tmp_path / "pricing"
    rc = update(fetch=_fetcher({"a.json": _good_pack()}), out=out)
    assert rc == 0
    installed = json.loads((out / "a.json").read_text())
    assert installed["models"]["test-model"]["input"] == 1.0
    assert "restart" in capsys.readouterr().out.lower()


def test_update_installs_nothing_when_any_pack_is_bad(tmp_path):
    bad = _good_pack()
    bad["models"]["test-model"]["input"] = -5
    with pytest.raises(PackValidationError):
        update(fetch=_fetcher({"good.json": _good_pack(), "bad.json": bad}),
               out=tmp_path / "pricing")
    assert not (tmp_path / "pricing").exists()


def test_user_packs_override_builtins(tmp_path, monkeypatch):
    user_dir = tmp_path / ".agenticledger" / "pricing"
    user_dir.mkdir(parents=True)
    (user_dir / "override.json").write_text(json.dumps({
        "provider": "test",
        "models": {"gpt-4o": {"input": 99.0, "output": 99.0}}}))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    saved = dict(pricing._PRICES)
    try:
        pricing._load_user_packs()
        assert pricing.compute_cost("gpt-4o", 1_000_000, 0, provider="openai") == 99.0
    finally:
        pricing._PRICES.clear()
        pricing._PRICES.update(saved)
