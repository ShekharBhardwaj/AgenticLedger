"""
agenticledger — command-line loop runner for Agentic Ledger.

`agenticledger run` puts a name on whatever command you already run:

    agenticledger run nightly-digest -- python agent.py

The command runs exactly as before; behind the scenes its LLM calls go
through the ledger and land on the run tile named `nightly-digest`, with
each launch counted as the next iteration (the ledger is asked where the
run left off, so numbering survives restarts by construction). Attribution
travels in the base URL path (no header support needed in the client).

With loop flags it becomes an observable, budgeted loop (the Ralph
pattern): each iteration re-executes the command with a fresh context, and
the loop stops on a completion promise, a budget ceiling, or the iteration
cap — whichever comes first:

    agenticledger run overnight --max-iterations 50 --budget 25 -- \
        claude -p "$(cat PROMPT.md)" --dangerously-skip-permissions

The proxy must be running (python -m agenticledger.proxy). Set
AGENTICLEDGER_COMPLETION_PROMISE on the proxy (e.g. "COMPLETE") to let the
agent end the loop early by printing the promise in its final response.
"""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx2 as httpx


def _file_under_project(proxy: str, run_id: str, project: str,
                        api_key: Optional[str]) -> None:
    """Best-effort: file the run under a dashboard project (#104). A failure
    never touches the loop; the run just stays unfiled."""
    headers = {"x-agenticledger-api-key": api_key} if api_key else {}
    with contextlib.suppress(Exception):
        httpx.put(f"{proxy}/api/labels/run/{run_id}",
                  json={"project": project}, headers=headers, timeout=5.0)


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


_RUN_ID_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def _default_run_id() -> str:
    """A run id a human can read in the sidebar: the folder's name plus a
    short timestamp (#84). Running again the same minute reuses the id,
    which simply continues the run — with iteration numbering carrying on
    (#85). Random hex only if the folder name sanitizes away entirely."""
    folder = _RUN_ID_SAFE.sub("-", Path.cwd().name).strip("-.")
    stamp = time.strftime("%m%d-%H%M")
    return f"{folder}-{stamp}" if folder else f"run-{uuid.uuid4().hex[:8]}"


_AL_MARKER = "_agenticledger_run"


class _ClaudeLocalSettings:
    """Claude Code applies a project's .claude/settings.json env OVER the
    process environment, which silently strips the runner's /r/<run>/<iter>
    attribution from the base URL (walkthrough finding #73). Local project
    settings outrank shared ones in Claude Code's documented precedence, so
    for the duration of a run we maintain .claude/settings.local.json with
    the tagged URL, preserving any real local settings and cleaning up
    after ourselves. A marker key identifies our file so a crash's
    leftovers are recognized and replaced on the next run, never treasured.
    """

    def __init__(self, cwd: Path) -> None:
        self.path = cwd / ".claude" / "settings.local.json"
        self.active = (cwd / ".claude" / "settings.json").is_file()
        self.original: Optional[str] = None
        if not self.active:
            return
        if self.path.is_file():
            text = self.path.read_text()
            try:
                stale = _AL_MARKER in json.loads(text)
            except Exception:
                stale = False
            self.original = None if stale else text

    def point_at(self, tagged_url: str) -> None:
        if not self.active:
            return
        base: dict = {}
        if self.original is not None:
            with contextlib.suppress(Exception):
                base = json.loads(self.original)
        env = dict(base.get("env") or {})
        env["ANTHROPIC_BASE_URL"] = tagged_url
        base["env"] = env
        base[_AL_MARKER] = True
        self.path.write_text(json.dumps(base, indent=2) + "\n")

    def restore(self) -> None:
        if not self.active:
            return
        with contextlib.suppress(FileNotFoundError):
            if self.original is not None:
                self.path.write_text(self.original)
            else:
                self.path.unlink()


