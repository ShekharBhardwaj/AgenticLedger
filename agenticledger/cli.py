"""
agenticledger — command-line loop runner for Agentic Ledger.

`agenticledger run` wraps any agent command in an observable, budgeted loop
(the Ralph pattern): each iteration re-executes the command with a fresh
context, every LLM call is attributed to the run via path-segment base URLs
(no header support needed in the client), and the loop stops on a completion
promise, a budget ceiling, or the iteration cap — whichever comes first.

    agenticledger run --max-iterations 50 --budget 25 -- \
        claude -p "$(cat PROMPT.md)" --dangerously-skip-permissions

The proxy must be running (python -m agenticledger.proxy). Set
AGENTICLEDGER_COMPLETION_PROMISE on the proxy (e.g. "COMPLETE") to let the
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
    headers = {"x-agenticledger-api-key": api_key} if api_key else {}
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
    env["AGENTICLEDGER_RUN_ID"] = run_id
    env["AGENTICLEDGER_ITERATION"] = str(iteration)
    return env


def _print_summary(run_id: str, iterations: int, status: Optional[dict], reason: str) -> None:
    print("\n─── agenticledger run summary ───", file=sys.stderr)
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
        print("error: no command given — usage: agenticledger run [options] -- <command...>",
              file=sys.stderr)
        return 2

    proxy = args.proxy.rstrip("/")
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"
    api_key = os.environ.get("AGENTICLEDGER_API_KEY")
    last_exit = 0
    status: Optional[dict] = None
    reason = "loop never started"

    print(f"agenticledger: starting run {run_id} "
          f"(max {args.max_iterations} iterations"
          f"{f', budget ${args.budget:.2f}' if args.budget else ''}) via {proxy}",
          file=sys.stderr)

    iteration = 0
    while True:
        iteration += 1
        print(f"agenticledger: iteration {iteration}/{args.max_iterations}", file=sys.stderr)
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
                "agenticledger: warning — no calls recorded for this run yet; "
                "is the proxy running and the agent using ANTHROPIC_BASE_URL/OPENAI_BASE_URL?",
                file=sys.stderr,
            )
        stop = _decide_stop(status, iteration, args.max_iterations, args.budget)
        if stop:
            reason = stop
            break

    status = _fetch_status(proxy, run_id, api_key) or status
    # The runner knows the loop exited — tell the ledger so the run reads
    # "ended" immediately instead of after the inactivity window. Best
    # effort: a failure here must never change the loop's exit code.
    try:
        headers = {"x-agenticledger-api-key": api_key} if api_key else {}
        httpx.post(f"{proxy}/api/runs/{run_id}/end", headers=headers, timeout=5.0)
    except Exception:
        pass
    _print_summary(run_id, iteration, status, reason)
    return last_exit


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agenticledger",
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
    run_p.add_argument("--proxy", default=os.environ.get("AGENTICLEDGER_URL", "http://localhost:8000"))
    run_p.add_argument("--stop-on-error", action="store_true",
                       help="Stop the loop when the command exits non-zero")
    run_p.add_argument("command", nargs=argparse.REMAINDER,
                       help="Command to loop, after --")

    sub.add_parser(
        "mcp",
        help="Serve the MCP server over stdio (for Claude Desktop-style "
             "subprocess configs; reads the ledger DB via AGENTICLEDGER_DSN).",
    )

    conn_p = sub.add_parser(
        "connect",
        help="Wire a framework to the ledger — writes its config for it "
             "(claude-code, bmad, openclaw); no schema memorization.",
    )
    conn_p.add_argument("framework", choices=["claude-code", "bmad", "openclaw"])
    conn_p.add_argument("--port", default=os.environ.get("AGENTICLEDGER_PORT", "8000"))
    conn_p.add_argument("--app-id", default=None,
                        help="App tag for captured calls (default: the framework name)")

    cfg_p = sub.add_parser(
        "config",
        help="Read or change one setting in agenticledger.toml — "
             "e.g. agenticledger config set proxy.upstream_url https://api.anthropic.com",
    )
    cfg_sub = cfg_p.add_subparsers(dest="config_action")
    cfg_set = cfg_sub.add_parser("set", help="Set one key (section.key value)")
    cfg_set.add_argument("key")
    cfg_set.add_argument("value")
    cfg_get = cfg_sub.add_parser("get", help="Show one key's value in the file")
    cfg_get.add_argument("key")
    cfg_unset = cfg_sub.add_parser("unset", help="Comment a key out again")
    cfg_unset.add_argument("key")
    cfg_sub.add_parser("path", help="Which config file is in effect")

    init_p = sub.add_parser(
        "init",
        help="Write a commented agenticledger.toml — one config file instead "
             "of env vars in a command.",
    )
    init_p.add_argument("--path", default="agenticledger.toml",
                        help="Where to write it (default: ./agenticledger.toml)")

    sub.add_parser(
        "start",
        help="Run the proxy in the background — terminal freed, survives the "
             "window closing; logs to ~/.agenticledger/proxy.log.",
    )
    sub.add_parser("stop", help="Stop the background proxy.")
    sub.add_parser("status", help="Is the proxy up, what version, is the store healthy?")
    logs_p = sub.add_parser("logs", help="Show the background proxy's log.")
    logs_p.add_argument("-n", "--lines", type=int, default=50)
    logs_p.add_argument("-f", "--follow", action="store_true")
    sub.add_parser(
        "serve",
        help="Run the proxy in the FOREGROUND (containers, debugging) — "
             "same as python -m agenticledger.proxy.",
    )

    args = parser.parse_args(argv)
    if args.subcommand == "mcp":
        from agenticledger.mcp_stdio import main as mcp_main
        return mcp_main()
    if args.subcommand == "connect":
        from agenticledger.connect import connect as connect_fw
        return connect_fw(args.framework, port=args.port, app_id=args.app_id)
    if args.subcommand == "config":
        from agenticledger.config import find_config, get_value, set_value
        action = getattr(args, "config_action", None)
        if action == "path":
            found = find_config()
            print(found or "no config file yet. Run: agenticledger init")
            return 0 if found else 1
        if action == "get":
            val = get_value(args.key)
            found = find_config()
            if val is not None:
                print(val)
                # The file note goes to stderr so `$(config get ...)` in
                # scripts still captures the bare value.
                print(f"(from {found})", file=sys.stderr)
            elif found:
                print(f"(not set in {found})")
            else:
                print("(no config file found)")
            return 0
        if action in ("set", "unset"):
            target = set_value(args.key, args.value if action == "set" else None)
            what = f"{args.key} = {args.value}" if action == "set" else f"{args.key} (unset)"
            print(f"{target}: {what}")
            from agenticledger import service
            known, loaded = service.running_proxy_config()
            if known and loaded != target:
                if loaded is None:
                    print("Warning: the proxy running right now started "
                          "without a config file, so it is not using the "
                          "file this just edited.")
                else:
                    print(f"Warning: the proxy running right now reads its "
                          f"settings from {loaded}, not from the file this "
                          f"just edited.")
            print("Restart to apply: agenticledger stop && agenticledger start")
            return 0
        cfg_p.print_help()
        return 2
    if args.subcommand == "init":
        from agenticledger.config import init_config
        target = init_config(args.path)
        print(f"Wrote {target} — open it, uncomment what you need, then: agenticledger start")
        return 0
    if args.subcommand in ("start", "stop", "status"):
        from agenticledger import service
        return getattr(service, args.subcommand)()
    if args.subcommand == "logs":
        from agenticledger import service
        return service.logs(lines=args.lines, follow=args.follow)
    if args.subcommand == "serve":
        os.execv(sys.executable, [sys.executable, "-m", "agenticledger.proxy"])
    if args.subcommand != "run":
        parser.print_help()
        return 2
    # argparse.REMAINDER keeps a leading "--" — drop it.
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    return run_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
