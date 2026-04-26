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
| Edge | `/edge` | 标的级 edge 画像、排名与纸面门控（只读 + 白名单构建命令） |
| Reports | `/reports` | 日/周/研究/回测/edge/扫描报告路径汇总与生成按钮（只通过白名单） |
| Logs | `/logs` | 近期命令与事件（只读、脱敏） |
| Settings / Doctor | `/settings` | 安全说明、白名单命令、运行时标志、诊断类按钮；**本手册链接** |

---

## 3. 每天如何使用（摘要）

1. （可选）启动 **TWS / IB Gateway** 纸面账户。  
2. 需要与券商对账时：在 **Dashboard / Settings** 点 **Paper Reconcile**，或终端运行 `python3 -m bot.cli paper-reconcile`（见每日清单）。  
3. 启动 **UI**：`python3 -m bot_ui` 或 `./scripts/start_strategy_lab_ui.sh`；macOS **推荐** 双击 **`Strategy Lab.command`**（已运行则只开 Dashboard）。  
4. 浏览器打开 `http://127.0.0.1:8765/`。  
5. 按当日计划跑 **Research / Watchlist / 扫描 / 回测**，再在 **Paper** 区用**白名单按钮**触发 Worker。  
6. 在 **Journal** 核对纸单与回测记录；在 **Reports** 生成/查看日周报告。  
7. 收工可 **停止 UI**：`./scripts/stop_strategy_lab_ui.sh`（不影响 TWS）。

更细步骤见 **`docs/daily-operation-checklist.md`**。

### 3.1 完全通过 UI 的日流程（不记 CLI）

**引擎只在满足 ICT/SMC ready + 1 分钟触发 + 全部纸面门控时才会发纸单**；Edge 与回测**不会**自动触发交易。若一整天无成交，多为门控/信号/对账/时段原因，见 `docs/troubleshooting.md`。

建议顺序（均为浏览器内**显式按钮**，页面加载**不会**连 IBKR）：

1. macOS **双击 `Strategy Lab.command`**（或脚本启动）进入 UI，并自动打开 **Dashboard**（若服务已在跑则不会重复启动）。  
2. **Dashboard**：看 Engine health、TWS/IBKR 可稍后用按钮刷新；**不要** expect 页面一打开就已有券商状态。  
3. **Research**：**Run Research Report** / Status / Macro 等。  
4. **Watchlist**：**Build Watchlist**（需 IBKR 时选带 IBKR 的按钮）。  
5. **Signals（ICT 标签）**：**Run Intraday Scan**；审阅 `DAY_TRADE_READY_*` / `WATCH_ONLY` 等。  
6. 若**休市**想做验证：**Backtest** 页跑 1 周/2 周/1 月 或自填区间；**不要**打开「fetch」类选项，除非你明确要拉取缓存。  
7. **Edge**：**Build Edge Profile(s)** 作候选排序；**仍须** 日内信号 + 1m 触发。  
8. **Paper**：**Paper Readiness**、**Paper Activation Status**；确认 Kill Switch 未误开、预算行可读。  
9. 仅当你**明确愿意发纸单**时，再点 **First Paper Pass**（一次受控 pass，不启动长期 loop）。  
10. **Journal**：核对 `bracket_integrity`、sizing、broker 错码。  
11. **Reports** 或 Dashboard：**Generate Daily / Weekly Report**。  
12. 收工：**Intraday Paper OFF**（若你曾打开）、需要时 **Stop** UI。

**周回测**：在 **Backtest** 页用「core basket」快捷或自填 5–20 日区间 + `strict` / `aggressive` / 双向等；引擎只用本地 K 线缓存，**不下单**。

**哪些按钮可能触达 IBKR**（只读或纸面，仍非实盘）：`ibkr-session-status`、`open-orders`、`portfolio`、**Paper Reconcile**、带 `build-watchlist` 且需行情的变体、**Intraday Scan** 上若勾选需行情的选项等。以页面说明为准。  
**可能产生纸面委托**的：`first-paper-pass`、`auto-paper-intraday-smc` 等**仅**在 Paper/受控 flow 中、且你显式点击后出现。

---

## 4. 如何启动 UI

**macOS（推荐）**：在仓库根目录**双击** **`Strategy Lab.command`** — 若本机 `http://127.0.0.1:8765/healthz` 已正常则**只**打开 **Dashboard**；否则先后台启动再打开。不连 IBKR。停止：双击 `Stop Strategy Lab.command`。说明见 `docs/mac-launchers.md`。

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

### 6.1 第一次端到端验证流程

第一次把整条「研究 → 表 → 扫描 → 回测 → 纸面 → 日志」走通时，建议顺序如下（**纸账户；不下实盘单**）：

