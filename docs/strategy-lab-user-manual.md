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

### 1.1 Web UI 显示语言（EN / 中文）

- 侧栏提供 **EN** 与 **中文** 切换。也可用查询参数：默认 **`?lang=en`（英文）**；`?lang=zh` 为简体中文界面文案。
- 选择会写入浏览器 cookie **`strategy_lab_lang`**（`en` / `zh`），下次进入同一路由可保持语言，直至再次切换或清除 cookie。
- **中文模式**会翻译**操作类**标题、按钮、说明与部分安全提示；**策略名、配置键、JSON 字段、路径、CLI 子命令名**等可能仍为**英文**以便对照文档与日志。
- **仅显示层**：语言切换**不改变**白名单、自动纸引擎、下单逻辑、回测或任何 TWS/IBKR 行为；页面**渲染本身仍不建立**到经纪商的连接。

---

### 1.2 成交对账（Fill reconciliation，只读）

- **是什么**：CLI `python3 -m bot.cli reconcile-fills`（或与 Dashboard「**成交对账**」等价白名单命令）只读连接 TWS，拉取 **Executions/Fills**，与本地 **`data/paper_orders/*.jsonl`** 里已发送到券商的记录按 **委托号（parent/stop/target order id）** 等方式对齐。  
- **不是什么**：**不等同**「本地提交了就有持仓」；**不**根据计划止损/止盈价臆造平仓；**不**下单、不改单。**UI 打开页面本身不连 IBKR**；只对显式 CLI/按钮会话使用 **`broker_readonly`** client id。  
- **产出（默认 gitignore）**：最近一次摘要 `data/runtime/fills_reconciliation_last.json`；可按日存档 `data/reconciled_trades/YYYY-MM-DD-reconciled-trades.json`、`data/executions/YYYY-MM-DD-ibkr-executions.json`。  
- **之后才可靠**：Dashboard **成交对账**卡片、**累计 R / 交易图上的 Entry·Exit**，在存在真实成交记录并对账后更准确；若无成交或未跑对账，可能仍为「已提交未成交」或缺 Exit 标记。

### 1.3 Forex ICT 1 分钟测试模式（独立于美股日内）

> **与股票 ICT（`ict_smc_intraday_v1`）完全分离**：独立 `strategy_id`（默认 `ict_fx_1m_test`）、独立数据目录 **`data/candles_forex/`**、独立审计 **`data/forex_orders/`**、独立 `orderRef` 前缀（见 `config/forex_ict_1m.yaml`）。

- **目的**：墨尔本白天仍可验证「引擎链路 + IBKR IDEALPRO 只读/纸面」（外汇 24/5），**不靠**美盘股票时段。  
- **推荐先试货币对**：`AUD/USD`、`USD/JPY`、`AUD/JPY`（YAML 中还列有可选币种）。  
- **流程**：先在纸面、`Kill switch` off 下拉 **1 分钟 MIDPOINT** → `fetch-forex-candles` → 本地 **`run-forex-ict-1m --dry-run`** → 确认就绪前 **不要** 把 YAML 设为 `submit_to_broker: true`。  
- **硬性约束**：**仅 paper、仅限价括号（实现为 LMT 括号）、不写市价单**；干跑不写单；实盘提交需 **显式 YAML 启用** — 不改变股票自动纸引擎代码路径。

## 2. 每个页面用途

