#!/usr/bin/env bash
# ReAct with Claude Code: one `claude -p` process, one conversation. Every
# step the model thinks, calls a tool, reads the result, and the whole
# exchange stays in its context. Needs a running ledger and Claude Code
# signed in (a subscription is enough; no API key).
#
#   ./react-claude.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LEDGER="${AGENTICLEDGER_URL:-http://localhost:8000}"
MODEL="${LOOP_MODEL:-sonnet}"
RUN="react-$(date +%m%d-%H%M%S)"
WS="$HERE/runs/$RUN"
rm -rf "$WS" && mkdir -p "$HERE/runs" && cp -R "$HERE/fixture" "$WS" && cd "$WS"

PROMPT="$(cat TASK.md)

How you work: one continuous conversation. Think, call a tool, read the
result, and repeat until every test passes (run them with
\`python3 -m unittest -q test_textkit\`). Then reply with exactly
<promise>DONE</promise> and nothing else. Do not change the tests."

TOOLS="Read,Write,Edit,Glob,Grep,Bash(python3 -m unittest*),Bash(python -m unittest*)"
started=$(date +%s)
echo "one conversation, step by step"
agenticledger run "$RUN" --project loop-anatomy --proxy "$LEDGER" -- \
  claude -p "$PROMPT" --model "$MODEL" --allowedTools "$TOOLS" || true
curl -s -X PUT "$LEDGER/api/labels/run/$RUN" -H 'content-type: application/json' \
  -d '{"name":"ReAct","icon":"brain","color":"blue","project":"loop-anatomy"}' >/dev/null || true
if python3 -m unittest -q test_textkit >/dev/null 2>&1; then verdict="tests pass"; else verdict="tests still failing"; fi
echo
echo "react: $verdict | $(( $(date +%s) - started ))s"
echo "open: $LEDGER/app#/runs/$RUN"
