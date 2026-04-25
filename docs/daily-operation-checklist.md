# 每日操作清单（Strategy Lab 本地）

> 按顺序执行；**不**包含实盘交易步骤。环境变量与密钥仍放在本机 `.env`（已 gitignore），勿提交。  
> 与 `docs/strategy-lab-user-manual.md` 配合使用。

---

## 盘前

- [ ] **启动 TWS / IB Gateway（纸面账户）**；确认端口、客户端 ID 与 `docs/ibkr-setup.md` 一致。  
- [ ] 检查 **Kill Switch** 未误开：`data/KILL_SWITCH` 不应在常规日存在。  
- [ ] （可选）运行诊断：`./scripts/strategy_lab_doctor.sh` 或加 `--check-ibkr` 仅检测端口。  

---

## 对账与数据

- [ ] 运行 **paper-reconcile**（在终端；需要 IBKR 已连接时）：  
  `python3 -m bot.cli paper-reconcile`  
- [ ] 若对账失败：先看终端输出与 `memory/DAILY-SUMMARY.md` 回退通知，**先不要**开自动纸策略。

---

## 启动 UI

- [ ] 启动：`python3 -m bot_ui` **或** `./scripts/start_strategy_lab_ui.sh`  
- [ ] 浏览器打开：`http://127.0.0.1:8765/`（或 `open_strategy_lab_ui.sh` 在 macOS）  
- [ ] 打开 **Dashboard** 看 badges（ACCOUNT: PAPER、BACKEND: LOCAL 等）。  

---

## 研究与表

- [ ] **Research Report**：在 UI 中通过白名单运行 `research-report`（或按你工作流在 Worker 中跑）。  
- [ ] **Build Watchlist**：`build-watchlist`（需 IBKR 与配置）。  
- [ ] 在 **Watchlist** 页确认展示与最新 `data/watchlists/` 是否一致。  

---

## 扫描与信号

- [ ] 运行 **MTF 扫描**（如 `scan-mtf-smc-watchlist`）在 CLI。  
- [ ] 运行 **Intraday 扫描**（如 `scan-intraday-smc-watchlist`）在 CLI。  
- [ ] 在 **Signals** 页审阅；无新文件时先确认 `data/mtf_smc/` 与 `data/intraday_smc/` 是否生成。  

---

## 回测

- [ ] 确认 **Candles** 已缓存；否则 `fetch-candles` 再跑回测。  
- [ ] 在 UI **Backtest** 或通过 CLI 运行 `backtest-intraday-smc` / 表版本。  
- [ ] 检查 `data/backtests/intraday/*-backtest-summary.json`。  

---

## 纸面自动化（仅当你明确需要）

- [ ] 在 `config/settings.yaml` 与 **runtime 文件**中确认 **MTF / Intraday 自动纸** 符合预期（非本文档可写范围则绝不通过提交修改只读受保护文件来「偷偷开启」）。  
- [ ] 在 **Paper** 页开关 **Intraday auto** 等，仅写规范路径。  
- [ ] 仍保持 **bracket 纸面** 与对账/ Kill 逻辑；不明原因不下单。  

---

## Journal 与审阅

- [ ] 打开 **Journal** 看纸单 JSONL 与回测行项目。  
- [ ] 运行只读总览：  
  `python3 -m bot.cli engine-status --json`  

---

## 收工

- [ ] 记录当日结论（可写在 `memory/` 或个人笔记，**勿**提交含隐私内容）。  
- [ ] 若需释放端口：  
  `./scripts/stop_strategy_lab_ui.sh`  
- [ ] **不需要**为「正常关 UI」而关闭 TWS（除非你希望完全断连）。  

---

## 附：快速命令

| 目的 | 命令 |
|------|------|
| 引擎/产物快照 | `python3 -m bot.cli engine-status --json` |
| UI 状态 + 日志尾 | `./scripts/status_strategy_lab_ui.sh` |
| 本机检查 | `./scripts/strategy_lab_doctor.sh` |
| 冒烟测试 | `make strategy-lab-smoke` |