| 页面 | 路径 | 用途 |
|------|------|------|
| Dashboard | `/dashboard` | **交易员驾驶舱**：顶部 **交易日摘要 + 引擎徽章**，紧接着 **操作条**（**连接 / 刷新 TWS** `broker-snapshot-refresh --json`、`complete-trade-charts --fetch-missing-candles …`（只读拉线）、以及打开 Trades/Reports/Paper/诊断（链接））。其下分两栏含义：**券商真实状态**展示最近一次快照（账户净值 Net Liquidation、可用资金、购买力、现金与盈亏摘要；券商持仓/未完成单/近期成交计数等）；**本地引擎记录**为本仓库日志统计（提交/已发送/跳过/缺图等），与券商实况对照阅读。**刷新页面不会连 IBKR**，只有按钮/CLI 才写入 `data/runtime/broker_snapshot_last.json`。再往下是 **交易日记核心**（累计 R / USD equity gate / Edge / 数据质量）；再 **需要处理**（含未核对券商快照时）、**最新交易**、快捷入口；白名单表单/`launchd`/Today's safety / automatic engine 等在折叠 **「开发者与引擎诊断」**。 |
| Watchlist | `/watchlist` | ICT/SMC 研究用**股票池**（磁盘 JSON）；自选说明与重建按钮见下文 §2.1a |
| Signals (MTF) | `/signals` | 多周期 SMC/ICT 信号汇总（只读展示） |
| Paper Trading | `/paper` | 纸交易与自动纸策略**安全命令**入口（白名单）；含 **券商快照**摘要与「连接 / 刷新 TWS」（同 Dashboard，只读；GET 不自动连 IBKR） |
| **Trade Records（交易记录）** | **`/trades`** | **交易员视角**按笔复盘；**「本地状态 / 券商侧状态」**两列：**券商侧状态**依赖最近一次 **`broker_snapshot`** 文件——未点「连接 / 刷新 TWS」或未跑 CLI 时多为「尚未核对」。图表使用落盘在 `data/candles/.../1min/*.csv` 的 **1 分钟 K 线**（数据**来源于 IBKR**，经**只读**历史拉取、`fetch-candles`、或 **report-on-exit** 补档后本地保存）；**`/trades` 页面 GET 不连 IBKR**；无 exit 时「尚未记录平仓」，**不编造** |
| Journal（技术流水 / 审计） | `/journal` | 引擎 **JSONL 技术流水**与回测表格：可展开 **仓位/原始键**；如需「每笔一眼看完」请优先用 **`/trades`** |
| Strategies | `/strategies` | 策略注册表与多策略扫描入口 |
| Research | `/research` | 研究情报层报告与指令（只读 + 命令） |
| Backtest | `/backtest` | ICT/SMC 日内回测配置与运行（引擎在 CLI/Worker） |
| Edge | `/edge` | 标的级 edge 画像、排名与纸面门控（只读 + 白名单构建命令） |
| Reports | `/reports` | **交易日记分析**：**累计 R 曲线**为主要早期绩效曲线；按**平仓小时**的 R 表现与按**提交小时**的跳过决策分布**分开标注**（不混为一谈）；**美元资金曲线（USD Equity）**仅在**每笔已平仓**在 JSON 中都有可靠已实现美元盈亏时出现，否则界面会说明原因。**主报告台**另有日/周/研究/回测/edge/盘前/新闻汇总。缺平仓或缺本地 1 分钟 K 线表示**记录不齐**，不一定是交易或引擎故障。生成走白名单；**加载不连接 IBKR**，不主动拉 K 线 |
| Logs | `/logs` | 近期命令与事件（只读、脱敏） |
| Settings / Doctor | `/settings` | 安全说明、白名单命令、运行时标志、诊断类按钮；**本手册链接** |

### 2.1a Watchlist（自选股 JSON）

- **是什么**：仓库内 **`data/watchlists/*-dynamic-watchlist.json`** 描述「今日研究关注股票池」（来自 `config/watchlist.yaml` 的静态核心 + 若有 IBKR 日线则还有流动性/量比/波动等分层），供 ICT/SMC 扫描与回测篮子参考；**不是订单列表**。
- **打开页面不会报价**：`/watchlist` **GET 不连接 IBKR**。表格里的 **Latest price / Rel vol** 仅在 JSON **已写入**这些字段时出现（通常来自一次成功的 **`python3 -m bot.cli build-watchlist --ibkr`**）。
- **`source` 字段**：`static` 表示那次构建未拉 IBKR 日线 → 常为「—」；`ibkr` 表示那次已用 **只读日线** 计算指标。
- **Reason「static_core」**：表示该标的始终来自配置的 **固定核心**（大盘股/指数 ETF 等）；**并非错误**。若离线重建且仅用核心池，表格可能全部是 `static_core`，**Rebuild 仍可能「看起来没变」**，但命令仍成功、文件时间会更新。
- **显式刷新指标**：需要使用白名单 **`build-watchlist --ibkr …`**（UI 上与「用 IBKR 日线重建」按钮一致）；**无**市价单，**不改变**交易策略逻辑。
- **IBKR Error 326（client id 已被占用）**：若 **自动纸监督器 / 其它脚本** 已在用同一 `IBKR_CLIENT_ID` 连 TWS，再跑只读 **`build-watchlist --ibkr`** 可能报错；代码侧已对「只读会话」使用**独立默认 client id 段**并在冲突时**有限重试**。纸面引擎仍可保留环境里的**纸面引擎 client id**（常见为 `1`）；详见 `docs/troubleshooting.md` 专节。
- （若已启用中英 UI）中英文切换详见 §1.1；技术字段仍可混排英文。

