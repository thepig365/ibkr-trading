#!/usr/bin/env bash
# IBKR Trading Engine — start backend + frontend (service launcher only; no trades).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

# Homebrew / Node: non-interactive shells often lack brew's PATH
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH:-}"
if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv zsh)" 2>/dev/null || true
fi

RUNTIME="$ROOT/.runtime"
VENV_UVICORN="$ROOT/.venv/bin/uvicorn"
ENV_FILE="$ROOT/.env"
BACK_PID_FILE="$RUNTIME/backend.pid"
FRONT_PID_FILE="$RUNTIME/frontend.pid"
BACK_LOG="$ROOT/logs/backend.log"
FRONT_LOG="$ROOT/logs/frontend.log"

log() {
  printf '%s\n' "$@"
}

fatal() {
  log "ERROR: $*" >&2
  exit 1
}

confirm_tws_prompt() {
  log ""
  log "TWS Paper is not detected. Open TWS or IB Gateway, log into Paper Trading,"
  log "enable API, port 7497, then press Enter to continue or Ctrl+C to abort."
  read -r _
}

check_port_listen() {
  local host="$1" port="$2"
  if command -v nc >/dev/null 2>&1; then
    nc -z -w 2 "$host" "$port" >/dev/null 2>&1
    return $?
  fi
  if command -v bash >/dev/null 2>&1; then
    (echo >/dev/tcp/"$host"/"$port") >/dev/null 2>&1 && return 0
    return 1
  fi
  return 1
}

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  return 1
}

poll_http() {
  local url="$1" label="$2" max_secs="${3:-90}"
  local i=0
  log "Waiting for $label ..."
  while (( i < max_secs * 2 )); do
    if curl -sf --connect-timeout 1 "$url" >/dev/null 2>&1; then
      log "$label responded."
      return 0
    fi
    sleep 0.5
    ((++i))
  done
  log "WARNING: $label did not respond within ${max_secs}s (check logs)."
  return 1
}

# ----- Virtualenv -----
[[ -x "$VENV_UVICORN" ]] || fatal ".venv missing or incomplete. Run from project root:
  cd \"$ROOT\" && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
  cp .env.example .env && cp config.example.yaml config.yaml
  Edit .env and config.yaml locally (never commit secrets)."

# ----- .env -----
[[ -f "$ENV_FILE" ]] || fatal "Missing $ENV_FILE — copy .env.example to .env and fill in values."

if ! grep -Eq '^IBKR_ACCOUNT=.+' "$ENV_FILE"; then
  fatal ".env must set IBKR_ACCOUNT=your_paper_id (non-empty value after '=')."
fi

# ----- npm -----
if ! command -v npm >/dev/null 2>&1; then
  log "npm not found. Install Node with Homebrew or run: eval \"\$(/opt/homebrew/bin/brew shellenv zsh)\"" >&2
  fatal "'npm' not found in PATH (${PATH})"
fi

# ----- node_modules -----
if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  log "frontend/node_modules is missing."
  read -r -p "Run npm install in frontend now? [y/N] " ans
  case "${ans:-}" in
    y|Y|yes|YES)
      ( cd "$ROOT/frontend" && npm install ) || fatal "npm install failed."
      ;;
    *)
      fatal "Aborted (install deps: cd \"$ROOT/frontend\" && npm install)."
      ;;
  esac
fi

# ----- TWS Paper port -----
if ! check_port_listen 127.0.0.1 7497; then
  confirm_tws_prompt
fi

# ----- Collision -----
if [[ -f "$BACK_PID_FILE" ]] || [[ -f "$FRONT_PID_FILE" ]]; then
  fatal "PID files already present under .runtime/. Stop first: \"$ROOT/scripts/stop_engine.sh\""
fi
port_in_use 8000 && fatal "Port 8000 is already in use. Stop other services or run scripts/stop_engine.sh"
port_in_use 3000 && fatal "Port 3000 is already in use. Stop other services or run scripts/stop_engine.sh"

mkdir -p "$RUNTIME" "$ROOT/logs"

LOG_STAMP="===== $(date '+%Y-%m-%d %H:%M:%S %z') start_engine session ====="
{
  printf '\n%s\n\n' "$LOG_STAMP"
} >>"$BACK_LOG"
{
  printf '\n%s\n\n' "$LOG_STAMP"
} >>"$FRONT_LOG"

# Backend (cwd ROOT so backend/config.py finds config.yaml and loads .env)
log "Starting backend on :8000 ..."
(
  cd "$ROOT"
  nohup "$VENV_UVICORN" backend.main:app --host 127.0.0.1 --port 8000 >>"$BACK_LOG" 2>&1 &
  echo $! >"$BACK_PID_FILE"
)

sleep 1
[[ -s "$BACK_PID_FILE" ]] || fatal "Failed to capture backend PID."
BACK_PID="$(tr -d '[:cntrl:] ' <"$BACK_PID_FILE")"
[[ -n "$BACK_PID" ]] || fatal "Backend PID missing."
poll_http "http://127.0.0.1:8000/api/connection-status" "Backend API" 90 || true

# Frontend
log "Starting frontend on :3000 ..."
(
  cd "$ROOT/frontend"
  nohup npm run dev >>"$FRONT_LOG" 2>&1 &
  echo $! >"$FRONT_PID_FILE"
)

sleep 1
[[ -s "$FRONT_PID_FILE" ]] || fatal "Failed to capture frontend PID."
if ! poll_http "http://127.0.0.1:3000/" "Frontend" 120; then
  log "Frontend did not respond — last 80 lines of $FRONT_LOG:" >&2
  tail -n 80 "$FRONT_LOG" >&2 || true
fi

if [[ "$(uname -s)" == "Darwin" ]]; then
  open "http://127.0.0.1:3000" >/dev/null 2>&1 || true
fi

log ""
log "=== IBKR Trading Engine (local) ==="
log "Dashboard:          http://127.0.0.1:3000"
log "Backend health:       http://127.0.0.1:8000/api/health"
log "Connection status:    http://127.0.0.1:8000/api/connection-status"
log "Logs:"
log "  $BACK_LOG"
log "  $FRONT_LOG"
log "Stop:"
log "  $ROOT/scripts/stop_engine.sh"
log ""
