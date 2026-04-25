#!/usr/bin/env bash
# Diagnose local Strategy Lab environment. Does not print secret values.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
RUN_PY="${PYTHON:-python3}"

echo "=== strategy_lab_doctor (repo: $ROOT) ==="

pass() { echo "PASS  $*"; }
warn() { echo "WARN  $*"; }
fail() { echo "FAIL  $*"; }

# Python
if command -v "$RUN_PY" >/dev/null 2>&1; then
  pass "python: $RUN_PY ($($RUN_PY -V 2>&1))"
else
  fail "python3 not in PATH"
  exit 1
fi

# venv
if [[ -d "$ROOT/.venv" ]]; then
  pass "directory .venv exists"
else
  warn "no .venv (create: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt)"
fi

# Imports (no secret output)
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "$ROOT/.venv/bin/activate"
fi
if $RUN_PY -c "import fastapi, uvicorn, typer, yaml, pydantic" 2>/dev/null; then
  pass "imports: fastapi, uvicorn, typer, yaml, pydantic"
else
  fail "required packages not importable (pip install -r requirements.txt)"
fi

# .env (never print contents)
if [[ -f "$ROOT/.env" ]]; then
  pass "file .env exists (contents not shown)"
else
  warn "missing .env (copy from .env.example if you need API keys; UI runs without it)"
fi

# Local-only settings overlay (gitignored; never print contents)
if [[ -f "$ROOT/config/settings.local.yaml" ]]; then
  pass "file config/settings.local.yaml exists (local paper overlay; contents not shown)"
else
  warn "config/settings.local.yaml missing (optional: python3 -m bot.cli write-paper-local-config)"
fi

# Git
if [[ -d "$ROOT/.git" ]]; then
  st="$(git -C "$ROOT" status -s 2>/dev/null || true)"
  if [[ -n "${st// }" ]]; then
    warn "git has local changes (sample):"
    echo "$st" | head -8
  else
    pass "git working tree clean"
  fi
else
  warn "not a git repository"
fi

# Runtime paths
_check_path() {
  local label="$1" path="$2"
  if [[ -e "$path" ]]; then
    pass "$label: $path"
  else
    warn "$label missing: $path (optional until you use the feature)"
  fi
}
_check_path "KILL_SWITCH" "$ROOT/data/KILL_SWITCH"
_check_path "mtf runtime flag" "$ROOT/data/runtime/mtf_auto_paper_enabled"
_check_path "intraday runtime flag" "$ROOT/data/runtime/intraday_auto_paper_enabled"

# gitignore
if command -v git >/dev/null 2>&1 && [[ -d "$ROOT/.git" ]]; then
  if git -C "$ROOT" check-ignore -q data/runtime/ 2>/dev/null; then
    pass "git-ignore: data/runtime/ is ignored"
  else
    warn "data/runtime/ not ignored? check .gitignore"
  fi
  if git -C "$ROOT" check-ignore -q logs/ 2>/dev/null; then
    pass "git-ignore: logs/ is ignored"
  else
    warn "logs/ not ignored? check .gitignore"
  fi
fi

# Optional IBKR TCP
IBKR_HOST="${IBKR_HOST:-127.0.0.1}"
IBKR_PORT="${IBKR_PORT:-7497}"
DO_IBKR=0
for a in "$@"; do
  if [[ "$a" == "--check-ibkr" ]]; then DO_IBKR=1; fi
done
if [[ "$DO_IBKR" -eq 1 ]]; then
  if command -v nc >/dev/null 2>&1; then
    if nc -z -w1 "$IBKR_HOST" "$IBKR_PORT" 2>/dev/null; then
      pass "TWS/gateway port reachable $IBKR_HOST:$IBKR_PORT (nc)"
    else
      warn "cannot connect to $IBKR_HOST:$IBKR_PORT (TWS/Gateway not listening?)"
    fi
  elif $RUN_PY -c "import socket; s=socket.create_connection(('$IBKR_HOST',int('$IBKR_PORT')),1);s.close()" 2>/dev/null; then
    pass "TWS/gateway port reachable $IBKR_HOST:$IBKR_PORT (python socket)"
  else
    warn "cannot connect to $IBKR_HOST:$IBKR_PORT (TWS/Gateway or firewall)"
  fi
else
  echo "(skip TWS check; use --check-ibkr; default probe 127.0.0.1:7497)"
fi

# UI healthz if pid
PID_FILE="${ROOT}/data/runtime/strategy_lab_ui.pid"
HOST="${STRATEGY_LAB_HOST:-127.0.0.1}"
PORT="${STRATEGY_LAB_PORT:-8765}"
if [[ -f "$PID_FILE" ]]; then
  pid="$(tr -d ' \n' < "$PID_FILE" || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    if command -v curl >/dev/null 2>&1; then
      if out=$(curl -sS --connect-timeout 2 "http://${HOST}:${PORT}/healthz" 2>&1); then
        pass "UI healthz: $out"
      else
        warn "UI pid present but healthz failed: $out"
      fi
    else
      warn "curl not installed; cannot probe /healthz"
    fi
  fi
else
  echo "UI not started via scripts (no pid) — healthz probe skipped"
fi

# Pytest
DO_PYTEST=0
for a in "$@"; do
  if [[ "$a" == "--pytest" ]]; then DO_PYTEST=1; fi
done
if [[ "$DO_PYTEST" -eq 1 ]]; then
  if $RUN_PY -m pytest -q --co -q tests/test_engine_launch_workflow.py 2>/dev/null | head -1 | grep -q .; then
    pass "pytest can collect tests/test_engine_launch_workflow.py"
  else
    fail "pytest collection failed for workflow tests"
  fi
fi

echo "=== done ==="
exit 0