### 2.1 人性化 UI（读法）

- 页面用语尽量用**普通人能读的中文/英文短句**（如「今日安全状态」「盘前简报」「1 分钟触发」），避免把**内部字段名**当成主标题。  
- 需要核对引擎细节时，见各页**技术字段**、JSON/路径说明或「Technical / 原始键」行。  
- **新闻、历史 edge 分数、关注列表**仅作**软提示/排序/说明**，**不会**因新闻自动发单；**有效 ICT/SMC 结构 + 1 分钟触发 + 纸面门控**仍是硬性条件。  

### 2.2 各页卡片在回答什么

| 区域 | 在问什么 |
|------|----------|
| **Dashboard** | **第一层**：交易日摘要 → **券商真实状态** vs **本地引擎记录** → **交易日记核心**（R 曲线 / USD equity / Edge）→ **需要处理** → **最新交易** → **快捷入口**。**券商账户净值等**来自上次 **连接 / 刷新 TWS**（或 CLI `broker-snapshot-refresh`）落盘的 JSON；**不会因 GET 页面自动连券商**。**第二层（折叠默认关）**：Today's safety / readiness、Report center、automatic engine 诊断等。**缺复盘图**：点 **「补齐交易图表」**（只读 IBKR 1m）；**不因打开 Dashboard 而自动 fetch** |
| **Research → Pre-Market Brief** | 最近一份盘前简报的**时间、 tone、各新闻源状态、邮件是否已发**；可用按钮**生成/邮件**（不连 TWS 直至你点受控命令） |
| **Signals（ICT）** | 每行是 **Ready to test / Watching / Blocked** 等口语状态 + 下一条件；**不是**交易指令 |
| **Paper** | **现在能否做纸面试**（文件侧门控 + 可选「不确定」= 缺信号/TWS 不可见等）、**风险上限**、**最近一笔纸单结果** |
| **Journal（技术流水）** | 引擎原始 **Sent / Skipped**、**括号是否完整**、**错码**（偏审计）；**交易复盘**请用 **`/trades`** |
| **Trade Records** | 每笔 **一行**、可读时间轴、**止损/目标/计划 R:R**、图表链接；过滤为**本机**行集 |
| **Reports** | **累计 R / 回撤**等日记分析（见上表）+ 磁盘上日周与盘前简报等索引；**生成**走白名单 CLI |

### 2.3 盘前简报（Pre-Market Brief）

- **作用**：在美股开盘前（建议 **美东 08:30**）生成**人类可读**的盘头摘要：大势 tone、宏观日程、关注标的财报/新闻、风险一句话等；**不触发任何交易**。  
- **生成（CLI）**：`python3 -m bot.cli premarket-brief --today`；`--latest` 查看最近一份；`--email` 若配置了发信且主题到 `ileonzh@gmail.com`（以 `config`/环境为准，缺凭证则**跳过、不崩**）。  
- **落盘**（默认 gitignore）：`data/premarket_briefs/`、可选 `data/news_cache/`。  
- **新闻源（可选，缺 API key 就跳过该源，不整份失败）**：环境变量名仅示例 — `BENZINGA_API_KEY`、`FINNHUB_API_KEY`、`FMP_API_KEY`、`POLYGON_API_KEY`；另有 IBKR News 等现有集成。  
- **调度**：若使用外部 crontab/launchd，**推荐** 在 **America/New_York 08:30** 运行上述带 `--email` 的命令；仓库内**不**默认开启后台循环任务。

### 2.4 多策略与「当前策略」

- **配置来源**：`config/strategy_ui.yaml`（可提交，无密钥）描述每个策略的 **展示名、是否可 scan/backtest/edge/paper、最终触发周期** 等。  
- **本机选择状态**：`data/runtime/selected_strategy.json`（**gitignore**，勿提交）保存四类默认：`active_scan_strategy`、`active_backtest_strategy`、`active_edge_strategy`、`active_paper_strategy`；未写文件时 UI 会按 **ict_smc_intraday_v1** 回退。  
- **在 UI 里改**：**策略控制中心** → `/strategies` 用表单保存；**Signals / Backtest / Edge / Paper** 页会显示当前选择的策略。  
- **为什么 ICT/SMC 用 1 分钟触发**：日内的最终入场在 **1 分钟** 上确认；高周期只做**背景与结构**，不替代 1m 条件。  
- **Edge / 新闻**：**不能单独触发下单**；仍要 **有效 ICT/SMC 结构 + 1 分钟触发 + 纸面门控**。  
- **Chanlun / ORB 等未来策略**：在列表中可见，但 **paper_enabled = false** 时，不能选为纸面策略；页面上有「未来 / 未就绪」类说明。  
- **新策略何时能纸面**：在代码里**独立声明**纸面不变量、接入扫描器/回测/括号与安全测试后，再把 `paper_enabled` 与相关开关打开（由开发流程决定，不在这里自动放开）。