1. 打开 **TWS / IB Gateway 纸面**（若你要 build-watchlist、扫描、对账、纸 pass 等需要连券商端口的步骤；仅看 UI 可跳过）。  
2. **启动 UI**（推荐 macOS 双击 **`Strategy Lab.command`**，或 `./scripts/start_strategy_lab_ui.sh`）。  
3. 在 **Research** 用白名单运行 **Run Research Report**（或 `python3 -m bot.cli research-report`）。  
4. 在终端或 **Watchlist** 相关流程跑 **Build Watchlist**（`build-watchlist --ibkr` 等，视配置而定）。  
5. 在 **Signals** 侧跑 **Intraday Scan**（`scan-intraday-smc-watchlist`），确认 `data/intraday_smc/` 有最新汇总。  
6. 在 **Backtest** 用缓存跑一笔小回测，或 `backtest-report --latest` 看最新摘要。  
7. 在 **Paper** 看 `intraday-paper-status`、Kill/ runtime 与 **Run One Intraday Paper Pass**；是否真实向纸账户发 bracket **仍受** `trading.intraday_paper`、对账、Kill、交易时段、信号等共同约束。  
8. 打开 **Journal / Logs** 与（若配置）**Telegram**，核对是否有新审计行或通知。  
9. 收工可 **停止 UI**（`Stop Strategy Lab.command` 或 `stop_strategy_lab_ui.sh`），不必为关 UI 而关 TWS。

> 研究指令里 **`auto_paper_allowed` 为否** 时，多表示**存在 per-symbol 硬挡**；宏观/VIX/新闻以软标与 `bot_notes` 提醒为主。真正是否下单仍由**执行层**（配置、对账、Kill、runtime）决定。详见下一小节。

### 6.2 为什么没有下单？

在 **Intraday 纸面 pass** 或 **自动纸** 中「没有发出 bracket」时，常见**只读原因**（非完整列表，以当次命令输出和 Paper 卡片区为准）：

| 情况 | 说明 |
|------|------|
| `trading.intraday_paper.enabled=false` | 在 `config/settings.yaml` 侧关闭；**不要**为测试去改你本地被保护的 `settings.local.yaml` 除非你有意。 |
| Intraday **runtime 未开** | `data/runtime/intraday_auto_paper_enabled` 未显式打开（自动 pass 会跳过；「一键 pass」仍可能跑但受配置/时段限制，见当次结果）。 |
| **Kill Switch** 激活 | 存在 `data/KILL_SWITCH`。 |
| **对账未通过** | `paper-reconcile` 不 PASS 时执行层会拒绝新风险。 |
| **没有 READY 信号** | 扫描汇总里无 `DAY_TRADE_READY_*` 等可落单档位。 |
| **bracket 无效** | 止损/目标等不满足约束。 |
| **重复仓位/重复挂单** | 与已有持仓或订单冲突。 |
| **不在允许交易窗** | 如 NY 时间早于/晚于配置的新仓窗口。 |
| **IBKR 不可用** | 端口未连、只读等。 |
| **Ticker edge 门控** | 见下节：无画像、只 watch、strict_only 挡 aggress、disabled、或只应用了风险乘子（`edge_profile_missing` 等，见当次 `edge_audit`）。 |
| **日度名义 / 表内 cap** | 如 `max_daily_notional_usd` 已用尽，不会绕过或重置该 cap。 |

**不会**在 UI 渲染时偷偷连 TWS 或下单；**不会**在默认配置下发「实盘」或**市价**单。纸面为 LIMIT bracket（父限价 + 子止损/目标），由 Broker/配置双重约束。

### 6.2.1 什么是 Ticker edge profile？为何不能「每个标的一样对待」？

- **Edge 画像**是对 **单一标的、单一策略**（如 `ict_smc_intraday_v1`）在某段样本上的**回测统计**（满充率、R、利润因子、回撤、按小时/方向分解等）经**可解释**公式得到的 **edge_score、置信度、建议模式、风险乘子**。  
- **不是**黑箱模型；**不是**在 UI 里直连 IBKR 算出来的。  
- **原因**：多标的池子里，有的标的在样本内有统计优势，有的没有；把纸面试单与放大量一视同仁会稀释纪律、浪费日度名义 cap。引擎可以**广扫**表，但**纸面触发与放大量**应跟「已建立或正在建立的优势」走。

**收盘后如何构建**（不默认拉全历史；无缓存时勿默默连券商）：

```bash
python3 -m bot.cli build-edge-profiles --symbols AAPL,NVDA,CRM --start 2026-04-01 --end 2026-04-24 --strategy ict_smc_intraday_v1
```

- 需已有 **1m 缓存**；缺数据时仅写出 `insufficient_data` 画像。  
- 显式需要补线时**才**在命令上加 `--fetch`（只读拉 K 线到缓存）。  
- 产物：`data/edge_profiles/YYYY-MM-DD-edge-profiles.json` 与同名 Markdown 报告。  

**对纸交易的影响**（`trading.intraday_paper`）：

