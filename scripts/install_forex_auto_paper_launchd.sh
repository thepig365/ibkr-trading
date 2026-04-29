#!/bin/bash
# Install Forex auto paper launchd (paper only; gated inside Python YAML + runtime flags).
set -euo pipefail

REPO_ROOT="${STRATEGY_LAB_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"
export REPO_ROOT

SRC_PLIST="${REPO_ROOT}/scripts/com.strategy-lab.forex-auto-paper.plist"
SRC_WRAP="${REPO_ROOT}/scripts/forex_auto_paper_launchd_wrapper.sh"
DEST_PLIST="${HOME}/Library/LaunchAgents/com.strategy-lab.forex-auto-paper.plist"
LABEL="com.strategy-lab.forex-auto-paper"

WORK_DIR="${HOME}/Library/Application Support/StrategyLab"
LOG_DIR="${HOME}/Library/Logs/StrategyLab"
WRAPPER_SH="${WORK_DIR}/forex_auto_paper_launchd_wrapper.sh"
OUT_LOG="${LOG_DIR}/launchd_forex_auto_paper.out.log"
ERR_LOG="${LOG_DIR}/launchd_forex_auto_paper.err.log"

if [[ ! -f "${SRC_PLIST}" ]] || [[ ! -f "${SRC_WRAP}" ]]; then
  echo "error: missing plist or wrapper in ${REPO_ROOT}/scripts" >&2
  exit 1
fi

echo "=== Strategy Lab Forex auto paper launchd (paper only) ==="
echo "Repo: ${REPO_ROOT}"

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
echo "Forex supervisor log (wrapper): ${LOG_DIR}/forex_auto_paper_supervisor.log"
echo ""
echo "Status:  bash ${REPO_ROOT}/scripts/status_forex_auto_paper_launchd.sh"
echo "Uninstall: bash ${REPO_ROOT}/scripts/uninstall_forex_auto_paper_launchd.sh"
