#!/usr/bin/env bash
# Launch the backend (FastAPI) and frontend (Vite) together with one command.
# Ctrl+C stops both. Run from anywhere: ./dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

cd "$ROOT"

# --- preflight ---------------------------------------------------------------
if [[ ! -x "backend/.venv/bin/uvicorn" ]]; then
  echo "✗ backend venv not found. Create it first:"
  echo "    cd backend && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi
if [[ ! -d "frontend/node_modules" ]]; then
  echo "✗ frontend deps not installed. Run: (cd frontend && npm install)"
  exit 1
fi

# Free the backend port if a stale server is still holding it.
if lsof -ti ":$BACKEND_PORT" >/dev/null 2>&1; then
  echo "• Port $BACKEND_PORT in use — stopping the stale process."
  lsof -ti ":$BACKEND_PORT" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

# --- launch ------------------------------------------------------------------
# Enable job control so each background job becomes its own process group leader
# (PGID == PID). That lets us kill the *whole* group on shutdown — important
# because `npm run dev` spawns Vite as a grandchild that a plain kill would orphan.
set -m

pids=()
cleanup() {
  echo ""
  echo "• Shutting down…"
  for pid in "${pids[@]}"; do
    kill -TERM -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "▶ Backend  → http://localhost:$BACKEND_PORT"
( cd backend && exec .venv/bin/uvicorn app.main:app --port "$BACKEND_PORT" --reload ) &
pids+=($!)

echo "▶ Frontend → http://localhost:$FRONTEND_PORT"
( cd frontend && exec npm run dev -- --port "$FRONTEND_PORT" ) &
pids+=($!)

echo ""
echo "Open http://localhost:$FRONTEND_PORT  (Ctrl+C to stop both)"

# Exit as soon as either process dies, so a crash doesn't leave a half-running app.
# (Portable to macOS's bash 3.2, which lacks `wait -n`.)
while true; do
  for pid in "${pids[@]}"; do
    kill -0 "$pid" 2>/dev/null || exit 0
  done
  sleep 1
done
