#!/bin/bash
# Install launchd job for full-auto PAPER supervisor (never live). No secrets printed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export REPO_ROOT
SRC="${REPO_ROOT}/scripts/com.strategy-lab.full-auto-paper.plist"
DEST="${HOME}/Library/LaunchAgents/com.strategy-lab.full-auto-paper.plist"
LABEL="com.strategy-lab.full-auto-paper"

if [[ ! -f "${SRC}" ]]; then
  echo "error: missing ${SRC}" >&2
  exit 1
fi

mkdir -p "${REPO_ROOT}/logs" "${REPO_ROOT}/data/runtime"

REPO_ROOT="${REPO_ROOT}" SRC="${SRC}" DEST="${DEST}" python3 <<'PY'
import os
from pathlib import Path
root = Path(os.environ["REPO_ROOT"]).resolve()
src = Path(os.environ["SRC"])
dest = Path(os.environ["DEST"])
text = src.read_text(encoding="utf-8").replace("__REPO_ROOT__", str(root))
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(text, encoding="utf-8")
PY

launchctl unload "${DEST}" 2>/dev/null || true
launchctl load "${DEST}"

echo "Loaded: ${DEST}"
echo "Status:  bash ${REPO_ROOT}/scripts/status_full_auto_paper_launchd.sh"
echo "Uninstall: bash ${REPO_ROOT}/scripts/uninstall_full_auto_paper_launchd.sh"
echo "Label: ${LABEL}"