### 2.5a Full Auto Paper Supervisor（全日纸面自动监督，**仅纸面**）

- **含义**：在 `run-automatic-paper-engine` **外层**再包一层 **监督器** —— 按美东时间等待 RTH 窗口、检查 TWS/API 门控、在**需要人工处理**时发 **Telegram 阻塞告警**（去重），门控全部通过后再启动**同一套** ICT/SMC 自动纸面引擎。**不会**启用实盘、**不会**使用市价单；新闻与 Edge **不会**代为下单。
- **CLI**：`python3 -m bot.cli run-full-auto-paper-supervisor --session full --telegram --report-on-exit`；干跑：`… --dry-run --json`。只读门控：`python3 -m bot.cli full-auto-paper-readiness --json`（可加 `--probe-ibkr` 探针 TWS/对账）。
- **脚本**：`scripts/run_full_auto_paper_supervisor.sh`（cd 到仓库根再执行上述命令）。可选 **launchd** 模板：`scripts/com.strategy-lab.full-auto-paper.plist.example` —— **不会**由仓库自动安装，需你手动复制/编辑路径后再 `launchctl load`。
- **Telegram**：仅在**阻塞**（如 TWS 未监听、对账失败、kill switch、策略非 ict_smc、预算为 0 等）或**真实引擎事件**（启动/停止、订单、括号不完整、券商拒单、日帽、EOD 摘要）时发送；**不发**「无新闻」、**不发**无信号心跳。
- **新闻**：监督器在盘前/RTH 相关时段可**最多每小时**调用一次与 `market-news-check` 相同的逻辑；仅当**达到市场波动分数且去重通过**才可能发 Telegram；缺 API key 则跳过、不崩溃。
- **UI**：Dashboard / Paper 的 **Full Auto Paper Supervisor** 卡片为**只读文件快照** + 白名单按钮（**Dry Run** / **Start**）；页面加载**不**建立 IBKR API 连接（TWS 端口是否开放以你上次监督器落盘或 CLI 为准）。
- **状态文件**（默认 gitignore）：`data/runtime/full_auto_paper_supervisor_state.json`；阻塞去重：`data/runtime/full_auto_blocker_dedup.json`。

#### 后台运行（macOS launchd，无需一直开 Cursor / Terminal）

- **目标**：安装后由 `launchd` 调用 **`~/Library/Application Support/StrategyLab/run_full_auto_paper_supervisor.sh`**（由安装脚本从 `scripts/strategy_lab_launchd_wrapper.sh` 复制），**不必**一直开着 Cursor；安装完成后也**不必**一直开着 Terminal。**TWS 纸面账户必须保持登录**，**Mac 建议禁止睡眠或使用常开机器**。若仓库在 **`~/Documents/...`**，macOS 可能对后台任务限制访问——安装脚本会提示；仍失败时请把仓库迁到如 **`~/StrategyLab/ibkr-trading-bot`** 再重装 launchd，或授予 **完全磁盘访问**（见 `docs/troubleshooting.md`）。  
- **稳定 CLI**（与干跑一致）：`python3 -m bot.cli full-auto-paper-readiness --json`；`python3 -m bot.cli run-full-auto-paper-supervisor --dry-run --json`。另：`automatic-paper-engine-readiness` / `run-automatic-paper-engine`。  
- **安装**（本机终端）：`bash scripts/install_full_auto_paper_launchd.sh`；可选 `STRATEGY_LAB_REPO_DIR=/path/to/ibkr-trading-bot bash scripts/install_full_auto_paper_launchd.sh`。  
- **状态**：`bash scripts/status_full_auto_paper_launchd.sh`（含 **Operation not permitted** 诊断、`~/Library/Logs/StrategyLab`、7497、readiness）  
- **卸载**：`bash scripts/uninstall_full_auto_paper_launchd.sh`（移除 plist 与 wrapper，**不删**仓库内 `data/` / 报告）  
- **日志**：优先看 **`~/Library/Logs/StrategyLab/`**（`full_auto_paper_supervisor.log`、`launchd_full_auto.out.log` / `.err.log`）；从终端直接跑仓库内 `scripts/run_full_auto_paper_supervisor.sh` 时仍可能写仓库 `logs/`。  
- **plist 模板**：`scripts/com.strategy-lab.full-auto-paper.plist`（**WorkingDirectory** 为 Application Support，**非** Documents）。**实盘仍禁用**。

