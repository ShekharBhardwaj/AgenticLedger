"""Unit tests for agentledger/cli.py — the `agentledger run` loop runner."""

import argparse
import sys

from agentledger.cli import _decide_stop, _iteration_env, main, run_command


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
    assert env["AGENTLEDGER_RUN_ID"] == "night-1"
    assert env["AGENTLEDGER_ITERATION"] == "4"


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
