"""Unit tests for agenticledger/cli.py — the `agenticledger run` loop runner."""

import argparse
import sys

from agenticledger.cli import (
    _decide_stop,
    _iteration_env,
    _pop_run_name,
    main,
    run_command,
)


def test_decide_stop_on_completion_promise():
    status = {"status": "complete", "total_cost_usd": 0.5}
    assert "promise" in _decide_stop(status, 3, 10, None)


def test_decide_stop_on_budget():
    status = {"status": "running", "total_cost_usd": 25.5}
    assert "budget" in _decide_stop(status, 3, 10, 25.0)


def test_decide_stop_on_max_iterations_even_without_status():
    assert "max iterations" in _decide_stop(None, 10, 10, None)


def test_decide_continue_when_under_all_limits():
    status = {"status": "running", "total_cost_usd": 1.0}
    assert _decide_stop(status, 3, 10, 25.0) is None


def test_iteration_env_points_clients_at_tagged_proxy_path():
    env = _iteration_env("http://localhost:8000", "night-1", 4)
    assert env["ANTHROPIC_BASE_URL"] == "http://localhost:8000/r/night-1/4"
    assert env["OPENAI_BASE_URL"] == "http://localhost:8000/r/night-1/4/v1"
    assert env["AGENTICLEDGER_RUN_ID"] == "night-1"
    assert env["AGENTICLEDGER_ITERATION"] == "4"
    # Bedrock clients read their own base-URL variable (#104).
    assert env["ANTHROPIC_BEDROCK_BASE_URL"] == "http://localhost:8000/r/night-1/4"


def test_run_loops_until_max_iterations(tmp_path):
    """With no proxy reachable, the loop still runs the command max times."""
    marker = tmp_path / "count.txt"
    cmd = [sys.executable, "-c",
           f"open(r'{marker}', 'a').write('x')"]
    args = argparse.Namespace(
        command=cmd, run_id="t-run", max_iterations=3, budget=None,
        proxy="http://127.0.0.1:1",  # nothing listens here — status stays None
        stop_on_error=False,
    )
    exit_code = run_command(args)
    assert exit_code == 0
    assert marker.read_text() == "xxx"


def test_run_stops_on_error_when_requested(tmp_path):
    marker = tmp_path / "count.txt"
    cmd = [sys.executable, "-c",
           f"open(r'{marker}', 'a').write('x'); raise SystemExit(3)"]
    args = argparse.Namespace(
        command=cmd, run_id="t-err", max_iterations=5, budget=None,
        proxy="http://127.0.0.1:1", stop_on_error=True,
    )
    exit_code = run_command(args)
    assert exit_code == 3
    assert marker.read_text() == "x"  # stopped after the first failure


def test_main_requires_subcommand():
    assert main([]) == 2


def test_main_strips_leading_dashdash(tmp_path):
    marker = tmp_path / "ran.txt"
    code = main([
        "run", "--run-id", "t-dd", "--max-iterations", "1",
        "--proxy", "http://127.0.0.1:1",
        "--", sys.executable, "-c", f"open(r'{marker}', 'w').write('y')",
    ])
    assert code == 0
    assert marker.read_text() == "y"


# --- #73: project .claude/settings.json must not steal run attribution ---
#
# Claude Code applies a project's shared settings env over the process
# environment, which erased the runner's /r/<run>/<iter> tag. The runner
# now maintains .claude/settings.local.json (higher precedence) with the
# tagged URL for the duration of the run. These tests drive run_command
# in a scratch project and read what the local-settings file said during
# each iteration — exactly what Claude Code itself would have read.

_READ_LOCAL = (
    "import json,pathlib;"
    "d=json.loads(pathlib.Path('.claude/settings.local.json').read_text());"
    "open('seen.txt','a').write(d['env']['ANTHROPIC_BASE_URL']+'\\n')"
)


def _project_with_shared_settings(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        '{"env": {"ANTHROPIC_BASE_URL": "http://localhost:8000"}}')
    return tmp_path


def _run(tmp_path, monkeypatch, iterations=2, run_id="t-attr"):
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        command=[sys.executable, "-c", _READ_LOCAL],
        run_id=run_id, max_iterations=iterations, budget=None,
        proxy="http://127.0.0.1:1", stop_on_error=False,
    )
    return run_command(args)


def test_runner_outranks_project_settings_with_tagged_local_settings(
        tmp_path, monkeypatch):
    _project_with_shared_settings(tmp_path)
    assert _run(tmp_path, monkeypatch) == 0
    assert (tmp_path / "seen.txt").read_text().splitlines() == [
        "http://127.0.0.1:1/r/t-attr/1",
        "http://127.0.0.1:1/r/t-attr/2",
    ]
    # Cleaned up: the run's overlay does not outlive the run.
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_runner_preserves_real_local_settings(tmp_path, monkeypatch):
    _project_with_shared_settings(tmp_path)
    original = '{"permissions": {"allow": ["Bash"]}, "env": {"FOO": "bar"}}'
    (tmp_path / ".claude" / "settings.local.json").write_text(original)
    assert _run(tmp_path, monkeypatch, iterations=1) == 0
    # During the run the user's own keys rode along with the tag.
    seen = (tmp_path / "seen.txt").read_text().strip()
    assert seen == "http://127.0.0.1:1/r/t-attr/1"
    # After the run the file is byte-for-byte what the user had.
    assert (tmp_path / ".claude" / "settings.local.json").read_text() == original