### 2.5 自动纸面日内循环与 Automatic Paper Engine

- **底层循环**：`run-auto-paper-intraday-loop` 是在终端**长时间轮询**、反复调用与单次 `auto-paper-intraday-smc` 同一条执行链路的 **Worker/CLI 流程**；用于盘中自动重复「扫描/门控/尝试发纸面括号」。  
- **推荐人机可读别名（同一循环 + 更严预检 + Telegram 少打扰）**：`run-automatic-paper-engine` —— 要求 **$10k / $100k** 与 **ict_smc_intraday_v1** 等**自动引擎专用门槛**；**不**依赖旧版 `READY_FOR_PAPER_TEST` 人工关卡（该标记仍影响 `auto-loop-readiness` 的「60 分钟烟测准备」读数）。  
- **为什么仍保留 `run-auto-paper-intraday-loop` 名字**：向后兼容与脚本；新操作员优先记 **`run-automatic-paper-engine --session morning|full`**。  
- **UI 控制（Strategy Lab）**：**Dashboard / Paper** 的 **Automatic Paper Trading Engine** 区块提供：只读门控、**Check Automatic Engine Readiness**（`automatic-paper-engine-readiness`）、**Turn Paper Runtime ON/OFF**、**Start Morning / Full-Day Paper Engine**（白名单子命令 `run-automatic-paper-engine`，子进程有延长超时，仍建议大段跑盘用**终端**）。`run-auto-paper-intraday-loop` 本身**不**在 UI 白名单。  
- **旧读数**：**「Automatic paper loop readiness」** 卡片 + **Check Auto Loop Readiness**（`auto-loop-readiness`）仍反映含 `READY_FOR_PAPER_TEST` 的**另一套**准备度；与自动引擎门控**数值可能不一致**属预期。  
- **美东 RTH 参考**（`America/New_York`；与 `trading.intraday_paper` 的字符串一致）：**09:30** 开盘；**不早于** `no_new_entries_before`（默认 **09:45**）、**不晚于** `no_new_entries_after`（默认 **15:30**）才考虑新入；`exit_open_positions_at` 默认 **15:55** 附近减仓/收口；**16:00** 收盘；**16:05–16:30** 为人工或外部调度跑日报/邮件的常见窗口（**不是**本循环自动代跑，除非你另有调度）。  
- **如何检查是否可做烟测**：终端 `python3 -m bot.cli auto-loop-readiness` 或 `… --json`；Dashboard / Paper 上同一按钮。输出含 `readiness`、`next_safe_action`（如 `ready_for_60min_smoke`、`wait_for_daily_budget`、`kill_switch_active`、`fix_reconcile` 等）。`--probe-ibkr` 可选、默认关闭；只有显式加才会走对账/探针。  
- **收市后报告（预期工程能力，不由此 prompt 代跑）**：停循环后，你可以按 `docs/daily-operation-checklist.md` 顺序在终端跑：`open-orders`、`portfolio`、`paper-reconcile`、`paper-daily-report --email`（以环境是否配置发信为准）。

#### Forward test（前向 / 纸面验证）与回测的区别

- **回测（Backtest）**：在历史 K 线上重放策略逻辑，**不下单**、不经过 TWS；用于研究 R 分布与参数，**不是**「明天会不会这样走」的保证。  
- **Forward test / 纸面 forward test**：在**真实行情时间**用**纸面账户**走与生产尽量一致的链路（扫描 → ICT/SMC 门控 → 1 分钟触发 → 限价括号 + 止损/目标），**仍无实盘**。目的为检验**实现、延迟、括号完整性、日志与帽位**，而不是回测收益。  
- **共同前提**：`active_paper_strategy = ict_smc_intraday_v1`；**新闻与 Edge 不能单独创造可交易资格**；**禁止市价单**；**日帽 / 笔帽**见 `trading.intraday_paper`。

#### 美东早晨纸面窗口（计划中的烟测，**不**在 UI 启动循环）

