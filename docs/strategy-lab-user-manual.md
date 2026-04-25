# Strategy Lab 用户手册（本地）

> 本文档描述**本地** Strategy Lab：纸面交易、研究、回测与日志。  
> **不**涉及实盘下单、**不**在本文档中启用 live trading。  
> Vercel 远程部署见文末「未来架构」。

---

## 1. 系统是什么

Strategy Lab 是一套 **Python 后端（`bot`）+ 本地 FastAPI UI（`bot_ui`）** 组成的**策略实验室**：

- **UI**：在浏览器中查看研究、信号、回测、Paper 控制与 Journal；**启动 UI 时不会连接 IBKR/TWS**。
- **Worker/CLI**：通过 `python3 -m bot.cli …` 执行扫描、回测、纸面撮合等；仅在**你显式运行命令**时才会按配置连接 TWS（纸面端口）。
- **安全默认值**：`account.block_live_trading`、纸面专用、下单入口受 Broker 与配置双重约束（详见 `docs/safety-rules.md`）。

---

## 2. 每个页面用途

| 页面 | 路径 | 用途 |
|------|------|------|
| Dashboard | `/dashboard` | 总览、快捷状态 |
| Watchlist | `/watchlist` | 关注列表与动态/静态表相关说明 |
| Signals (MTF) | `/signals` | 多周期 SMC/ICT 信号汇总（只读展示） |
| Paper Trading | `/paper` | 纸交易与自动纸策略**安全命令**入口（白名单） |
| Journal | `/journal` | 纸单审计与回测成交只读日志 |
| Strategies | `/strategies` | 策略注册表与多策略扫描入口 |
| Research | `/research` | 研究情报层报告与指令（只读 + 命令） |
| Backtest | `/backtest` | ICT/SMC 日内回测配置与运行（引擎在 CLI/Worker） |
| Logs | `/logs` | 近期命令与事件（只读） |
| Settings | `/settings` | 安全说明、白名单命令、运行时标志；**本手册链接** |

---

## 3. 每天如何使用（摘要）

1. （可选）启动 **TWS / IB Gateway** 纸面账户。  
2. 需要与券商对账时运行：`python3 -m bot.cli paper-reconcile`（见每日清单）。  
3. 启动 **UI**：`python3 -m bot_ui` 或 `./scripts/start_strategy_lab_ui.sh`。  
4. 浏览器打开 `http://127.0.0.1:8765/`。  
5. 按当日计划跑 **Research / Watchlist / 扫描 / 回测**，再在 **Paper** 区用**白名单按钮**触发 Worker。  
6. 在 **Journal** 核对纸单与回测记录。  
7. 收工可 **停止 UI**：`./scripts/stop_strategy_lab_ui.sh`（不影响 TWS）。

更细步骤见 **`docs/daily-operation-checklist.md`**。

---

## 4. 如何启动 UI

**前台（调试用）**：

```bash
cd /path/to/ibkr-trading-bot
source .venv/bin/activate   # 若已创建 venv
python3 -m bot_ui
```

**后台（PID + 日志）**：

```bash
./scripts/start_strategy_lab_ui.sh
```

- 绑定：**127.0.0.1:8765**（与 `start_strategy_lab_ui.sh` 内参数一致，不默认监听 0.0.0.0）  
- PID：`data/runtime/strategy_lab_ui.pid`  
- 标准输出/错误：`logs/strategy-lab-ui.stdout.log`、`logs/strategy-lab-ui.stderr.log`  
- 环境变量可选用：`STRATEGY_LAB_PORT`（与 `bot_ui` 一致时生效逻辑以脚本为准）

---

## 5. 如何停止 UI

仅停止本机 FastAPI 进程，**不**停 TWS、**不**改纸循环配置：

```bash
./scripts/stop_strategy_lab_ui.sh
```

---

## 6. 如何查看 bot / UI 是否在跑

```bash
./scripts/status_strategy_lab_ui.sh
```

或只读配置与最新产物（**不连 IBKR**）：

```bash
python3 -m bot.cli engine-status --json
```

带 UI /healthz 探测（不连 IBKR，仅 HTTP）：

```bash
python3 -m bot.cli engine-status --json --probe-ui
```

---

## 7. Research Mode 怎么用

- 在 **Research** 页面阅读最新 `data/research/` 下报告；若不存在则显示空态。  
- 生成/更新报告通过 **白名单** 中的 `research-report` 等命令由 Worker 执行，**不是**在浏览器里直连券商。  
- 详见 `docs/deployment-architecture.md` 中 UI/Worker 边界。

