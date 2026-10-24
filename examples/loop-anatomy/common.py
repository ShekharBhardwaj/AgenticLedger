"""Shared pieces for the loop-anatomy demo: one task, one toolbox, one
model, recorded through Agentic Ledger. Only the loop shape differs
between ralph.py and react.py, so the ledger's Compare shows the shape and
nothing else.

The task comes from a case module (cases/<name>/case.py): its documents
or fixture, its tools, its judge of done, and the one "how you work"
paragraph each loop adds to the shared task text.

Environment:
    ANTHROPIC_API_KEY      your key; the ledger passes it through untouched
    AGENTICLEDGER_URL      the ledger (default http://localhost:8000)
    AGENTICLEDGER_API_KEY  only if your ledger has a key set
    LOOP_MODEL             model id (default claude-sonnet-5-5)
    LOOP_CASE              case name (default textkit; the scripts' --case sets it)
"""

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import anthropic
import httpx

HERE = Path(__file__).resolve().parent
LEDGER = os.environ.get("AGENTICLEDGER_URL", "http://localhost:8000").rstrip("/")
MODEL = os.environ.get("LOOP_MODEL", "claude-sonnet-5-5")
PROMISE = "<promise>DONE</promise>"
PROJECT = "loop-anatomy"
SCRIPTED = os.environ.get("LOOP_SCRIPTED") == "1"   # the runner sets this when the model is the scripted one


def load_case(name: str):
    path = HERE / "cases" / name / "case.py"
    if not path.is_file():
        raise SystemExit(f"no case named {name!r}: expected {path}")
    spec = importlib.util.spec_from_file_location(f"case_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


CASE_NAME = os.environ.get("LOOP_CASE", "textkit")
case = load_case(CASE_NAME)
TASK = case.TASK
TOOLS = case.TOOLS
NOTEBOOK = case.NOTEBOOK
RALPH_RULE = case.RALPH_RULE.replace("{PROMISE}", PROMISE)
REACT_RULE = case.REACT_RULE.replace("{PROMISE}", PROMISE)
SYSTEM = "You are a careful analyst working with the tools you are given."


def make_workspace(run_id: str) -> Path:
    """A private copy of the case per run, so runs never touch each other."""
    ws = HERE / "runs" / run_id
    ws.parent.mkdir(parents=True, exist_ok=True)
    case.make_workspace(ws)
    return ws


def run_tool(ws: Path, name: str, args: dict) -> str:
    return case.run_tool(ws, name, args)


def judge(ws: Path) -> tuple[bool, str]:
    return case.judge(ws)


def run_name(kind: str) -> str:
    return f"{kind}-{time.strftime('%m%d-%H%M%S')}"


def label_name(base: str) -> str:
    return base


class Recorder:
    """Talks to the ledger: the model client that records, plus labels."""

    def __init__(self, run_id: str, agent: str):
        self.run_id = run_id
        self.agent = agent
        self._headers = {
            "x-agenticledger-run-id": run_id,
            "x-agenticledger-framework": "loop-anatomy",
            "x-agenticledger-agent-name": agent,
        }
        key = os.environ.get("AGENTICLEDGER_API_KEY")
        if key:
            self._headers["x-agenticledger-api-key"] = key
        self.client = anthropic.Anthropic(base_url=LEDGER, default_headers=self._headers,
                                          api_key=os.environ.get("ANTHROPIC_API_KEY") or ("scripted" if SCRIPTED else None))
        self.calls = 0

    def ask(self, messages: list, system: str, *, iteration: int, session_id: str):
        self.calls += 1
        return self.client.messages.create(
            model=MODEL, max_tokens=4000, system=system, tools=TOOLS, messages=messages,
            extra_headers={"x-agenticledger-iteration": str(iteration),
                           "x-agenticledger-session-id": session_id},
        )

    def _key_headers(self) -> dict:
        return {k: v for k, v in self._headers.items() if "api-key" in k}

    def label(self, name: str, icon: str, color: str) -> None:
        try:
            httpx.put(f"{LEDGER}/api/labels/run/{self.run_id}",
                      json={"name": name, "icon": icon, "color": color, "project": PROJECT},
                      headers=self._key_headers(), timeout=5)
        except Exception as e:
            print(f"(could not label the run: {e})", file=sys.stderr)

    def end(self) -> None:
        try:
            httpx.post(f"{LEDGER}/api/runs/{self.run_id}/end", headers=self._key_headers(), timeout=5)
        except Exception as e:
            print(f"(could not mark the run ended: {e})", file=sys.stderr)


def text_of(response) -> str:
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text")


def tool_uses(response) -> list:
    return [b for b in response.content if getattr(b, "type", "") == "tool_use"]


def converse(rec: Recorder, ws: Path, messages: list, system: str, *,
             iteration: int, session_id: str, max_calls: int, log) -> tuple[str, str]:
    """Run one conversation until the model stops calling tools or the call
    cap hits. Returns (final text, why it stopped). The ledger refusing a
    call (budget, stop all, loop block) ends the conversation too; the
    script never argues with the wall."""
    calls = 0
    while True:
        if calls >= max_calls:
            return "", "call cap"
        try:
            resp = rec.ask(messages, system, iteration=iteration, session_id=session_id)
        except anthropic.APIStatusError as e:
            if e.status_code in (402, 429):
                return "", f"refused by the ledger ({e.status_code})"
            raise
        calls += 1
        messages.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
        uses = tool_uses(resp)
        if resp.stop_reason != "tool_use" or not uses:
            return text_of(resp), "model stopped"
        results = []
        for u in uses:
            out = run_tool(ws, u.name, u.input or {})
            first = out.splitlines()[0][:70] if out else ""
            log(f"    {u.name}({json.dumps(u.input or {})[:60]}) -> {first}")
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
        messages.append({"role": "user", "content": results})


def report(rec: Recorder, ws: Path, started: float, *, iterations: int, why: str) -> None:
    ok, verdict = judge(ws)
    print()
    print(f"{rec.agent}: {verdict} | {iterations} iteration(s) | {rec.calls} model calls | "
          f"{time.time() - started:.0f}s | stopped: {why}")
    print(f"open: {LEDGER}/app#/runs/{rec.run_id}")
    rec.end()