def _iteration_env(proxy: str, run_id: str, iteration: int) -> dict:
    """Child env pointing every base-URL-configurable client at the proxy,
    with run/iteration encoded in the URL path."""
    env = dict(os.environ)
    tagged = f"{proxy}/r/{run_id}/{iteration}"
    env["ANTHROPIC_BASE_URL"] = tagged
    env["ANTHROPIC_BEDROCK_BASE_URL"] = tagged
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
    calls = (status or {}).get("call_count") or 0
    if not calls:
        # The worst kind of nothing: the loop "completed" but no call ever
        # reached the ledger. Say it plainly — the user was sent to stare
        # at an empty dashboard twice before this line existed.
        print("  ⚠ no calls reached the ledger. The command likely failed "
              "before calling any model — its own output above has the "
              "reason. (Common: broken cloud credentials in this shell; "
              "try running the command bare first.)", file=sys.stderr)
    else:
        print(f"  dashboard:  open the proxy URL and filter run {run_id}", file=sys.stderr)


def run_command(args: argparse.Namespace) -> int:
    if getattr(args, "instance", None):
        # Record through a named ledger: resolve the port IT answers on.
        from agenticledger import service
        service.use_instance(args.instance)
        args.proxy = f"http://localhost:{service.effective_port()}"
    if not args.command:
        print("error: no command given — usage: agenticledger run [options] -- <command...>",
              file=sys.stderr)
        return 2

    proxy = args.proxy.rstrip("/")
    run_id = args.run_id or _default_run_id()
    api_key = os.environ.get("AGENTICLEDGER_API_KEY")
    last_exit = 0
    status: Optional[dict] = None
    reason = "loop never started"

    single = args.max_iterations == 1 and args.budget is None
    launch_word = ("single launch" if single
                   else f"max {args.max_iterations} iterations")
    print(f"agenticledger: starting run {run_id} "
          f"({launch_word}"
          f"{f', budget ${args.budget:.2f}' if args.budget else ''}) via {proxy}",
          file=sys.stderr)

    # A reused run id continues the run, so the numbering continues too:
    # ask the ledger where the run left off (#85). Offline or brand new,
    # the answer is silence and we start at 1, exactly as before.
    base_iteration = 0
    prior = _fetch_status(proxy, run_id, api_key)
    if prior and isinstance(prior.get("iterations"), int) and prior["iterations"] > 0:
        base_iteration = prior["iterations"]
        print(f"agenticledger: run {run_id} already has {base_iteration} "
              f"iteration(s) recorded; continuing at {base_iteration + 1}",
              file=sys.stderr)

    project = getattr(args, "project", None)
    if project:
        _file_under_project(proxy, run_id, project, api_key)

    local_settings = _ClaudeLocalSettings(Path.cwd())
    if local_settings.active:
        print("agenticledger: project has .claude/settings.json; using "
              "settings.local.json for run attribution (restored on exit)",
              file=sys.stderr)

    iteration = 0
    try:
      while True:
        iteration += 1
        if not single:
            print(f"agenticledger: iteration {iteration}/{args.max_iterations}",
                  file=sys.stderr)
        tagged = base_iteration + iteration
        local_settings.point_at(f"{proxy}/r/{run_id}/{tagged}")
        try:
            result = subprocess.run(  # noqa: S603 — running the user's own command is the point
                args.command, env=_iteration_env(proxy, run_id, tagged),
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
            reason = "launch complete" if single else stop
            break
    finally:
        local_settings.restore()

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


def _managed_install_hint() -> Optional[str]:
    """When the install is owned by a tool pip must not fight, name the
    right one-liner instead of upgrading behind its back."""
    exe = sys.executable
    if "/pipx/" in exe:
        return "this install is managed by pipx. Run: pipx upgrade agentic-ledger"
    if "/uv/tools/" in exe:
        return "this install is managed by uv. Run: uv tool upgrade agentic-ledger"
    if exe.startswith(("/opt/homebrew/", "/usr/local/Cellar/")):
        return "this install is managed by Homebrew. Run: brew upgrade agentic-ledger"
    return None


def upgrade_command(args: argparse.Namespace) -> int:
    """agenticledger upgrade: self-upgrade without knowing which Python owns
    the install (#92). sys.executable IS the owning environment, so its pip
    upgrades the right one every time. Like `pricing update`, the network is
    touched only when the user runs this."""
    from importlib.metadata import PackageNotFoundError, version

    hint = _managed_install_hint()
    if hint:
        print(f"agenticledger: {hint}", file=sys.stderr)
        return 1

    try:
        old = version("agentic-ledger")
    except PackageNotFoundError:
        old = None

    target = "agentic-ledger"
    if args.source:
        src = Path(args.source).expanduser().resolve()
        if not src.exists():
            print(f"error: --from path does not exist: {src}", file=sys.stderr)
            return 2
        target = f"agentic-ledger @ {src.as_uri()}"

    print(f"agenticledger: upgrading with {sys.executable}", file=sys.stderr)
    result = subprocess.run(  # noqa: S603 — the owning interpreter's own pip
        [sys.executable, "-m", "pip", "install", "--upgrade", target],
    )
    if result.returncode != 0:
        print(
            f"agenticledger: upgrade failed (pip exit {result.returncode}). "
            f"If this environment has no pip, try: "
            f"uv pip install --python {sys.executable} --upgrade agentic-ledger",
            file=sys.stderr,
        )
        return result.returncode

    probe = subprocess.run(  # noqa: S603
        [sys.executable, "-c",
         "from importlib.metadata import version; print(version('agentic-ledger'))"],
        capture_output=True, text=True,
    )
    new = probe.stdout.strip() or "?"
    print(f"agentic-ledger: {old or '?'} -> {new}")
    print("Restart to apply: agenticledger stop && agenticledger start")
    return 0


def _pop_run_name(argv: list) -> Optional[str]:
    """`agenticledger run nightly-digest -- cmd...`: when the token right
    after `run` is a bare word (not an option, not the `--` divider), it is
    the run name. Removed from argv so argparse never sees it."""
    if (len(argv) >= 2 and argv[0] == "run"
            and argv[1] != "--" and not argv[1].startswith("-")):
        return argv.pop(1)
    return None


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agenticledger",
        description="Agentic Ledger CLI — observable, budgeted agent loops.",
    )
    sub = parser.add_subparsers(dest="subcommand")

    run_p = sub.add_parser(
        "run",
        usage="agenticledger run [name] [options] -- <command...>",
        help="Put a name on any agent command (agenticledger run <name> -- "
             "<cmd>), or loop it with budget stop and completion-promise "
             "detection.",
    )
    run_p.add_argument("--run-id", default=None,
                       help="Run id (same as giving the name positionally; "
                            "default: generated from folder + minute)")
    run_p.add_argument("--project", default=None,
                       help="File the run under this dashboard project")
    run_p.add_argument("--max-iterations", type=int, default=None,
                       help="Loop the command up to N times "
                            "(default: 1 when a name is given, else 10)")
    run_p.add_argument("--budget", type=float, default=None,
                       help="Stop when the run's total cost (USD) reaches this")
    run_p.add_argument("--proxy", default=os.environ.get("AGENTICLEDGER_URL", "http://localhost:8000"))
    run_p.add_argument("--name", dest="instance", default=None,
                       help="Record through a NAMED ledger instance instead of "
                            "the everyday one (resolves its port; overrides --proxy).")
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

    start_p = sub.add_parser(
        "start",
        help="Run the proxy in the background — terminal freed, survives the "
             "window closing; logs to ~/.agenticledger/proxy.log.",
    )
    start_p.add_argument("--name", default=None,
                         help="Start a NAMED instance beside the everyday ledger "
                              "(own state, own database). Needs --port.")
    start_p.add_argument("--port", type=int, default=None,
                         help="Port for this instance (sets AGENTICLEDGER_PORT).")
    pricing_p = sub.add_parser(
        "pricing", help="Price pack utilities.")
    pricing_sub = pricing_p.add_subparsers(dest="pricing_cmd", required=True)
    pricing_sub.add_parser(
        "update",
        help="Fetch the latest price packs from the repository into "
             "~/.agenticledger/pricing/ (overrides built-ins; restart to apply). "
             "Network is touched only when you run it.")

    upgrade_p = sub.add_parser(
        "upgrade",
        help="Upgrade agentic-ledger in the environment that owns this "
             "install (no guessing which pip). Then: stop && start.",
    )
    upgrade_p.add_argument("--from", dest="source", default=None,
                           help="Install from a local checkout instead of "
                                "PyPI (path to the repo)")

    doctor_p = sub.add_parser(
        "doctor",
        help="The whole truth of this machine: every install on PATH, who "
             "shadows whom, what can actually run, and what is serving. "
             "--fix applies the fixes it names.",
    )
    doctor_p.add_argument("--fix", action="store_true",
                          help="Evict shadow installs (keeping the newest), "
                               "offer the PATH-prepend cleanup, re-diagnose.")

    share_p = sub.add_parser(
        "share",
        help="Your dashboard on another device: an https tunnel you own "
             "(via cloudflared), key enforced, QR code to pair. "
             "--wifi for a same-network link without a tunnel; "
             "--rotate to un-pair every device; --stop closes the tunnel.",
    )
    share_p.add_argument("--wifi", action="store_true",
                         help="Same-network pairing link, no tunnel (plain http).")
    share_p.add_argument("--rotate", action="store_true",
                         help="Mint a fresh pairing key; every paired device un-pairs.")
    share_p.add_argument("--stop", action="store_true",
                         help="Close the tunnel; the old link dies.")
    share_p.add_argument("--name", default=None,
                         help="Share a named instance instead of the everyday ledger.")
    stop_p = sub.add_parser("stop", help="Stop the background proxy.")
    stop_p.add_argument("--name", default=None, help="Stop a named instance.")
    status_p = sub.add_parser("status", help="Is the proxy up, what version, is the store healthy?")
    status_p.add_argument("--name", default=None, help="Status of a named instance.")
    logs_p = sub.add_parser("logs", help="Show the background proxy's log.")
    logs_p.add_argument("--name", default=None, help="Logs of a named instance.")
    logs_p.add_argument("-n", "--lines", type=int, default=50)
    logs_p.add_argument("-f", "--follow", action="store_true")
    sub.add_parser(
        "serve",
        help="Run the proxy in the FOREGROUND (containers, debugging) — "
             "same as python -m agenticledger.proxy.",
    )

    argv = list(argv) if argv is not None else sys.argv[1:]
    run_name = _pop_run_name(argv)
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
    if args.subcommand == "upgrade":
        return upgrade_command(args)
    if args.subcommand == "doctor":
        from agenticledger.doctor import doctor_command
        return doctor_command(fix=args.fix)
    if args.subcommand == "share":
        from agenticledger import service
        if args.name:
            service.use_instance(args.name)
        return service.share(stop=args.stop, wifi=args.wifi, rotate=args.rotate)
    if args.subcommand == "pricing":
        from .pricing_update import update
        return update()

    if args.subcommand == "init":
        from agenticledger.config import init_config
        target = init_config(args.path)
        print(f"Wrote {target} — open it, uncomment what you need, then: agenticledger start")
        return 0
    if args.subcommand in ("start", "stop", "status"):
        from agenticledger import service
        if getattr(args, "name", None):
            service.use_instance(args.name)
        if getattr(args, "port", None):
            os.environ["AGENTICLEDGER_PORT"] = str(args.port)
        return getattr(service, args.subcommand)()
    if args.subcommand == "logs":
        from agenticledger import service
        if args.name:
            service.use_instance(args.name)
        return service.logs(lines=args.lines, follow=args.follow)
    if args.subcommand == "serve":
        os.execv(sys.executable, [sys.executable, "-m", "agenticledger.proxy"])
    if args.subcommand != "run":
        parser.print_help()
        return 2
    # argparse.REMAINDER keeps a leading "--" — drop it.
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if run_name:
        if args.run_id:
            print("error: run name given twice, positionally and via "
                  "--run-id; pick one", file=sys.stderr)
            return 2
        args.run_id = run_name
    if args.max_iterations is None:
        # A named run is wrapper mode: the command runs once per launch and
        # each launch is the next iteration. Unnamed keeps the loop default.
        args.max_iterations = 1 if run_name else 10
    return run_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