- **参考时间**（`America/New_York`）：市场 **09:30** 开盘；**不早于 09:45** 起算新单；**早晨专项窗口 09:45–11:30** 用于未来「上午段」自动纸循环烟测；全日循环仍可用默认 **09:45–15:30**（以 `intraday_paper` 配置为准）。  
- **CLI**：`python3 -m bot.cli run-automatic-paper-engine --session morning --telegram --report-on-exit`（也支持 `--session full`）。等价旧命令：`run-auto-paper-intraday-loop`（**无**自动引擎的专用预检与 TG 行为）。**干跑**（不启 runtime、不循环）：`--dry-run --json`。  
- **只读检查**：Dashboard / Paper 的 **Morning paper test readiness** 与 **Check Morning Paper Readiness**（与 `auto-loop-readiness` 同源 JSON）含 `morning_next_safe_action`（如 `ready_for_morning_smoke`、`wait_for_market_open`、`wait_for_daily_budget` 等）。  
- **为何先早晨、后全日**：早晨段流量与窗口更短，适合验证括号与日志；通过后再计划更长时烟测。  

#### 收市后核查清单（只读命令序列）

- 在终端可打印推荐顺序（**本身不下单、不发邮件**）：`python3 -m bot.cli eod-paper-checklist`  
- 实跑券商侧时（TWS 纸面已登入）：`open-orders` → `portfolio` → `paper-reconcile` → `paper-daily-report --latest --email`（邮件依赖 Resend 等环境；缺凭证为 `skipped_missing_credentials`，不崩溃）。

### 2.6 回测与 1 分钟 K 线缓存（周末/多标的必读）

- **会占磁盘、会积少成多吗**：会。`fetch-candles` / 一键回测里拉取的历史会写在 **`data/candles/`** 下，重复回测同一区间时**不必**反复拉。该目录在 **`.gitignore`** 中，**不会**随 `git push` 上传到 GitHub。回测产出的 **JSON/CSV/MD/PNG** 在 **`data/backtests/`**（含 `intraday/charts/`）同样是本地回顾文件，可按需清理旧时间戳；**`data/paper_orders/`** 与 **`data/runtime/`** 等为审计/运行态，**勿随意删**。在 **Backtest** 页有简要说明；**Settings** 与 **Reports** 的「Data on disk」表（页面加载时快照）和 **`python3 -m bot.cli data-status`** 可查看各类目录占用的字节数。  
- **为什么需要缓存**：`backtest-intraday-smc` / `backtest-intraday-smc-watchlist` 只读 **`data/candles/{标的}/1min/{YYYY-MM-DD}.csv`**（与 `fetch-candles` 写入布局一致），**不**在回测时自动向 IBKR 拉线。某标的在区间内**无文件**时引擎**跳过**该标的，多标的回测会看起来像「只跑了一两只」。  
- **如何先看缺口**：在 **Backtest** 页用 **Check Data Coverage**（白名单命令 `candle-coverage`），或终端 `python3 -m bot.cli candle-coverage --core-basket --start YYYY-MM-DD --end YYYY-MM-DD` / `--symbols AAPL,CRM` / `--watchlist latest`。该检查**只读本地文件**，不连 TWS。  
- **Ready / Partial / Missing**：在请求区间内的**美东周一日历**上（节假日未剔除，见报告备注），**每天**有非空 1m CSV 为 *Ready*；**部分**日期有文件为 *Partial*；**几乎无数据**为 *Missing*。  
- **何时点 Fetch missing candles**：仅在你在 **Backtest** 页点击 **Fetch missing candles from IBKR**（`fetch-candles`）**且 TWS/网关已开**时，才会向券商拉**只读**历史；**每提交一次通常填一个标的+区间**；回测**不会**替你自动 fetch。  
- **推荐流程（周末回测）**：打开 **Backtest** → 选标的来源与日期（或 `candle-coverage`）→ **Check Data Coverage** → 对缺口标的再 **Fetch**（需时）→ 再 **Check** → **Run backtest** → 看 **backtest-report** / 再 **Build edge profile**。
- **一键流程（新手向）**：**Fetch Missing Data & Run Backtest** 会先做与 `candle-coverage` 相同的**本地**检查；仅有缺口时才用只读历史向 IBKR 补 1m（需 TWS/网关在线），最后跑 `ict_smc_intraday_v1` 回测。可用复选框 **Allow partial**：TWS 未开或仍有缺口时，只在有缓存的标的上回测；不勾选则缺口未补满时**不跑回测**并说明原因。CLI：`python3 -m bot.cli backtest-oneclick --symbols CRM --start YYYY-MM-DD --end YYYY-MM-DD --strategy ict_smc_intraday_v1 --mode strict_and_aggressive --direction both`。该流程**从不下单**。

