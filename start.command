#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
UVICORN="$ROOT_DIR/.venv/bin/uvicorn"
LOG_DIR="${TMPDIR:-/tmp}/knowledge-nexus"
BACKEND_PID=""

mkdir -p "$LOG_DIR"

if [[ ! -x "$UVICORN" ]]; then
  echo "Python dependencies are missing. Follow the setup instructions in README.md."
  exit 1
fi

if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "Knowledge Nexus is already running."
  open http://localhost:8000/login
  exit 0
fi

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 8000 is in use by another process. Stop it, then run start.command again."
  exit 1
fi

cleanup() {
  trap - EXIT INT TERM
  [[ -z "$BACKEND_PID" ]] || kill "$BACKEND_PID" 2>/dev/null || true
  [[ -z "$BACKEND_PID" ]] || wait "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(
  cd "$BACKEND_DIR"
  PYTHONPATH="$BACKEND_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$UVICORN" app.main:app --host 127.0.0.1 --port 8000
) >"$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

for _ in {1..60}; do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    echo "Knowledge Nexus failed to start. Log: $LOG_DIR/backend.log"
    exit 1
  fi
  sleep 1
done

if ! curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
  echo "Knowledge Nexus did not become ready. Log: $LOG_DIR/backend.log"
  exit 1
fi

echo "Knowledge Nexus is running at http://localhost:8000"
echo "Keep this window open while you use the app. Press Ctrl+C to stop the server."
echo "Logs: $LOG_DIR"
open http://localhost:8000/login
wait "$BACKEND_PID"
