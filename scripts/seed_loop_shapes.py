"""Seed a dev ledger with two SYNTHETIC runs shaped like a Ralph loop and a
ReAct loop, for working on the loop-anatomy stage without spending money.

    python scripts/seed_loop_shapes.py /path/to/shapes.db

The model is scripted; the rest is the real demo. Each run works in a
private copy of examples/loop-anatomy/fixture through the demo's own tools
(the test output, file contents and write receipts are what the tools
really returned), with the demo's system prompt, prompt and tool list, and
every stored call carries the whole conversation the way the proxy records
it: the context grows by one assistant turn and one tool result per call.
The numbers are made up to look like the real thing (a fresh conversation
per iteration with a flat context for Ralph; one conversation whose context
climbs for ReAct) and the model's words were written by hand. Never
present these as recorded runs. The real pair is recorded with
examples/loop-anatomy/. Rewrites the file each time.
"""

import asyncio
import random
import shutil
import string
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "examples" / "loop-anatomy"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DEMO))

import common as demo  # noqa: E402  the demo's tools, workspace and promise
import ralph  # noqa: E402  the Ralph script's SYSTEM and PROMPT
import react  # noqa: E402  the ReAct script's SYSTEM and PROMPT

from agenticledger.proxy.normalize import CanonicalRequest, CanonicalResponse  # noqa: E402
from agenticledger.proxy.providers.base import extract_tool_results  # noqa: E402
from agenticledger.proxy.store import Store  # noqa: E402

MODEL = "claude-sonnet-5-5"
IN_PER_M, OUT_PER_M, CACHE_PER_M = 3.0, 15.0, 0.30
RALPH, REACT = "ralph-1016-0912", "react-1016-0915"
DONE = demo.PROMISE
NOTE = "Fixed one thing; notebook updated."

# The bugs in fixture/textkit.py, as (the line that is there, the line the
# scripted model writes instead). A fix replaces the whole file, like the
# real model's write_file does. Ralph takes slugify in two steps: its first
# fix strips spaces but misses the "!" that still leaves an empty word, and
# the next iteration's test run catches it. ReAct, with the tests in its
# context, fixes slugify in one go.
FIXES = {
    "slugify_lower_strip": (
        'words = re.split(r"[^A-Za-z0-9]+", text)',
        'words = re.split(r"[^A-Za-z0-9]+", text.strip().lower())'),
    "slugify_drop_empty": (
        'return "-".join(words)',
        'return "-".join(w for w in words if w)'),
    "slugify": (
        '    words = re.split(r"[^A-Za-z0-9]+", text)\n    return "-".join(words)\n',
        '    words = re.split(r"[^A-Za-z0-9]+", text.lower())\n'
        '    return "-".join(w for w in words if w)\n'),
    "word_count": ('return len(text.split(" "))', 'return len(text.split())'),
    "truncate": ('return text[:limit] + "..."', 'return text[:limit - 3] + "..."'),
    "parse_duration": (
        '        elif unit == "s":\n',
        '        elif unit == "m":\n            total += int(amount) * 60\n        elif unit == "s":\n'),
}


@dataclass
class Turn:
    """One model response: what it says, then what it does."""
    text: Optional[str]                  # the model's words before acting; None when it only acts
    tool: Optional[str] = None           # the tool it calls; None ends the conversation
    args: Optional[dict] = None          # the call's input (read_file, list_files, run_tests)
    fix: Optional[str] = None            # write_file textkit.py with this FIXES entry applied
    notebook: Optional[str] = None       # write_file progress.md with this section added
    failures: Optional[int] = None       # run_tests: failures the output must report; 0 = all pass


def read(text, path):
    return Turn(text, "read_file", {"path": path})


def tests(text, failures):
    return Turn(text, "run_tests", {}, failures=failures)


def fix(text, name):
    return Turn(text, "write_file", fix=name)


def note(text, section):
    return Turn(text, "write_file", notebook=section)


def say(text):
    return Turn(text)


