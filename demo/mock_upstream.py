"""A puppet upstream for the stage demo — answers instantly, costs nothing.

The driver (drive.py) writes the script: each request may carry two extra
fields the proxy forwards untouched and real providers would ignore:

    demo_reply: {"type": "text", "text": "..."}
             or {"type": "tool", "name": "...", "args": {...}}
    demo_usage: [prompt_tokens, completion_tokens]

The puppet echoes them back in OpenAI response shape, so every number the
audience sees on the dashboard is computed by the ledger from realistic
wire traffic — nothing on screen is hard-coded.

Run:  python demo/mock_upstream.py   (listens on 127.0.0.1:9911)
"""

import json
import random
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class Puppet(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's spelling
        raw = self.rfile.read(int(self.headers.get("content-length", 0)))
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        reply = body.get("demo_reply") or {"type": "text", "text": "ok"}
        usage = body.get("demo_usage") or [random.randint(300, 900),
                                           random.randint(20, 120)]
        time.sleep(random.uniform(0.05, 0.25))

        message = {"role": "assistant", "content": None}
        finish = "stop"
        if reply.get("type") == "tool":
            message["tool_calls"] = [{
                "id": f"call_{random.randint(1000, 9999)}",
                "type": "function",
                "function": {"name": reply["name"],
                             "arguments": json.dumps(reply.get("args", {}))},
            }]
            finish = "tool_calls"
        else:
            message["content"] = reply.get("text", "ok")

        out = json.dumps({
            "id": "chatcmpl-demo", "object": "chat.completion",
            "model": body.get("model", "gpt-4o"),
            "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": int(usage[0]),
                      "completion_tokens": int(usage[1])},
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):  # quiet stage, quiet terminal
        pass


if __name__ == "__main__":
    print("puppet upstream on http://127.0.0.1:9911 — ctrl-c to stop")
    HTTPServer(("127.0.0.1", 9911), Puppet).serve_forever()
