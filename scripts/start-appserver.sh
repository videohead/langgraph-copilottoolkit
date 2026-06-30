#!/bin/sh
set -eu

langgraph dev --host 0.0.0.0 --port 8000 &
server_pid=$!

cleanup() {
  kill "$server_pid" 2>/dev/null || true
}

trap cleanup INT TERM

if ! python scripts/startup_smoke.py; then
  echo "[startup-smoke] one or more checks failed; continuing appserver startup"
fi

wait "$server_pid"