def ralph_iterations() -> list[list[Turn]]:
    """Six fresh conversations. Each reads the notebook, runs the tests,
    reads the module, fixes one thing, writes the notebook and stops; the
    last one only verifies. The notebook is the only memory between them."""
    return [
        [
            read("Reading the notebook first, as instructed.", "progress.md"),
            tests("The notebook is empty, so this is the first iteration. Running the tests to see what fails.", 8),
            read("Eight failures and the output is cut at the front, but slugify is the first I can read whole: "
                 "the result is not lowercased and the edge words are empty. I will fix slugify first and need "
                 "to see the code.", "textkit.py"),
            fix("slugify splits on anything that is not a letter or digit without lowercasing, and the spaces "
                "around the input leave empty words at both ends. Lowercasing and stripping the input should "
                "cover all three slugify failures.", "slugify_lower_strip"),
            note("Writing the notebook so the next iteration knows what changed and what is still failing.",
                 "## Iteration 1\n"
                 "Tests: 8 failures. The output is cut at the front; the hidden part probably covers "
                 "parse_duration, the one function with no visible failure.\n"
                 "Fixed: slugify now lowercases and strips the input before splitting. Not verified by a "
                 "test run yet.\n"
                 "Still failing: truncate test_result_fits_the_limit (13 chars for limit 10), word_count "
                 "test_any_whitespace (5 != 4) and test_empty (1 != 0).\n"
                 "Next: run the tests, confirm slugify is green, then take word_count.\n"),
            say(NOTE),
        ],
        [
            read("Reading the notebook to see what the last iteration did.", "progress.md"),
            tests("The notebook says slugify was fixed but never tested. Running the tests.", 6),
            read("Down to six. slugify still fails one test: 'hello-world-' has a trailing dash, so stripping "
                 "the spaces was not enough and the '!' still leaves an empty word after the split. Let me "
                 "look at the code again.", "textkit.py"),
            fix("Dropping empty words after the split handles any punctuation at the edges.",
                "slugify_drop_empty"),
            note("Notebook update for the next iteration.",
                 "## Iteration 2\n"
                 "Tests: 6 failures. slugify still failed test_trims_punctuation_at_the_edges "
                 "('hello-world-'): the '!' leaves an empty word, strip only removed the spaces. "
                 "parse_duration is failing too (test_all_parts 3615 != 5415, test_minutes_only 0 != 2700): "
                 "the minutes are ignored.\n"
                 "Fixed: slugify now drops empty words after the split. Not verified yet.\n"
                 "Still failing: parse_duration (2), truncate (1), word_count (2).\n"
                 "Next: word_count.\n"),
            say(NOTE),
        ],
        [
            read("Notebook first.", "progress.md"),
            tests("Two slugify fixes so far, neither verified in the same iteration. Running the tests.", 5),
            read("slugify is green now. Five left across parse_duration, truncate and word_count; the notebook "
                 "says word_count is next, so I need the code.", "textkit.py"),
            fix("word_count splits on a single space, so runs of whitespace count extra and the empty string "
                "counts as one word. split() with no argument fixes both.", "word_count"),
            note("Notebook update.",
                 "## Iteration 3\n"
                 "Tests: 5 failures, slugify is green.\n"
                 "Fixed: word_count uses split() with no argument, so any whitespace separates words and the "
                 "empty string gives 0.\n"
                 "Still failing: parse_duration test_all_parts and test_minutes_only (minutes ignored), "
                 "truncate test_result_fits_the_limit (result is limit + 3 chars).\n"
                 "Next: truncate.\n"),
            say(NOTE),
        ],
        [
            read("Reading the notebook.", "progress.md"),
            tests("Running the tests to confirm the word_count fix and see what is left.", 3),
            read("Three left: truncate and the two parse_duration tests. Taking truncate; let me see the code.",
                 "textkit.py"),
            fix("truncate cuts to the limit and then adds three dots, so the result is three characters over. "
                "Cut to limit minus three instead.", "truncate"),
            note("Notebook update.",
                 "## Iteration 4\n"
                 "Tests: 3 failures, word_count is green.\n"
                 "Fixed: truncate cuts to limit - 3 before adding the dots, so the result fits the limit.\n"
                 "Still failing: parse_duration test_all_parts (3615 != 5415) and test_minutes_only "
                 "(0 != 2700). The minutes part is never added.\n"
                 "Next: parse_duration, then everything should pass.\n"),
            say(NOTE),
        ],
        [
            read("Reading the notebook.", "progress.md"),
            tests("Running the tests.", 2),
            read("Only parse_duration is left: 1h30m15s gives 3615, which is the hours and the seconds without "
                 "the minutes. Let me read the code.", "textkit.py"),
            fix("There is no branch for the m unit. Adding one that multiplies by 60.", "parse_duration"),
            note("Notebook update; the next iteration should find everything green.",
                 "## Iteration 5\n"
                 "Tests: 2 failures, truncate is green.\n"
                 "Fixed: parse_duration adds the minutes part (amount * 60).\n"
                 "Still failing: nothing expected. All four functions have been fixed.\n"
                 "Next: run the tests and, if they are all green, reply with the completion promise.\n"),
            say(NOTE),
        ],
        [
            read("Reading the notebook.", "progress.md"),
            tests("The notebook says every bug is fixed and the last fix was not verified. Running the tests.", 0),
            say(DONE),
        ],
    ]