def test_runner_merge_keeps_user_keys_during_run(tmp_path, monkeypatch):
    _project_with_shared_settings(tmp_path)
    (tmp_path / ".claude" / "settings.local.json").write_text(
        '{"env": {"FOO": "bar"}}')
    read_all = (
        "import json,pathlib,shutil;"
        "shutil.copy('.claude/settings.local.json','during.json')"
    )
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        command=[sys.executable, "-c", read_all],
        run_id="t-merge", max_iterations=1, budget=None,
        proxy="http://127.0.0.1:1", stop_on_error=False,
    )
    assert run_command(args) == 0
    import json
    during = json.loads((tmp_path / "during.json").read_text())
    assert during["env"]["FOO"] == "bar"
    assert during["env"]["ANTHROPIC_BASE_URL"].endswith("/r/t-merge/1")


def test_runner_replaces_stale_overlay_from_a_crash(tmp_path, monkeypatch):
    _project_with_shared_settings(tmp_path)
    (tmp_path / ".claude" / "settings.local.json").write_text(
        '{"_agenticledger_run": true, "env": {"ANTHROPIC_BASE_URL": "http://dead/r/old/9"}}')
    assert _run(tmp_path, monkeypatch, iterations=1, run_id="t-new") == 0
    assert (tmp_path / "seen.txt").read_text().strip() == \
        "http://127.0.0.1:1/r/t-new/1"
    # The stale leftover is not mistaken for user data: it is removed.
    assert not (tmp_path / ".claude" / "settings.local.json").exists()


def test_runner_leaves_projects_without_shared_settings_alone(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        command=[sys.executable, "-c", "pass"],
        run_id="t-none", max_iterations=1, budget=None,
        proxy="http://127.0.0.1:1", stop_on_error=False,
    )
    assert run_command(args) == 0
    assert not (tmp_path / ".claude").exists()


# --- #84/#85: readable default run ids, and reruns that continue counting ---

def test_default_run_id_is_folder_plus_timestamp(tmp_path, monkeypatch, capsys):
    proj = tmp_path / "night shift!"
    proj.mkdir()
    monkeypatch.chdir(proj)
    args = argparse.Namespace(
        command=[sys.executable, "-c", "pass"],
        run_id=None, max_iterations=1, budget=None,
        proxy="http://127.0.0.1:1", stop_on_error=False,
    )
    assert run_command(args) == 0
    err = capsys.readouterr().err
    # Spaces and punctuation sanitized, timestamp appended, no hex soup.
    assert "starting run night-shift-" in err
    assert "run-" not in err.split("starting run ")[1].split(" ")[0]


def test_rerun_continues_iteration_numbering(tmp_path, monkeypatch):
    import agenticledger.cli as cli
    monkeypatch.chdir(tmp_path)
    # The ledger says this run already has 4 iterations recorded.
    monkeypatch.setattr(cli, "_fetch_status", lambda proxy, run_id, key: {
        "status": "running", "iterations": 4, "total_cost_usd": 0.0})
    seen = tmp_path / "seen.txt"
    args = argparse.Namespace(
        command=[sys.executable, "-c",
                 f"import os; open(r'{seen}','a').write(os.environ['AGENTICLEDGER_ITERATION']+'\\n')"],
        run_id="carry-on", max_iterations=2, budget=None,
        proxy="http://127.0.0.1:1", stop_on_error=False,
    )
    assert run_command(args) == 0
    # Execution-local loop ran twice, but the tagged iterations continue.
    assert seen.read_text().splitlines() == ["5", "6"]


def test_fresh_or_offline_run_still_starts_at_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = tmp_path / "seen.txt"
    args = argparse.Namespace(
        command=[sys.executable, "-c",
                 f"import os; open(r'{seen}','a').write(os.environ['AGENTICLEDGER_ITERATION']+'\\n')"],
        run_id="fresh", max_iterations=2, budget=None,
        proxy="http://127.0.0.1:1",  # nothing listens: status is None
        stop_on_error=False,
    )
    assert run_command(args) == 0
    assert seen.read_text().splitlines() == ["1", "2"]


# --- #104: agenticledger run <name> -- <cmd> — one-word run naming ---

def test_pop_run_name_takes_bare_word_after_run():
    argv = ["run", "nightly-digest", "--", "python", "agent.py"]
    assert _pop_run_name(argv) == "nightly-digest"
    assert argv == ["run", "--", "python", "agent.py"]


def test_pop_run_name_leaves_options_and_divider_alone():
    for argv in (
        ["run", "--", "python", "agent.py"],
        ["run", "--max-iterations", "3", "--", "cmd"],
        ["status"],
        ["run"],
    ):
        before = list(argv)
        assert _pop_run_name(argv) is None
        assert argv == before


def test_named_run_is_wrapper_mode(tmp_path):
    """A positional name means: run the command ONCE under that name.
    The child sees the name and the iteration in its environment."""
    marker = tmp_path / "seen.txt"
    code = main([
        "run", "wrappy", "--proxy", "http://127.0.0.1:1",
        "--", sys.executable, "-c",
        "import os; open(r'%s', 'a').write("
        "os.environ['AGENTICLEDGER_RUN_ID'] + ' ' + "
        "os.environ['AGENTICLEDGER_ITERATION'] + chr(10))" % marker,
    ])
    assert code == 0
    assert marker.read_text() == "wrappy 1\n"  # once, not the loop default


def test_named_run_still_loops_when_asked(tmp_path):
    marker = tmp_path / "count.txt"
    code = main([
        "run", "wrappy2", "--max-iterations", "2",
        "--proxy", "http://127.0.0.1:1",
        "--", sys.executable, "-c", f"open(r'{marker}', 'a').write('x')",
    ])
    assert code == 0
    assert marker.read_text() == "xx"


def test_name_given_twice_is_an_error(tmp_path, capsys):
    code = main([
        "run", "wrappy", "--run-id", "other",
        "--proxy", "http://127.0.0.1:1", "--", "true",
    ])
    assert code == 2
    assert "twice" in capsys.readouterr().err