**周末一键回测（UI）**

1. 打开 **Backtest**。  
2. 选 **Single ticker / Core basket / Latest watchlist / Custom** 与日期区间。  
3. 点 **Fetch Missing Data & Run Backtest**（或按上节分步按钮）。  
4. 看结果里已拉取的标的、失败项、实际参与回测与跳过的标的；是否 **Complete**。  
5. 阅读 **backtest-report** / 最新 summary。  
6. 到 **Edge** 做 **Build Edge Profile**。

### 2.7 报告工作流：UI 优先，Telegram 短讯，邮件可选

- **主路径**：在 **`/reports`** 阅读汇总、生成报告、看路径与摘要；**`data/reports/**` 等为本地文件（git 忽略大产物）**，不依赖邮件是否成功。  
- **Telegram**：仅适合**短提醒**（如部分 digest、达标重大新闻摘要）。  
- **Email（Resend）**：**可选**、可晚配；未配置或失败时，**仍可在 UI 与磁盘读完整内容**。  
- 纸面收工后：先 **Reports / Journal / Paper** 检查，再视需要发邮件。  

| 内容 | 典型 Telegram | 典型 Email / 全文 | 仅 UI 文件 |
|------|----------------|-------------------|------------|
| 盘前简报 | 可 `--telegram` 短讯 | 可 `--email`；缺凭证不崩 | 本地 JSON/MD |
| 纸面日/周报告 | 已有中文短 digest（`paper-daily-report --telegram`） | Resend + `REPORT_*` 环境；`ileonzh@gmail.com` 在 `config/settings.yaml` 的 `reports.email_to` | `data/reports/paper/` |
| 重大市场新闻（heuristic） | 仅**达标且未去重**时一条 HTML 摘要 | 默认关（`news_reporting.email_enabled: false`） | `market-news-check` 的 JSON 状态在 `data/runtime/` |
| 回测 / Edge | 不在此自动狂发；可选手动 `--telegram` | 可选、非默认 | 报告路径在 **Reports** 页 |

- **不刷屏**：`news_reporting.send_no_news_messages: false` → **没有可发送的重大新闻时，Telegram 不发任何「无新闻」消息**。  
- **去重**：`data/runtime/telegram_report_dedup.json`（由 CLI 维护；目录已 gitignore）。  
- **读就绪**：`python3 -m bot.cli news-monitor-readiness --json`（不拉第三方）。  
- **手动查新闻（Finnhub/FMP，env key）**：`python3 -m bot.cli market-news-check --core-basket --market-moving-only --dry-run`；默认 **dry-run 不发 Telegram**；需发送时用 `--no-dry-run` 且配置 `TELEGRAM_*`。  
- **新闻不触发交易**：与盘前/研究相同，**不能**替代 ICT/SMC + 1 分钟触发。  
- **环境变量名（不写入仓库）**：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`RESEND_API_KEY`、`REPORT_EMAIL_FROM`、`REPORT_EMAIL_TO`、以及各新闻 REST：`FINNHUB_API_KEY`、`FMP_API_KEY`、`BENZINGA_API_KEY`、`POLYGON_API_KEY`。

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

## 12. Journal、Trade Review 与本地复盘图