def react_turns() -> list[Turn]:
    """One conversation: look around, read everything, then fix and test,
    fix and test, with one repeated test run the loop guard flags."""
    return [
        Turn("Let me see what is in the folder.", "list_files", {}),
        read("Reading the module under test.", "textkit.py"),
        read("And the tests, so I know what each function should do.", "test_textkit.py"),
        tests("Running the tests to get the real failures before changing anything.", 8),
        fix("Eight failures, and I can see all four causes from the code and the tests. Starting with slugify: "
            "lowercase the input and drop the empty words the split leaves at the edges.", "slugify"),
        tests("Checking that slugify is green and what is left.", 5),
        fix("slugify passes. word_count splits on a single space, so runs of whitespace count extra and the "
            "empty string counts as one word; split() with no argument fixes both.", "word_count"),
        tests("Running the tests again.", 3),
        fix("truncate cuts to the limit and then adds three dots, so the result is three characters over. "
            "Cut to limit minus three instead.", "truncate"),
        tests("Running the tests.", 2),
        tests("Running the tests once more to be sure the two remaining failures are both in parse_duration.", 2),
        fix("Same two. parse_duration has no branch for the m unit, so 45m comes out as 0. Adding the minutes "
            "branch.", "parse_duration"),
        tests("Final check.", 0),
        say(DONE),
    ]


_ids = random.Random(11)  # its own stream, so the token numbers below never move


def tool_id() -> str:
    return "toolu_01" + "".join(_ids.choices(string.ascii_letters + string.digits, k=22))


def resolve_args(turn: Turn, ws: Path) -> dict:
    """The tool input for this turn, built from the workspace as it is now."""
    if turn.fix:
        src = (ws / "textkit.py").read_text()
        old, new = FIXES[turn.fix]
        if src.count(old) != 1:
            sys.exit(f"fixture/textkit.py changed: cannot apply the {turn.fix} fix")
        return {"path": "textkit.py", "content": src.replace(old, new)}
    if turn.notebook:
        cur = (ws / "progress.md").read_text().replace("(nothing yet)\n", "").rstrip("\n")
        return {"path": "progress.md", "content": f"{cur}\n\n{turn.notebook}"}
    return turn.args or {}


def check_tests(turn: Turn, out: str) -> None:
    """The hand-written story must match what the tests really said."""
    if turn.failures == 0 and not out.startswith("ALL TESTS PASS"):
        sys.exit(f"expected all tests to pass, got:\n{out}")
    if turn.failures and f"FAILED (failures={turn.failures})" not in out:
        sys.exit(f"expected {turn.failures} failures, got:\n{out}")


