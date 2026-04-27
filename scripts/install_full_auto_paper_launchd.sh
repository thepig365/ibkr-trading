#!/bin/bash
# Install launchd for full-auto PAPER supervisor. Avoids executing scripts under ~/Documents (TCC).
# Optional: STRATEGY_LAB_REPO_DIR=/path/to/repo bash scripts/install_full_auto_paper_launchd.sh
set -euo pipefail

REPO_ROOT="${STRATEGY_LAB_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"
export REPO_ROOT

SRC_PLIST="${REPO_ROOT}/scripts/com.strategy-lab.full-auto-paper.plist"
SRC_WRAP="${REPO_ROOT}/scripts/strategy_lab_launchd_wrapper.sh"
DEST_PLIST="${HOME}/Library/LaunchAgents/com.strategy-lab.full-auto-paper.plist"
LABEL="com.strategy-lab.full-auto-paper"

WORK_DIR="${HOME}/Library/Application Support/StrategyLab"
LOG_DIR="${HOME}/Library/Logs/StrategyLab"
WRAPPER_SH="${WORK_DIR}/run_full_auto_paper_supervisor.sh"
OUT_LOG="${LOG_DIR}/launchd_full_auto.out.log"
ERR_LOG="${LOG_DIR}/launchd_full_auto.err.log"

if [[ ! -f "${SRC_PLIST}" ]] || [[ ! -f "${SRC_WRAP}" ]]; then
  echo "error: missing plist or wrapper in ${REPO_ROOT}/scripts" >&2
  exit 1
fi

echo "=== Strategy Lab launchd install (paper only) ==="
echo "Repo: ${REPO_ROOT}"

if [[ "${REPO_ROOT}" == *"/Documents/"* ]] || [[ "${REPO_ROOT}" == *"/Documents" ]]; then
  echo ""
  echo "WARNING: This repo path is under macOS 'Documents' (or Desktop/iCloud-protected areas)."
  echo "  launchd jobs may get 'Operation not permitted' or getcwd errors when the plist"
  echo "  points at scripts or WorkingDirectory under Documents."
  echo "  This installer uses a wrapper in Library/Application Support and sets PYTHONPATH."
  echo "  If it still fails, move the clone to e.g. ${HOME}/StrategyLab/ibkr-trading-bot and reinstall,"
  echo "  or grant Full Disk Access to /bin/bash and /usr/bin/python3 in"
  echo "  System Settings > Privacy & Security > Full Disk Access."
  echo ""
fi

mkdir -p "${REPO_ROOT}/logs" "${REPO_ROOT}/data/runtime" "${WORK_DIR}" "${LOG_DIR}"
install -m 0755 "${SRC_WRAP}" "${WRAPPER_SH}"

REPO_ROOT="${REPO_ROOT}" \
SRC_PLIST="${SRC_PLIST}" \
DEST_PLIST="${DEST_PLIST}" \
WORK_DIR="${WORK_DIR}" \
WRAPPER_SH="${WRAPPER_SH}" \
OUT_LOG="${OUT_LOG}" \
ERR_LOG="${ERR_LOG}" \
python3 <<'PY'
import os
from pathlib import Path
repo = Path(os.environ["REPO_ROOT"]).resolve()
text = Path(os.environ["SRC_PLIST"]).read_text(encoding="utf-8")
for key, val in {
    "__WORK_DIR__": Path(os.environ["WORK_DIR"]),
    "__WRAPPER_SH__": Path(os.environ["WRAPPER_SH"]),
    "__OUT_LOG__": Path(os.environ["OUT_LOG"]),
    "__ERR_LOG__": Path(os.environ["ERR_LOG"]),
    "__REPO_ROOT__": str(repo),
}.items():
    text = text.replace(key, str(val))
Path(os.environ["DEST_PLIST"]).parent.mkdir(parents=True, exist_ok=True)
Path(os.environ["DEST_PLIST"]).write_text(text, encoding="utf-8")
PY

launchctl unload "${DEST_PLIST}" 2>/dev/null || true
launchctl load "${DEST_PLIST}"

echo ""
echo "Installed wrapper: ${WRAPPER_SH}"
echo "LaunchAgents plist: ${DEST_PLIST}"
echo "Launchd stdout: ${OUT_LOG}"
echo "Launchd stderr: ${ERR_LOG}"
echo "Supervisor log (wrapper): ${LOG_DIR}/full_auto_paper_supervisor.log"
echo ""
echo "Status:  bash ${REPO_ROOT}/scripts/status_full_auto_paper_launchd.sh"
echo "Uninstall: bash ${REPO_ROOT}/scripts/uninstall_full_auto_paper_launchd.sh"

# Smoke: if stderr already shows EPERM from a previous run, note it
if [[ -f "${ERR_LOG}" ]] && grep -q "Operation not permitted" "${ERR_LOG}" 2>/dev/null; then
  echo ""
  echo "NOTE: ${ERR_LOG} still contains a previous 'Operation not permitted'."
  echo "  After one launchd run, re-check. If it persists, move the repo or use Full Disk Access (see doc)."
fi
