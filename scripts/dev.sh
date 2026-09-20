#!/usr/bin/env bash
# Dev server launcher. Ports are FIXED, do not change:
#   backend 8003, frontend 5177
# — 5173/5175/8000/8001/8002/23456 are already used by other projects on this machine
# (my-academy/btc-daily-web/my-news/exchange-fee/my-youtube).
#
# Usage: bash scripts/dev.sh [backend|frontend]   (no arg = both)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_backend() {
  cd "$repo_root/backend"
  source .venv/bin/activate
  exec uvicorn app.main:app --port 8004 --reload
}

run_frontend() {
  cd "$repo_root/frontend"
  # --host 를 빼면 [::1] 에만 붙어서 테일스케일 주소로는 안 열린다.
  # MagicDNS 이름으로 붙을 때 필요한 allowedHosts 는 vite.config.ts 에 있다.
  exec npx vite --port 5177 --strictPort --host 0.0.0.0
}

case "${1:-both}" in
  backend)
    run_backend
    ;;
  frontend)
    run_frontend
    ;;
  both)
    ( run_backend ) &
    backend_pid=$!
    ( run_frontend ) &
    frontend_pid=$!
    trap 'kill "$backend_pid" "$frontend_pid" 2>/dev/null' EXIT
    wait
    ;;
  *)
    echo "usage: bash scripts/dev.sh [backend|frontend]" >&2
    exit 1
    ;;
esac
