# macOS 双击打启动器（Strategy Lab UI）

在仓库**根目录**（与 `scripts/` 同级）提供四个 `.command` 文件。双击后会在“终端”中运行，**仅**调用 `scripts/*.sh`，不复制启动逻辑、不内嵌密钥、**启动 UI 时不会连接 TWS/IBKR**，也**不会下单或开启任何实盘/真实账户交易**（UI 为纸面/本地只读工作流，见 `healthz` 的 `paper_only`）。

## 文件说明

| 文件 | 作用 |
|------|------|
| **Start Strategy Lab.command** | 先执行 `scripts/start_strategy_lab_ui.sh` 再执行 `scripts/open_strategy_lab_ui.sh`（打开浏览器） |
| **Stop Strategy Lab.command** | 执行 `scripts/stop_strategy_lab_ui.sh`，仅停本地 UI 进程 |
| **Open Strategy Lab.command** | 仅执行 `scripts/open_strategy_lab_ui.sh`（在默认浏览器中打开本机地址） |
| **Strategy Lab Doctor.command** | 执行 `scripts/strategy_lab_doctor.sh`（可附加 `--pytest`、`--check-ibkr` 等，与脚本一致） |

## 使用方式

1. **推荐**：在 Finder 中进入本仓库根目录，**双击** `Start Strategy Lab.command` 一次即可启动并尝试打开浏览器。  
2. 需要结束 UI 时，双击 `Stop Strategy Lab.command`。  
3. UI 已在跑、只想开浏览器时，双击 `Open Strategy Lab.command`。  
4. 需要自检 Python、venv、依赖、路径、（可选）pytest / TWS 端口时，双击 `Strategy Lab Doctor.command`。

运行结束后，终端窗口会提示按 **Enter** 再关闭，方便看清输出。

## 若系统提示“无法打开”或安全拦截

- **先赋予执行权限**（在终端中、仓库根目录下执行一次）：

  ```bash
  chmod +x "Start Strategy Lab.command" "Stop Strategy Lab.command" "Open Strategy Lab.command" "Strategy Lab Doctor.command"
  ```

- **或**：在 Finder 中 **右键** → **打开**（Open），在确认对话框中仍选“打开”，之后即可正常双击。  
- 若仍被阻止，可在 **系统设置 → 隐私与安全性** 中允许该次运行。

## 与文档的关系

- 更完整的日常步骤见 [`daily-operation-checklist.md`](daily-operation-checklist.md)（双击打启动为**主路径**；脚本/CLI 为备选）。  
- 全量说明见 [`strategy-lab-user-manual.md`](strategy-lab-user-manual.md)。