- **Journal**（`/journal`）聚合 `data/paper_orders/*-intraday-paper-orders.jsonl` 与最新回测 trades CSV（见 state store），**只读**；**不负责下单**，也没有市场单按钮。加载页面**不会**连接 IBKR。  
- **主表**：面向阅读的列包括时间、标的、多空、模式（strict/aggressive）、发送/跳过/部分状态、入场/止损/目标价、计划 R:R、数量、名义金额、括号保护是否完整、ICT 链（HTF/5m/1m）、Edge 分数/推荐模式、**可读**跳过原因，以及 **Review** 链接。工程化字段（sizing 细节、minTick、原始 E/SL/TP、订单号、完整 JSON 等）收在各行 **Details** 折叠里，原文不丢。  
- **Sent / Skipped / Protection incomplete**：分别对应「已提交或部分到 TWS」「有 skipped 原因」「`bracket_integrity`≠complete」。不完整保护在列表行与复盘页上会**显眼提示**。  
- **Trade Review**（`/journal/trade/<trade_id>`）：每条 JSONL 行有稳定 **`trade_id`**（由时间、标的、方向、条目、跳过原因等派生哈希）。复盘页列出身份、价位、风险/每份股、盈亏比、ICT 链、Edge、保护与订单号、可读跳过原因（原文仍在 Details）；含到 Journal / Reports / Paper 的导航。  
- **图表与 K 线来源**：业务上 **K 线原始数据来自 IBKR**；落盘后在本地 **`data/candles/<SYMBOL>/1min/<YYYY-MM-DD>.csv`**（gitignored），再由 **`data/reports/trade_charts/<trade_id>.png`** 展示。**从不**在 Strategy Lab 内「**普通页面一打开**就自动向 IBKR 拉 K 线」。当本机已有对应 **NY 日 1m CSV** 时：**（1）** 打开 **Journal** 时在限定条数内**尝试**从本地缓存生成 PNG（不连券商）；**（2）** 纸面引擎 **`--report-on-exit`** 在写完纸面日报 JSON 后，会跑 **`complete_trade_charts`**：**默认** `trading.trade_charts.fetch_missing_candles_on_report_exit` 为 **true**——对**已发生交易**但缺当天本地 1m 的标的，用 **只读** `reqHistoricalData`（独立 **candles** client id 段）补齐本地缓存，再生成 trade chart；单个标的拉取失败会**软失败**不打断日报。**若不想**在 EOD 自动连 IBKR，在 `config/settings.local.yaml` 将该项设为 **false**。窗口长度见 `candle_window_before_minutes` / `candle_window_after_minutes`（影响 PNG 绘图窗口，与 `generate_trade_journal_chart_png` 一致）。
- **Tradervue 式补图管线**：**`/trades`** 与 **`/reports`** 白名单按钮 **Complete trade charts**；CLI：  
  `python3 -m bot.cli complete-trade-charts --latest --limit 50 [--local-only | --fetch-missing-candles] [--window-before-minutes N] [--window-after-minutes N] [--dry-run] [--json]`（别名 `tradervue-complete-charts` / `complete-journal-charts`）。**`--local-only`**：绝不连 IBKR，只用已有 `data/candles/`。**`--fetch-missing-candles`**：缺日文件时只读拉 1m（roster **`candles`**）再出 PNG。以上均为**显式** CLI/按钮/EOD 流程，**不是浏览器 GET**。  
  另可：`generate-trade-chart` / `generate-trade-charts --latest`（**不**自带缺日 IBKR fetch）。单笔：`generate-trade-chart`（同 `journal-generate-trade-chart`）`--trade-id <id> ...`。  

  **Past trades**：历史上缺的图可在以后**先有本地缓存**后再补——不是「交易失败」，只是当时磁盘上还没有该日 **`1min` CSV**。**Exit**：若 JSONL **未同时**记下 `exit_time` 与 `exit_price`，图与界面会标示 **Exit not recorded** /「尚未记录平仓」，**不从止损/止盈价推测离场**。  
**Journal 图表列**会用「图表可用 / 缺少K线 / 等待中 / —」等标明原因。  
- **筛选**：可按 Sent / Skipped / 保护不完整、多空、标的、**有/无已生成复盘图**、仅今日（NY）、**上一 NY 交易日** 等筛选，不改变引擎逻辑。

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
- **TWS 健康（限流）**：当本机 TWS/网关不可达、会话异常或（可选）对账探针失败时，可经 `trading.tws_health_alerts` 发送 **【TWS 警报】**；恢复后可选 **【TWS 恢复】**。同一告警代码默认 **15 分钟内不重复**。CLI：`python3 -m bot.cli tws-health-alert-check`；集成于 **full-auto supervisor**（美东日盘相关窗口）与 **`broker-snapshot-refresh`**。状态文件：`data/runtime/tws_health_alert_state.json`。Dashboard / Paper 页显示上次代码与时间（不含密钥）。  
- **入站命令**（在聊天里发 `/status`、`/news` 等并收到回复）与**通知推送**是两件不同的事：推送只要配置了 `TELEGRAM_*` 就会发；**要收到命令回复**，本机需运行 **`python3 -m bot.cli telegram-command-listener`**（或安装 `scripts/install_telegram_command_listener_launchd.sh` 的 launchd 任务）以轮询 `getUpdates`。**Settings** 页有只读状态说明。详见 `docs/telegram-commands.md`。
- **不是**远程下单通道；不替代 Kill Switch 与对账。Telegram 的 `/stop`、`/kill` **不会**在磁盘上写 KILL 文件，请用 UI 或终端。

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
