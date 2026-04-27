# 每日操作清单（Strategy Lab 本地）

> 按顺序执行；**不**包含实盘交易步骤。环境变量与密钥仍放在本机 `.env`（已 gitignore），勿提交。  
> 与 `docs/strategy-lab-user-manual.md` 配合使用。

**macOS 推荐首选：** 在仓库根目录**双击** **`Strategy Lab.command`**（一键：若 UI 已在跑则只打开 Dashboard，否则启动后打开）；收工可双击 `Stop Strategy Lab.command`；诊断用 `Strategy Lab Doctor.command`。说明见 `docs/mac-launchers.md`。`Start Strategy Lab.command` / `Open Strategy Lab.command` 为可选旧版。（终端下仍可用 `./scripts/start_strategy_lab_ui.sh` 等。）

### 纯 UI 日流程（与 `docs/strategy-lab-user-manual.md` §3.1 一致）

- [ ] 1. **`Strategy Lab.command`**（或 `start_strategy_lab_ui.sh`）  
- [ ] 2. 浏览器 `http://127.0.0.1:8765/dashboard` — 健康/预算/当前**纸面策略**提示；若使用 **Automatic Paper Trading Engine**：先看 **Check Automatic Engine Readiness**（`automatic-paper-engine-readiness --json`）或 `run-automatic-paper-engine --dry-run --json`。旧版 **60 分钟烟测准备**读数仍用 **Automatic paper loop readiness** / `auto-loop-readiness`（含 `READY_FOR_PAPER_TEST`，与引擎门控可能不一致）。  
- [ ] 2a.（可选）**早晨/全日纸面自动引擎**：Dashboard / Paper 的 **Start Morning / Full-Day Paper Engine**（白名单 `run-automatic-paper-engine`）；或终端 `python3 -m bot.cli run-automatic-paper-engine --session morning --telegram --report-on-exit`。底层仍与 `run-auto-paper-intraday-loop` 同执行链；**`run-auto-paper-intraday-loop` 本身**不在 UI 白名单。  
- [ ] 2b. 如需切换 **scan / backtest / edge / paper** 的默认：打开 **/strategies**（Strategy Center），`data/runtime/selected_strategy.json` 不提交  
- [ ] 3. **Research** — 需要时运行报告 / Telegram 变体  
- [ ] 4. **Watchlist** — 构建表  
- [ ] 5. **Signals（ICT）** — 日内扫描  
- [ ] 6. 若休市：**Backtest** — 先用 **Check Data Coverage** / `candle-coverage` 看 1m 缓存是否够；或一键 **Fetch Missing Data & Run Backtest**（`backtest-oneclick`，仅在你**明确点击**后才会连 IBKR 拉历史；不点则只读本地）。分步时：需要拉线再用 **Fetch missing candles**（`fetch-candles` + `--ibkr`）  
- [ ] 7. **Edge** — 建立画像（默认缓存）  
- [ ] 8. **Paper** — Readiness / Activation；**不**在无意向时点 First Paper Pass  
- [ ] 9. 仅当准备好：`First Paper Pass` 一次受控试单（不启 loop）  
- [ ] 10. **Journal** — 括号/帽/错码  
- [ ] 11. **Reports（主报告台）** — 在 **`/reports`** 看「今日汇总」、纸面日/周、回测/edge、研究/盘前/新闻状态；**Daily / Weekly 生成**与路径以页面为准；**邮件不必需**。可选在 **Settings** / **Reports** 看各 **data/** 子目录占用的空间（`data/candles`、**`data/backtests`** 为本地回测用，git 忽略、不上传 GitHub；纸单审计/运行时目录勿手删）  
- [ ] 12. 收工：**Intraday Paper OFF**（如曾打开）、Stop UI  

**周回测**：**Backtest** 页用 core basket 快捷或自填多周窗口；**不下单**。

**收市（EOD，纸面）** — 需要 TWS 时依次：`open-orders` → `portfolio` → `paper-reconcile` → `paper-daily-report --latest`（**`--email` 可选**；**先在 `/reports` 看本地报告**）；只读打印推荐顺序：`python3 -m bot.cli eod-paper-checklist`。

**重大新闻（可选，不刷屏）** — 配置 Finnhub/FMP 等 key 后：`python3 -m bot.cli news-monitor-readiness --json` 查看是否就绪；`python3 -m bot.cli market-news-check --core-basket --market-moving-only --dry-run --json` 干跑。默认 **不** 发「无新闻」；RTH 每小时可在外部 **cron/launchd** 调用（勿在仓库内静默开守护进程，除非你已自行配置调度）。

## 第一次端到端纸面验证（E2E）

> 与 `docs/strategy-lab-user-manual.md` 中 **§6.1** 同序；这里作清单勾选用。

- [ ] 纸面 TWS/IB Gateway 已开（需券商步骤时）  
- [ ] 双击或脚本 **启动 UI**  
- [ ] **Research Report**（UI 或 CLI）  
- [ ] **Build Watchlist**（有 IBKR 时）  
- [ ] **Intraday Scan**（生成 `data/intraday_smc/` 汇总）  
- [ ] **Backtest** 或 `backtest-report --latest`  
- [ ] **Paper** 看 status / 可跑一次 **Intraday Paper Pass**（是否发单以配置+对账+信号为准）  
- [ ] **Journal**、**Logs**、（可选）Telegram  
- [ ] 若仍无单：见用户手册 **「为什么没有下单？」** 与 `docs/troubleshooting.md`  

**本地纸前测（13I，可穿插在「启动 UI」之后）：** 使用 **仅** `config/settings.local.yaml` + `intraday-paper-on`；`paper-readiness-check`；可选 `first-paper-pass` 一次。勿改已跟踪的 `settings.yaml`；不要提交 local 与 runtime 产物。详见 `docs/strategy-lab-user-manual.md` §6.3。

---

## 盘前

- [ ] **启动 TWS / IB Gateway（纸面账户）**；确认端口、客户端 ID 与 `docs/ibkr-setup.md` 一致。  
- [ ] 检查 **Kill Switch** 未误开：`data/KILL_SWITCH` 不应在常规日存在。  
- [ ] （可选）运行诊断：`./scripts/strategy_lab_doctor.sh` 或加 `--check-ibkr` 仅检测端口。  
- [ ] **（建议）美东 08:30 前后** 生成盘前简报：  
  `python3 -m bot.cli premarket-brief --today`  
  若已配置发信：  
  `python3 -m bot.cli premarket-brief --today --email`（缺凭证会安全跳过；见用户手册 **§2.3**）  
- [ ] 在 **Research** 或 **Dashboard** 看简报摘要/邮件状态；**新闻与简报不触发交易** — 仍需 ICT/SMC + 1 分钟触发。  

---

## 对账与数据

- [ ] 运行 **paper-reconcile**（在终端；需要 IBKR 已连接时）：  
  `python3 -m bot.cli paper-reconcile`  
- [ ] 若对账失败：先看终端输出与 `memory/DAILY-SUMMARY.md` 回退通知，**先不要**开自动纸策略。

---

## 启动 UI

- [ ] **（macOS 主路径）** 仓库根目录双击 **`Strategy Lab.command`**（先检查 `healthz`，避免重复启动；再打开 `/dashboard`。不连 TWS。）  
- [ ] 备选：终端中 `python3 -m bot_ui` **或** `./scripts/start_strategy_lab_ui.sh`，再 `http://127.0.0.1:8765/` 或 `./scripts/open_strategy_lab_ui.sh`  
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

## 收盘后（可选）：Ticker edge 画像

- [ ] 对关心标的、在**有 1m 缓存**的日期范围运行：  
  `build-edge-profiles`（或 UI **Edge** 页白名单按钮；勿默认加大 `--fetch`）。  
- [ ] 查看 `data/edge_profiles/YYYY-MM-DD-edge-profile-report.md` 与 JSON；`edge-profile-report --latest` 看最新路径。  
- [ ] 在 **/edge** 与 **Signals（ICT 表）** 对照明细；纸面放大量以配置门控 + **edge 建议模式/乘子** 为准。详见 `docs/strategy-lab-user-manual.md` §6.2.1。

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
- [ ] 若需释放 **UI 端口**（macOS 可）双击 `Stop Strategy Lab.command`，**或**终端：`./scripts/stop_strategy_lab_ui.sh`  
- [ ] **不需要**为「正常关 UI」而关闭 TWS（除非你希望完全断连）。  

---

## 附：快速命令

| 目的 | 命令 |
|------|------|
| 引擎/产物快照 | `python3 -m bot.cli engine-status --json` |
| UI 状态 + 日志尾 | `./scripts/status_strategy_lab_ui.sh` |
| 本机检查 | `./scripts/strategy_lab_doctor.sh` |
| 冒烟测试 | `make strategy-lab-smoke` |
