"""The textkit case: a small module with planted bugs and a failing test
suite. The tests are the judge of done."""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent.parent / "fixture"
NOTEBOOK = "progress.md"
TASK = (FIXTURE / "TASK.md").read_text()

RALPH_RULE = f"""
How you work: this is a loop that starts you over with an empty memory on
every iteration. {NOTEBOOK} is your notebook. Read it first, run the
tests with the run_tests tool, fix ONE thing, then write to {NOTEBOOK}
what you did and what is still failing, and stop. If every test passes,
reply with exactly {{PROMISE}} and nothing else.
"""

REACT_RULE = """
How you work: one continuous conversation. Think, call a tool, read the
result, and repeat until every test passes (run them with the run_tests
tool). Then reply with exactly {PROMISE} and nothing else.
"""

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


def make_workspace(ws: Path) -> None:
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(FIXTURE, ws)


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


def judge(ws: Path) -> tuple[bool, str]:
    ok, _ = run_tests(ws)
    return ok, "tests pass" if ok else "tests still failing"
