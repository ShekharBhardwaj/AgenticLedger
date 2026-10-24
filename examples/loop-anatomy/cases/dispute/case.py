"""The dispute case: decide provisional credit on a disputed card
transaction from a folder of documents. check.py is the judge of done."""

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "docs"
NOTEBOOK = "notes.md"
TASK = (FIXTURE / "TASK.md").read_text()

RALPH_RULE = f"""
How you work: this is a loop that starts you over with an empty memory on
every iteration. {NOTEBOOK} is your notebook. Read it first, take the next
step the notebook says is needed (at most one or two documents), then
write to {NOTEBOOK} every fact you learned that the memo will need and
what to do next, and stop. When decision.md is written and complete,
reply with exactly {{PROMISE}} and nothing else.
"""

REACT_RULE = """
How you work: one continuous conversation. Think, call a tool, read the
result, and repeat until decision.md is written and complete. Then reply
with exactly {PROMISE} and nothing else.
"""

TOOLS = [
    {"name": "list_docs", "description": "List the documents in the folder.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "search", "description": "Find a word or phrase across every document; returns matching lines with the document name.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "read_doc", "description": "Read one document in full.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "write_file", "description": "Write one file in the folder, replacing it (for notes.md and decision.md).",
     "input_schema": {"type": "object",
                      "properties": {"name": {"type": "string"}, "content": {"type": "string"}},
                      "required": ["name", "content"]}},
]


def make_workspace(ws: Path) -> None:
    if ws.exists():
        shutil.rmtree(ws)
    shutil.copytree(FIXTURE, ws)
    (ws / NOTEBOOK).write_text("")


def _inside(ws: Path, rel: str) -> Path:
    p = (ws / rel).resolve()
    if ws.resolve() not in p.parents and p != ws.resolve():
        raise ValueError(f"{rel} is outside the folder")
    return p


def run_tool(ws: Path, name: str, args: dict) -> str:
    try:
        if name == "list_docs":
            return "\n".join(sorted(p.name for p in ws.iterdir() if p.is_file()))
        if name == "search":
            q = str(args.get("query", "")).lower()
            hits = []
            for p in sorted(ws.iterdir()):
                if not p.is_file():
                    continue
                for n, line in enumerate(p.read_text().splitlines(), start=1):
                    if q and q in line.lower():
                        hits.append(f"{p.name}:{n}: {line.strip()}")
            return "\n".join(hits) if hits else f"no document mentions {args.get('query')!r}"
        if name == "read_doc":
            return _inside(ws, args["name"]).read_text()
        if name == "write_file":
            _inside(ws, args["name"]).write_text(args["content"])
            return f"wrote {args['name']} ({len(args['content'])} chars)"
        return f"unknown tool {name}"
    except FileNotFoundError:
        return f"error: no document named {args.get('name')!r}"
    except Exception as e:
        return f"error: {e}"


def judge(ws: Path) -> tuple[bool, str]:
    proc = subprocess.run([sys.executable, str(HERE / "check.py"), str(ws / "decision.md")],
                          capture_output=True, text=True, timeout=30)
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out.splitlines()[0] if out else "no output from check.py"