- `edge_profile_enabled`：总开关。  
- `unknown_edge_policy` / `unknown_edge_risk_multiplier` / `allow_aggressive_without_edge_profile`：无画像时策略——默认**允许小幅 STRICT 试单**、**乘子 0.25**、**不允许 aggressive** 直到有可用画像。  
- 建议模式：`disabled` 跳过；`watch_only` 不提交纸单但可继续观察；`strict_only` 仅 STRICT；`strict_and_aggressive` 在配置允许时两者皆可。  
- **风险乘子**：`effective_risk ∝ base_risk × max_risk_multiplier`（见当笔 `edge_audit`）。

**置信度（简述）**：`insufficient_data` 样本不足；`weak` / `moderate` / `strong` 为递增；`negative` 为负期望、建议关闭纸试。  

**为何无画像时仍可小额 STRICT 纸试**：在可控名义与纪律下，对**新标的**做最小暴露验证；**aggress** 默认需要画像支撑，避免在未知统计特性上放大噪声。

### 6.2.2 ICT 执行链不变量（4H / 30m / 5m / 1m）

纸面下单**只**能来自 **ICT/SMC intraday** 扫描行的
`DAY_TRADE_READY_STRICT` / `DAY_TRADE_READY_AGGRESSIVE`，且摘要中须为真：

- `five_min_setup_found`（5m 结构）  
- `one_min_trigger_found`（1m 最终触发）  
- `higher_timeframe_context_ok`（新扫描：4H+30m 数据完整；旧 JSON 可能无此键，则仅强校验 5m+1m）

Edge、新闻、表内分数、相对成交量**不能**单独触发下单。缺失 1m 触发时跳过原因示例：`waiting_for_1m_trigger`；缺 5m/HTF 上下文：`structure_context_missing`。

### 6.3 本地 Paper Forward Test（`settings.local` + runtime, 13I）

1. **如何安全开启**  
   只通过 **`config/settings.local.yaml`**（已 gitignore）与 **`data/runtime/intraday_auto_paper_enabled`** 开启，不要改已跟踪的 `config/settings.yaml` 来「偷偷打开」交易。先 `python3 -m bot.cli write-paper-local-config` 预览，确认后再加 **`--write`**。然后 `python3 -m bot.cli intraday-paper-on` 打开 runtime 标志（或 UI **Intraday Paper ON**）。全程纸账户、**bracket LIMIT**、禁止市价/实盘。

2. **`settings.local.yaml` 是什么**  
   与 `settings.yaml` **覆盖合并**的本地层；适合本机只写密钥旁路与纸测试开关；**不提交**到 git。

3. **为什么不改 `config/settings.yaml`**  
   仓库内为团队默认/安全基线；本机纸前测应写在 local overlay，避免误提交、便于撤销。

4. **如何打开 intraday runtime**  
   CLI：`intraday-paper-on`；关闭：`intraday-paper-off`。与 Paper 页按钮写入同一路径。

5. **如何运行 readiness**  
   `python3 -m bot.cli paper-readiness-check --intraday --probe-ibkr --scan --source dynamic --limit 20`（`--scan` 需 TWS 只读取数，不下单）

6. **如何运行 first paper pass**  
   `python3 -m bot.cli first-paper-pass --source dynamic --limit 20`（可 `--telegram`）。内部会先做激活态与 readiness，再**一次** `auto-paper-intraday-smc`，**不**启动循环。

7. **未下单时看原因**  
   见 **§6.2** 表；并看 `intraday-paper-status`、`data/runtime/intraday_auto_paper_loop_state.json` 的 `last_reason` / `skipped_reasons`（**勿**提交该文件）。

8. **若下单成功，何处查看**  
   TWS 纸账户委托；**Journal** 与 `data/paper_orders/*-intraday-paper-orders.jsonl`；`engine-status` 中 `artifacts.latest_paper_order_log`。

9. **如何立刻停用**  
   - `intraday-paper-off` 或 UI；  
   - 建立 **Kill Switch** 文件；  
   - 在 TWS 中手动撤销挂单（如需要）。  

10. **系统绝不会**  
   不开实盘、不发明市价单通道、不裸单、不绕过 stop/target；Broker 与配置双重校验仍生效。

**辅助命令**：`paper-activation-status`（可选 `--probe-ibkr` 对账）、`engine-status --json` 中 **`paper_forward_test`** 段。

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

- **Signals** 有 **MTF** 与 **ICT/SMC Intraday** 两个页签。MTF 展示多周期汇总；**Intraday** 展示 `data/intraday_smc/` 下最新文件，并列出 HTF/5m/1m 等列。  
- **发纸面委托**在引擎侧要求：ICT/SMC 就绪类别 + **1 分钟触发** + 对账 / Kill / 预算等门控。Edge 画像**单独不能**触发下单。  
- 新扫描在 **UI 用白名单按钮** 或终端 `scan-mtf-smc-watchlist` / `scan-intraday-smc-watchlist`；**不在** UI 打开页面时连 TWS。

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
