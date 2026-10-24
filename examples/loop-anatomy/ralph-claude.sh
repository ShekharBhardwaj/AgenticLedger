#!/usr/bin/env bash
# Ralph with Claude Code: a fresh `claude -p` process every iteration, so the
# model starts each round with an empty memory; the files on disk and the
# progress.md notebook are the only things that carry over. Needs a running
# ledger and Claude Code signed in (a subscription is enough; no API key).
#
#   ./ralph-claude.sh [max_iterations]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LEDGER="${AGENTICLEDGER_URL:-http://localhost:8000}"
MODEL="${LOOP_MODEL:-sonnet}"
MAX="${1:-8}"
RUN="ralph-$(date +%m%d-%H%M%S)"
WS="$HERE/runs/$RUN"
rm -rf "$WS" && mkdir -p "$HERE/runs" && cp -R "$HERE/fixture" "$WS" && cd "$WS"

PROMPT="$(cat TASK.md)

How you work: this is a loop that starts you over with an empty memory on
every iteration. progress.md is your notebook. Read it first, run the
tests with \`python3 -m unittest -q test_textkit\`, fix ONE thing, then
write to progress.md what you did and what is still failing, and stop. If
every test passes, reply with exactly <promise>DONE</promise> and nothing
else. Do not change the tests."

TOOLS="Read,Write,Edit,Glob,Grep,Bash(python3 -m unittest*),Bash(python -m unittest*)"
started=$(date +%s)
why="max iterations"
for i in $(seq 1 "$MAX"); do
  echo "iteration $i: fresh conversation"
  out="$(agenticledger run "$RUN" --project loop-anatomy --proxy "$LEDGER" -- \
        claude -p "$PROMPT" --model "$MODEL" --allowedTools "$TOOLS" 2>&1 | tee /dev/stderr)" || true
  if [ "$i" = 1 ]; then
    curl -s -X PUT "$LEDGER/api/labels/run/$RUN" -H 'content-type: application/json' \
      -d '{"name":"Ralph","icon":"wrench","color":"orange","project":"loop-anatomy"}' >/dev/null || true
  fi
  if python3 -m unittest -q test_textkit >/dev/null 2>&1; then why="tests pass"; break; fi
  case "$out" in *"<promise>DONE</promise>"*) why="completion promise, but tests still fail"; break;; esac
done
if python3 -m unittest -q test_textkit >/dev/null 2>&1; then verdict="tests pass"; else verdict="tests still failing"; fi
echo
echo "ralph: $verdict | $i iteration(s) | $(( $(date +%s) - started ))s | stopped: $why"
echo "open: $LEDGER/app#/runs/$RUN"
