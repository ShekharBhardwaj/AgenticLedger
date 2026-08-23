"""The stage driver for the four-minute demo — one act per beat.

Every act sends REAL wire traffic through the ledger proxy to the puppet
upstream (mock_upstream.py); everything the audience sees — costs,
iterations, threads, flags, refusals — is the ledger doing its actual
job on that traffic.

    python3 demo/drive.py ralph    # act: a Ralph loop, 4 fresh-context iterations
    python3 demo/drive.py react    # act: a ReAct agent that gets itself stuck
    python3 demo/drive.py live     # act: a paced overnight loop for the live feed
                                  #      (keeps knocking ~2 min; set a ceiling
                                  #       mid-flight and watch the wall)

Needs: the demo proxy on :8003 pointed at the puppet on :9911 — see
demo/README.md for the two commands that start them.
"""

import json
import sys
import time
import urllib.request

PROXY = "http://localhost:8003"


def call(path, messages, session, reply, usage, extra_headers=None):
    body = {
        "model": "gpt-4o",
        "messages": messages,
        "demo_reply": reply,
        "demo_usage": usage,
    }
    req = urllib.request.Request(
        f"{PROXY}{path}",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            "authorization": "Bearer sk-demo",
            "x-agenticledger-session-id": session,
            **(extra_headers or {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


SYSTEM = "You are the overnight refactor loop. Finish the task, then say COMPLETE."


def act_ralph():
    """Four fresh-context iterations: same prompt, new session each time —
    the Ralph pattern the ledger groups by system prompt."""
    costs = [[5200, 640], [4100, 210], [6800, 900], [3900, 150]]
    for i, usage in enumerate(costs, start=1):
        status, _ = call(
            f"/r/ralph-style/{i}/v1/chat/completions",
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": f"iteration {i}: continue the refactor"}],
            session=f"ralph-{i}",
            reply={"type": "text", "text": f"iteration {i} done"},
            usage=usage,
        )
        print(f"ralph iteration {i}: {status}")
        time.sleep(0.6)


def act_react():
    """One session, one growing conversation: tool steps stitched into a
    thread — and then the agent re-runs the same failing test three times,
    which is exactly what the stuck-loop flag exists for."""
    session = "react-agent"
    path = "/r/react-style/1/v1/chat/completions"
    messages = [
        {"role": "system", "content": "You are a coding agent. Use tools."},
        {"role": "user", "content": "Fix the failing auth test."},
    ]
    steps = [
        {"type": "tool", "name": "read_file", "args": {"path": "auth/test_login.py"}},
        {"type": "tool", "name": "edit_file", "args": {"path": "auth/login.py"}},
        {"type": "tool", "name": "run_tests", "args": {"target": "auth"}},
        {"type": "tool", "name": "run_tests", "args": {"target": "auth"}},
        {"type": "tool", "name": "run_tests", "args": {"target": "auth"}},
        {"type": "text", "text": "The auth test passes now."},
    ]
    for n, reply in enumerate(steps, start=1):
        status, resp = call(path, messages, session, reply,
                            usage=[1500 + 400 * n, 90])
        print(f"react step {n}: {status}")
        msg = resp["choices"][0]["message"]
        messages.append({k: v for k, v in msg.items() if v is not None})
        for tc in msg.get("tool_calls") or []:
            messages.append({
                "role": "tool", "tool_call_id": tc["id"],
                "content": "1 failed" if "run_tests" in tc["function"]["name"] else "ok",
            })
        time.sleep(0.8)


def act_live(minutes: float = 2.0):
    """A paced loop under the run id night-shift: one iteration every few
    seconds so the audience watches calls land in the live feed. Set a
    cost ceiling on the run mid-flight; the knocks keep coming and the
    refusals land amber, costing nothing."""
    deadline = time.time() + minutes * 60
    i = 0
    while time.time() < deadline:
        i += 1
        status, body = call(
            f"/r/night-shift/{i}/v1/chat/completions",
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": f"iteration {i}"}],
            session=f"night-{i}",
            reply={"type": "text", "text": f"iteration {i} done"},
            usage=[5200, 640],
        )
        note = ""
        if status != 200:
            note = f"  <- {body.get('error', {}).get('type', 'refused')}"
        print(f"night-shift iteration {i}: {status}{note}")
        time.sleep(3)


if __name__ == "__main__":
    act = sys.argv[1] if len(sys.argv) > 1 else ""
    if act == "ralph":
        act_ralph()
    elif act == "react":
        act_react()
    elif act == "live":
        act_live(float(sys.argv[2]) if len(sys.argv) > 2 else 2.0)
    else:
        print(__doc__)
        raise SystemExit(2)