class Conversation:
    """One session over a real workspace. Every call stores the messages
    as the model saw them; then the model's turn and the tool's answer are
    appended, exactly as converse() in the demo builds them."""

    def __init__(self, store, ws: Path, run_id: str, session: str, system: str, prompt: str):
        self.store, self.ws, self.run_id, self.session, self.system = store, ws, run_id, session, system
        # The proxy keeps the system prompt in the messages too, ahead of the prompt.
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]

    async def step(self, n: int, turn: Turn, *, iteration: int, ts: float, tokens_in: int, tokens_out: int,
                   latency_ms: float, cache_read: int = 0, flags: Optional[str] = None) -> None:
        blocks: list[dict] = []
        tool_calls = None
        args = None
        if turn.text:
            blocks.append({"type": "text", "text": turn.text})
        if turn.tool:
            args = resolve_args(turn, self.ws)
            tid = tool_id()
            blocks.append({"type": "tool_use", "id": tid, "name": turn.tool, "input": args})
            tool_calls = [{"id": tid, "name": turn.tool, "arguments": args}]
        cost = tokens_in * IN_PER_M / 1e6 + tokens_out * OUT_PER_M / 1e6 + cache_read * CACHE_PER_M / 1e6
        await self.store.save(
            f"{n:032x}",
            CanonicalRequest(
                messages=list(self.messages), model_id=MODEL, provider="anthropic", timestamp=ts,
                system_prompt=self.system, tools=demo.TOOLS, max_tokens=4000,
                tool_results=extract_tool_results(self.messages),
            ),
            CanonicalResponse(
                content=turn.text, tool_calls=tool_calls,
                stop_reason="tool_use" if turn.tool else "end_turn",
                tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency_ms,
                cost_usd=round(cost, 6), cache_read_tokens=cache_read or None,
            ),
            session_id=self.session, run_id=self.run_id, iteration=iteration,
            framework="loop-anatomy", agent_name=self.run_id.split("-")[0],
            loop_flags=flags,
        )
        self.messages.append({"role": "assistant", "content": blocks})
        if turn.tool:
            out = demo.run_tool(self.ws, turn.tool, args)
            if turn.tool == "run_tests":
                check_tests(turn, out)
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tid, "content": out}]})


async def seed(path: Path) -> None:
    if path.exists():
        path.unlink()
    store = await Store.connect(f"sqlite:///{path}")
    rng = random.Random(7)
    n = 0
    now = time.time()

    # Ralph: six fresh conversations over one workspace. Flat context.
    t = now - 1500
    ws = demo.make_workspace(RALPH)
    for it, turns in enumerate(ralph_iterations(), start=1):
        conv = Conversation(store, ws, RALPH, f"{RALPH}-i{it}", ralph.SYSTEM, ralph.PROMPT)
        ctx = 2600
        for turn in turns:
            n += 1
            t += 6 + rng.random() * 8
            ctx += rng.randint(300, 900)
            tokens_out = rng.randint(60, 420)
            await conv.step(n, turn, iteration=it, ts=t, tokens_in=ctx, tokens_out=tokens_out,
                            latency_ms=900 + rng.random() * 2200)
        t += 4
    await store.set_label("run", RALPH, name="Ralph", icon="wrench", color="orange", project="loop-anatomy")

    # ReAct: one conversation, context climbing step after step, with cache reads
    # once the prefix is stable and one repeat flagged by the loop guard.
    t = now - 1400
    ws = demo.make_workspace(REACT)
    conv = Conversation(store, ws, REACT, REACT, react.SYSTEM, react.PROMPT)
    ctx, cache = 2600, 0
    for i, turn in enumerate(react_turns(), start=1):
        n += 1
        t += 5 + rng.random() * 9
        grow = rng.randint(900, 2600) if turn.tool in ("read_file", "run_tests") else rng.randint(300, 700)
        ctx += grow
        cache = int(ctx * 0.8) if i > 2 else 0
        tokens_out = rng.randint(80, 520)
        await conv.step(n, turn, iteration=i, ts=t, tokens_in=ctx - cache, tokens_out=tokens_out,
                        cache_read=cache, flags='["repeat_tool_call"]' if i == 11 else None,
                        latency_ms=900 + rng.random() * 2200)
    await store.set_label("run", REACT, name="ReAct", icon="brain", color="blue", project="loop-anatomy")
    await store.close()
    print(f"seeded {n} synthetic calls into {path}")


def cleanup() -> None:
    """The workspaces live where the demo keeps its own (gitignored); the
    seed leaves nothing behind."""
    runs = DEMO / "runs"
    for run_id in (RALPH, REACT):
        shutil.rmtree(runs / run_id, ignore_errors=True)
    if runs.is_dir() and not any(runs.iterdir()):
        runs.rmdir()


if __name__ == "__main__":
    try:
        asyncio.run(seed(Path(sys.argv[1])))
    finally:
        cleanup()
