"""
agentledger — command-line loop runner for Agentic Ledger.

`agentledger run` wraps any agent command in an observable, budgeted loop
(the Ralph pattern): each iteration re-executes the command with a fresh
context, every LLM call is attributed to the run via path-segment base URLs
(no header support needed in the client), and the loop stops on a completion
promise, a budget ceiling, or the iteration cap — whichever comes first.

    agentledger run --max-iterations 50 --budget 25 -- \
        claude -p "$(cat PROMPT.md)" --dangerously-skip-permissions

The proxy must be running (python -m agentledger.proxy). Set
AGENTLEDGER_COMPLETION_PROMISE on the proxy (e.g. "COMPLETE") to let the
agent end the loop early by printing the promise in its final response.
"""

import argparse
import os
import subprocess
import sys
import uuid
from typing import Optional

import httpx


def _fetch_status(proxy: str, run_id: str, api_key: Optional[str]) -> Optional[dict]:
    """Best-effort run status from the proxy. None when unreachable/unknown."""
    headers = {"x-agentledger-api-key": api_key} if api_key else {}
    try:
        resp = httpx.get(f"{proxy}/api/runs/{run_id}", headers=headers, timeout=5.0)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _decide_stop(
    status: Optional[dict],
    iteration: int,
    max_iterations: int,
    budget: Optional[float],
) -> Optional[str]:
    """Return a stop reason after `iteration` completed iterations, or None."""
    if status is not None:
        if status.get("status") == "complete":
            return "completion promise detected"
        cost = status.get("total_cost_usd") or 0
        if budget is not None and cost >= budget:
            return f"budget reached (${cost:.2f} of ${budget:.2f})"
    if iteration >= max_iterations:
        return f"max iterations reached ({max_iterations})"
    return None


def _iteration_env(proxy: str, run_id: str, iteration: int) -> dict:
    """Child env pointing every base-URL-configurable client at the proxy,
    with run/iteration encoded in the URL path."""
    env = dict(os.environ)
    tagged = f"{proxy}/r/{run_id}/{iteration}"
    env["ANTHROPIC_BASE_URL"] = tagged
    env["OPENAI_BASE_URL"] = f"{tagged}/v1"
    env["AGENTLEDGER_RUN_ID"] = run_id
    env["AGENTLEDGER_ITERATION"] = str(iteration)
    return env


def _print_summary(run_id: str, iterations: int, status: Optional[dict], reason: str) -> None:
    print("\n─── agentledger run summary ───", file=sys.stderr)
    print(f"  run:        {run_id}", file=sys.stderr)
    print(f"  iterations: {iterations}", file=sys.stderr)
    print(f"  stopped:    {reason}", file=sys.stderr)
    if status:
        cost = status.get("total_cost_usd") or 0
        print(f"  cost:       ${cost:.4f}", file=sys.stderr)
        print(
            f"  tokens:     {status.get('total_tokens_in') or 0} in / "
            f"{status.get('total_tokens_out') or 0} out",
            file=sys.stderr,
        )
        print(f"  calls:      {status.get('call_count') or 0} "
              f"({status.get('flagged_calls') or 0} flagged)", file=sys.stderr)
        print(f"  status:     {status.get('status')}", file=sys.stderr)
    print(f"  dashboard:  open the proxy URL and filter run {run_id}", file=sys.stderr)


def run_command(args: argparse.Namespace) -> int:
    if not args.command:
        print("error: no command given — usage: agentledger run [options] -- <command...>",
              file=sys.stderr)
        return 2

    proxy = args.proxy.rstrip("/")
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"
    api_key = os.environ.get("AGENTLEDGER_API_KEY")
    last_exit = 0
    status: Optional[dict] = None
    reason = "loop never started"

    print(f"agentledger: starting run {run_id} "
          f"(max {args.max_iterations} iterations"
          f"{f', budget ${args.budget:.2f}' if args.budget else ''}) via {proxy}",
          file=sys.stderr)

    iteration = 0
    while True:
        iteration += 1
        print(f"agentledger: iteration {iteration}/{args.max_iterations}", file=sys.stderr)
        try:
            result = subprocess.run(  # noqa: S603 — running the user's own command is the point
                args.command, env=_iteration_env(proxy, run_id, iteration),
            )
            last_exit = result.returncode
        except KeyboardInterrupt:
            reason = "interrupted"
            break
        except FileNotFoundError:
            print(f"error: command not found: {args.command[0]}", file=sys.stderr)
            return 127

        if last_exit != 0 and args.stop_on_error:
            reason = f"command exited {last_exit}"
            break

        status = _fetch_status(proxy, run_id, api_key)
        if status is None and iteration == 1:
            print(
                "agentledger: warning — no calls recorded for this run yet; "
                "is the proxy running and the agent using ANTHROPIC_BASE_URL/OPENAI_BASE_URL?",
                file=sys.stderr,
            )
        stop = _decide_stop(status, iteration, args.max_iterations, args.budget)
        if stop:
            reason = stop
            break

    status = _fetch_status(proxy, run_id, api_key) or status
    _print_summary(run_id, iteration, status, reason)
    return last_exit


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentledger",
        description="Agentic Ledger CLI — observable, budgeted agent loops.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    run_p = sub.add_parser(
        "run",
        help="Run a command in a loop with per-iteration attribution, "
             "budget stop, and completion-promise detection.",
    )
    run_p.add_argument("--run-id", default=None, help="Run id (default: generated)")
    run_p.add_argument("--max-iterations", type=int, default=10)
    run_p.add_argument("--budget", type=float, default=None,
                       help="Stop when the run's total cost (USD) reaches this")
    run_p.add_argument("--proxy", default=os.environ.get("AGENTLEDGER_URL", "http://localhost:8000"))
    run_p.add_argument("--stop-on-error", action="store_true",
                       help="Stop the loop when the command exits non-zero")
    run_p.add_argument("command", nargs=argparse.REMAINDER,
                       help="Command to loop, after --")

    sub.add_parser(
        "mcp",
        help="Serve the MCP server over stdio (for Claude Desktop-style "
             "subprocess configs; reads the ledger DB via AGENTLEDGER_DSN).",
    )

    args = parser.parse_args(argv)
    if args.subcommand == "mcp":
        from agentledger.mcp_stdio import main as mcp_main
        return mcp_main()
    if args.subcommand != "run":
        parser.print_help()
        return 2
    # argparse.REMAINDER keeps a leading "--" — drop it.
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    return run_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
