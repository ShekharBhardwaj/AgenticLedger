"""agenticledger doctor — the reasoning is pure functions, tested here
without a real machine's mess (the real machine supplied the spec)."""

import os
import stat

from agenticledger.doctor import Install, _shebang, diagnose, find_installs


def _script(dirpath, shebang="#!/usr/bin/python3"):
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / "agenticledger"
    p.write_text(f"{shebang}\nprint('hi')\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def test_shebang_plain_and_env_forms(tmp_path):
    a = _script(tmp_path / "a", "#!/opt/venv/bin/python")
    b = _script(tmp_path / "b", "#!/usr/bin/env python3.12")
    assert str(_shebang(a)) == "/opt/venv/bin/python"
    assert str(_shebang(b)) == "python3.12"


def test_find_installs_orders_like_the_shell(tmp_path):
    first = _script(tmp_path / "one")
    second = _script(tmp_path / "two")
    path = os.pathsep.join([str(first.parent), str(second.parent)])
    found = find_installs(path)
    assert [i.script for i in found] == [first, second]
    assert found[0].wins and not found[1].wins


def test_find_installs_dedupes_symlinked_copies(tmp_path):
    real = _script(tmp_path / "real")
    linkdir = tmp_path / "link"
    linkdir.mkdir()
    (linkdir / "agenticledger").symlink_to(real)
    path = os.pathsep.join([str(linkdir), str(real.parent)])
    assert len(find_installs(path)) == 1


def _inst(script, wins=False, version=None, error=None):
    from pathlib import Path
    return Install(script=Path(script), interpreter=Path("/py"), wins=wins,
                   version=version, error=error)


def test_broken_shadow_names_the_working_install():
    verdicts = diagnose(
        [_inst("/sys/agenticledger", wins=True, error="ImportError: boom"),
         _inst("/real/agenticledger", version="1.0")],
        {"running": True, "version": "1.0"})
    text = " ".join(verdicts)
    assert "THE ONE YOUR SHELL RUNS" in text
    assert "shadowing the working install" in text
    assert "pip uninstall -y agentic-ledger" in text


def test_version_disagreement_between_working_installs():
    verdicts = diagnose(
        [_inst("/a/agenticledger", wins=True, version="2.0"),
         _inst("/b/agenticledger", version="1.0")],
        {"running": True, "version": "2.0"})
    assert any("two working installs disagree" in v for v in verdicts)


def test_stale_running_version_asks_for_restart():
    verdicts = diagnose(
        [_inst("/a/agenticledger", wins=True, version="2.0")],
        {"running": True, "version": "1.0"})
    assert any("restart applies it" in v for v in verdicts)


def test_healthy_machine_has_no_verdicts():
    assert diagnose(
        [_inst("/a/agenticledger", wins=True, version="2.0")],
        {"running": True, "version": "2.0"}) == []


def test_empty_path_says_install():
    assert "pip install agentic-ledger" in diagnose([], {"running": False})[0]