---

## 8. Watchlist 怎么看

- **Watchlist** 页展示与动态表/配置相关的只读信息；**构建**动态表使用 CLI（如 `build-watchlist`）在 Worker 中运行。  
- 最新动态表文件在 `data/watchlists/`，`engine-status` 会显示「最新」路径（若存在）。

---

## 9. Signals 怎么看

- **Signals (MTF)** 展示多周期信号汇总；数据来自本地已生成的扫描结果。  
- 新扫描在终端使用 `python3 -m bot.cli scan-mtf-smc-watchlist` 等（需 IBKR 与配置允许），不在 UI 渲染线程连 TWS。

---

## 10. Backtest 怎么跑

- 在 **Backtest** 页填写/选择参数，通过**允许的单命令**触发（见 Settings 白名单与 `backtest-intraday-smc` 等）。  
- 回测数据使用本地 `data/candles/` 等缓存；若缺数据需先 `fetch-candles`（只读拉取，见 `docs/ibkr-setup.md`）。  
- 报告位于 `data/backtests/intraday/`，`engine-status` 会列出最新摘要文件（若有）。

---

## 11. Paper Trading 怎么开关

- **Kill Switch**、**MTF 自动纸**、**Intraday 自动纸** 等使用 **Settings / Paper** 页的安全表单；底层写入 `data/KILL_SWITCH`、`data/runtime/*` 等**规范路径**（与 CLI 一致）。  
- 纸面执行仍为 **bracket、纸账户、非实盘** 约束；见 `config/settings.yaml` 中 `trading.intraday_paper` 与 Broker 层校验。  
- 勿在 UI 中粘贴任意 shell 内容；只使用白名单命令。

---

## 12. Journal 怎么看

- **Journal** 聚合并展示纸单 JSONL 与回测成交（见 state store 逻辑），只读。  
- 不用于下单；无「市场单」入口。

---

## 13. Kill Switch 是什么

- 文件 **`data/KILL_SWITCH`** 存在时视为**禁止新风险动作**的硬开关（与具体模块实现一致，见各命令文档）。  
- 用于紧急停止自动化或人工熔断；**不是** TWS 登出。

---

## 14. MTF auto flag 与 Intraday auto flag

- **MTF**：`data/runtime/mtf_auto_paper_enabled`（及 MTF 循环状态等），控制 MTF 纸策略自动化是否在运行时允许继续（以代码与配置为准）。  
- **Intraday**：`data/runtime/intraday_auto_paper_enabled`（及 `intraday_auto_paper_loop_state.json` 等），与日内纸执行 pass 是否运行有关。  
- **缺失文件**时回退到 `settings.yaml` 中对应默认；两者路径与 CLI、UI 写入一致。

---

## 15. Telegram 适合做什么

- 在配置正确时，用于**通知**对账失败、重要跳过原因、研究摘要等（见 `docs/telegram-notifications.md`）。  
- **不是**远程下单通道；不替代 Kill Switch 与对账。

---

## 16. 哪些东西仍然不能做

- **不能**在 UI 渲染路径直接连 TWS 或 `Broker.place_order`。  
- **不能**在仓库默认配置下启用 live trading。  
- **不能**用 UI 执行白名单**以外**的任意 CLI。  
- **不能**把 API Token、`.env` 内容提交到 git（见 `.gitignore`）。

---

## 17. 未来 Vercel / Worker 架构

- 当前：**本地 UI** 通过命令队列执行白名单子进程；**未来**可换 `STRATEGY_LAB_BACKEND=remote`，由 Vercel 仅渲染，Worker 在云端/家中执行（见 `docs/vercel-worker-architecture.md`）。  
- 本文档描述的是**今天本地**操作方式。

---

## 18. 常见问题

| 问题 | 提示 |
|------|------|
| 端口被占用 | 改 `STRATEGY_LAB_PORT` 或结束占用 8765 的进程。 |
| 页面全空 | 多数为尚无 `data/*` 产物，先跑对应 CLI 生成 JSON。 |
| 命令被拒绝 | 检查 Settings 中白名单与参数校验。 |
| 纸单未成交 | 纸面=模拟/限额逻辑，**非**保证成交；看 Journal 中 skipped 原因。 |

更细见 **`docs/troubleshooting.md`**。
