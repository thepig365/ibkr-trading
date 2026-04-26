# macOS 双击打启动器（Strategy Lab UI）

在仓库**根目录**（与 `scripts/` 同级）提供 **Strategy Lab.command** 作为**主入口**（一键：若已运行则只开浏览器，否则先启动再等 `healthz` 后打开 `/dashboard`），并保留**停止**、**仅打开**、**诊断** 等独立启动器。均**仅**调用 `scripts/*.sh`，不内嵌 `uvicorn` 逻辑、不内嵌密钥；**不**在启动器里连 IBKR/TWS、不下单、不跑 `first-paper-pass` 或自动交易循环（UI 为纸面/本地工作流，见 `healthz` 的 `paper_only`）。

## 文件说明

| 文件 | 作用 |
|------|------|
| **Strategy Lab.command**（**推荐**） | 若 `http://127.0.0.1:8765/healthz` 已可用则直接打开 `…/dashboard`；否则先执行 `scripts/start_strategy_lab_ui.sh`，轮询 `healthz` 后再执行 `scripts/open_strategy_lab_ui.sh`（`STRATEGY_LAB_UI_PATH=/dashboard`） |
| **Start Strategy Lab.command** | 与旧版行为一致：先 `start` 再 `open`（不检测是否已在跑；保留兼容） |
| **Stop Strategy Lab.command** | 执行 `scripts/stop_strategy_lab_ui.sh`，仅停本地 UI 进程 |
| **Open Strategy Lab.command**（可选/旧版） | 仅 `open_strategy_lab_ui.sh`；若本机无 UI 会提示去 **Strategy Lab.command** 或 `start` 脚本 |
| **Strategy Lab Doctor.command** | 执行 `scripts/strategy_lab_doctor.sh`（可附加 `--pytest`、`--check-ibkr` 等，与脚本一致） |

## 使用方式

1. **推荐**：在 Finder 中进入本仓库根目录，**双击** `Strategy Lab.command` — 已运行则只打开 **Dashboard**；未运行则先启动再打开（见 `healthz`）。  
2. 需要结束 UI 时，双击 `Stop Strategy Lab.command`。  
3. 需要自检 Python、venv、依赖、路径、（可选）pytest / TWS 端口时，双击 `Strategy Lab Doctor.command`。  
4. **（可选/旧版）** 仍可使用 `Start Strategy Lab.command` / `Open Strategy Lab.command`；**主推荐** 已改为上表中的 **Strategy Lab.command**。

运行结束后，终端窗口会提示按 **Enter** 再关闭，方便看清输出。

## 若系统提示“无法打开”或安全拦截

- **先赋予执行权限**（在终端中、仓库根目录下执行一次）：

  ```bash
  chmod +x "Strategy Lab.command" "Start Strategy Lab.command" "Stop Strategy Lab.command" "Open Strategy Lab.command" "Strategy Lab Doctor.command"
  ```

- **或**：在 Finder 中 **右键** → **打开**（Open），在确认对话框中仍选“打开”，之后即可正常双击。  
- 若仍被阻止，可在 **系统设置 → 隐私与安全性** 中允许该次运行。

## 与文档的关系

- 更完整的日常步骤见 [`daily-operation-checklist.md`](daily-operation-checklist.md)（双击打启动为**主路径**；脚本/CLI 为备选）。  
- 全量说明见 [`strategy-lab-user-manual.md`](strategy-lab-user-manual.md)。
