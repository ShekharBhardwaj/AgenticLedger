"""Shared pieces for the loop-anatomy demo: one task, one toolbox, one
model, recorded through Agentic Ledger. Only the loop shape differs
between ralph.py and react.py, so the ledger's Compare shows the shape and
nothing else.

Environment:
    ANTHROPIC_API_KEY      your key; the ledger passes it through untouched
    AGENTICLEDGER_URL      the ledger (default http://localhost:8000)
    AGENTICLEDGER_API_KEY  only if your ledger has a key set
    LOOP_MODEL             model id (default claude-sonnet-5-5)
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import anthropic
import httpx

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixture"
LEDGER = os.environ.get("AGENTICLEDGER_URL", "http://localhost:8000").rstrip("/")
MODEL = os.environ.get("LOOP_MODEL", "claude-sonnet-5-5")
PROMISE = "<promise>DONE</promise>"
PROJECT = "loop-anatomy"

TASK = (FIXTURE / "TASK.md").read_text()

TOOLS = [
    {"name": "list_files", "description": "List the files in the working folder.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "read_file", "description": "Read one file from the working folder.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"]}},
    {"name": "write_file", "description": "Write one file in the working folder, replacing it.",
     "input_schema": {"type": "object",
                      "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["path", "content"]}},
    {"name": "run_tests", "description": "Run the test suite and return its output.",
     "input_schema": {"type": "object", "properties": {}}},
]


def make_workspace(run_id: str) -> Path:
    """A private copy of the fixture per run, so runs never touch each other."""
    ws = HERE / "runs" / run_id
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(FIXTURE, ws)
    return ws


def _inside(ws: Path, rel: str) -> Path:
    p = (ws / rel).resolve()
    if ws.resolve() not in p.parents and p != ws.resolve():
        raise ValueError(f"{rel} is outside the working folder")
    return p


def run_tests(ws: Path) -> tuple[bool, str]:
    proc = subprocess.run([sys.executable, "-m", "unittest", "-q", "test_textkit"],
                          cwd=ws, capture_output=True, text=True, timeout=60)
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out[-3000:]


def run_tool(ws: Path, name: str, args: dict) -> str:
    try:
        if name == "list_files":
            return "\n".join(sorted(p.name for p in ws.iterdir() if p.is_file()))
        if name == "read_file":
            return _inside(ws, args["path"]).read_text()
        if name == "write_file":
            _inside(ws, args["path"]).write_text(args["content"])
            return f"wrote {args['path']} ({len(args['content'])} chars)"
        if name == "run_tests":
            ok, out = run_tests(ws)
            return ("ALL TESTS PASS\n" if ok else "TESTS FAILING\n") + out
        return f"unknown tool {name}"
    except Exception as e:  # the model gets the error as a result, like a real shell would
        return f"error: {e}"


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
        self.client = anthropic.Anthropic(base_url=LEDGER, default_headers=self._headers)
        self.calls = 0

    def ask(self, messages: list, system: str, *, iteration: int, session_id: str):
        self.calls += 1
        return self.client.messages.create(
            model=MODEL, max_tokens=4000, system=system, tools=TOOLS, messages=messages,
            extra_headers={"x-agenticledger-iteration": str(iteration),
                           "x-agenticledger-session-id": session_id},
        )

    def label(self, name: str, icon: str, color: str) -> None:
        try:
            httpx.put(f"{LEDGER}/api/labels/run/{self.run_id}",
                      json={"name": name, "icon": icon, "color": color, "project": PROJECT},
                      headers={k: v for k, v in self._headers.items() if "api-key" in k},
                      timeout=5)
        except Exception as e:
            print(f"(could not label the run: {e})", file=sys.stderr)

    def end(self) -> None:
        try:
            httpx.post(f"{LEDGER}/api/runs/{self.run_id}/end",
                       headers={k: v for k, v in self._headers.items() if "api-key" in k},
                       timeout=5)
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
            log(f"    {u.name}({json.dumps(u.input or {})[:60]}) -> {out.splitlines()[0][:70] if out else ''}")
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": out})
        messages.append({"role": "user", "content": results})


def report(rec: Recorder, ws: Path, started: float, *, iterations: int, why: str) -> None:
    ok, _ = run_tests(ws)
    print()
    print(f"{rec.agent}: {'tests pass' if ok else 'tests still failing'} | "
          f"{iterations} iteration(s) | {rec.calls} model calls | "
          f"{time.time() - started:.0f}s | stopped: {why}")
    print(f"open: {LEDGER}/app#/runs/{rec.run_id}")
    rec.end()
