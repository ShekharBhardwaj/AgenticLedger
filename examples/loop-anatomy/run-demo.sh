#!/usr/bin/env bash
# The dispute case, both loops, on the scripted model, recorded live by a
# named ledger instance so the dashboard shows the calls arriving as they
# happen. Costs nothing.
#
#   ./run-demo.sh            start the scripted model and the demo ledger, run Ralph then ReAct
#   ./run-demo.sh --stop     stop the demo ledger and the scripted model
#   LOOP_DELAY=10 ./run-demo.sh   seconds per model call (default 8)
#   DEMO_PORT=8020 ./run-demo.sh  the demo ledger's port (default 8020)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-$HERE/../../.venv/bin/python}"
DEMO_PORT="${DEMO_PORT:-8020}"
MODEL_PORT="${MODEL_PORT:-9911}"
mkdir -p "$HERE/runs"
PIDFILE="$HERE/runs/scripted-model.pid"

if [ "${1:-}" = "--stop" ]; then
  agenticledger stop --name demo || true
  if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; fi
  echo "stopped the demo ledger and the scripted model"
  exit 0
fi

# The scripted model, where the real API would be. Restarted every time so
# the pace (LOOP_DELAY) you asked for is the pace you get. Fully detached,
# or the server would hold this script's output open.
if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; fi
for pid in $(lsof -ti "tcp:$MODEL_PORT" -sTCP:LISTEN 2>/dev/null); do kill "$pid" 2>/dev/null || true; done
sleep 0.5
(cd "$HERE/cases/dispute" && LOOP_DELAY="${LOOP_DELAY:-8}" nohup "$PY" scripted_model.py "$MODEL_PORT" > "$HERE/runs/scripted-model.log" 2>&1 < /dev/null & echo $! > "$PIDFILE") > /dev/null 2>&1 < /dev/null
sleep 1
if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null || ! lsof -ti "tcp:$MODEL_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "the scripted model did not start; see $HERE/runs/scripted-model.log" >&2
  tail -5 "$HERE/runs/scripted-model.log" >&2
  exit 1
fi

# A named ledger instance whose upstream is the scripted model: its own port
# and database; your everyday ledger is not touched. Started from this
# folder, which has no project config file, so the home configuration applies.
# The runs are recorded under the model id gpt-6-astra, which the ledger
# prices at GPT-6 Astra's standard rates (10 in, 50 out, 1 cache read, 12.50
# cache write, USD per million tokens). Restarted each run so settings apply.
cd "$HERE"
agenticledger stop --name demo >/dev/null 2>&1 || true
AGENTICLEDGER_UPSTREAM_URL="http://127.0.0.1:$MODEL_PORT" \
  agenticledger start --name demo --port "$DEMO_PORT" >/dev/null 2>&1 || true
sleep 2
export AGENTICLEDGER_URL="http://localhost:$DEMO_PORT" LOOP_MODEL="${LOOP_MODEL:-gpt-6-astra}" LOOP_SCRIPTED=1 ANTHROPIC_API_KEY=scripted LOOP_CASE=dispute
echo "demo ledger: http://localhost:$DEMO_PORT/app   scripted model: ${LOOP_DELAY:-8}s per call"
# Both loops at once, so both lanes move live and the race is visible.
"$PY" ralph.py --case dispute --max-iterations 15 > "$HERE/runs/ralph.out" 2>&1 &
RALPH_PID=$!
"$PY" react.py --case dispute --max-calls 60 > "$HERE/runs/react.out" 2>&1 &
REACT_PID=$!
echo "running Ralph and ReAct side by side; watch the dashboard"
wait "$REACT_PID" || true
echo "ReAct finished"
wait "$RALPH_PID" || true
echo "Ralph finished"
echo
grep -E "^(ralph|react):|^open:" "$HERE/runs/ralph.out" "$HERE/runs/react.out" | sed 's/^.*\.out://'
echo
echo "compare: open http://localhost:$DEMO_PORT/app, Loop Lens, pick both runs with the compare button"
