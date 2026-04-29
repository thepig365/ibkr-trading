# 故障排除（Strategy Lab 本地，简体中文）

> 安全提示：不要在此粘贴 **API 密钥、Token、账户号**。  
> 若问题与券商连接有关，先确认**纸面**端口与 `IBKR_CLIENT_ID` 未冲突。

---

---

## 本地「已提交」与 TWS 成交 / R 曲线不齐

- **第一层**：先有 **broker snapshot**（`broker-snapshot-refresh`）核对券商持仓与委托。  
- **第二层**：用 **`reconcile-fills`**（只读 executions，`broker_readonly`）把真实成交对齐到 Strategy Lab 本地委托行；写入 `data/runtime/fills_reconciliation_last.json`（默认 gitignore）。**不**代为推断平仓价。手册见 `docs/strategy-lab-user-manual.md` §1.2。

---

## Dashboard：本地有「已提交记录」但快照里券商持仓为零

- **正常可能原因**：订单未成交、已撤销/过期，或你**尚未**运行过一次 **Connect / Refresh TWS** / `broker-snapshot-refresh`，快照仍是旧的或不存在。  
- **处理**：纸面 TWS 已开且 API 端口正确时，在 **Dashboard**（或 **Paper**）点 **「连接 / 刷新 TWS」**，或终端：  
  `python3 -m bot.cli broker-snapshot-refresh --json`  
  该命令只读拉 positions / open orders / fills，**不下单**；使用 **`broker_readonly`** client id 段（见 `bot/ibkr_client_ids.py`），与纸面引擎订单 client id 分离。  
- **为何各页不各自连 TWS**：避免每打开一个页面就建新 API 会话（易 326 冲突、卡顿）。统一由你**显式刷新**生成一份本地 JSON，各页只读文件。

---

- **数据来源**：1m K 线来自 **IBKR 历史**（或你曾用 `fetch-candles` 等方式写入），**存盘**在 `data/candles/<SYMBOL>/1min/<DATE>.csv`，再用于生成 `data/reports/trade_charts/<trade_id>.png`。**打开 `/trades`（GET）不会自动连 IBKR**。若该交易日磁盘上**尚无**该 CSV，列表会显示缺少 K 线/尚无图——可先等 **纸面引擎 `--report-on-exit`**（默认会尝试只读补齐缺失日后再出图，失败则软跳过），或在 **`/trades` / `/reports`** 点 **Complete trade charts**，或 CLI `complete-trade-charts --fetch-missing-candles`。  
- **Past trades**：日后只要补齐对应 **NY 日** 的 `1min` CSV，仍可再生成 PNG。  
- **Exit 未显示**：JSONL 里若没有同时记录 **exit_time** 与 **exit_price**，界面会写 **「尚未记录平仓」**，**不会**推算或补全离场价。  
- **与 Journal 区别**：`/journal` 为 **技术/审计流水**；`/trades` 为 **交易员友好**按笔视图。
- **Completing charts（`complete-trade-charts`）**：**`--local-only`** 只读本地 `data/candles/`，不连 IBKR。加 **`--fetch-missing-candles`** 时才会用只读历史 API 写缺失日 `data/candles/.../1min/*.csv`（roster **`candles`**），**仍不下单**。纸上引擎 **`--report-on-exit`**：若 `trading.trade_charts.fetch_missing_candles_on_report_exit` 为 **true**（**跟踪默认 `config/settings.yaml`**），会在日报落盘后尝试自动补齐；设为 **false** 则 EOD 只做本地已有缓存的出图（可在 `settings.local.yaml` 覆盖）。

## IBKR Error 326 · client id already in use（API 会话 ID 冲突）

- **现象**：控制台或 Recent commands 报错含 **326**、`client id is already in use`、`already in use`，或紧随其后出现 **`TimeoutError()`**——本质往往是 **上一次 API 会话仍占用同一 `(host, port, clientId)`**。  
- **原因**：Interactive Brokers **同一时刻只允许一个会话**使用该三元组；若 **纸面自动化监督器 / 另一个 bot 进程 / 上一轮未断开的脚本**已在用 **`IBKR_CLIENT_ID`（常见为 1）**，再跑一次 **只做只读的 CLI**（如 `build-watchlist --ibkr`）若仍去抢同一个 ID，就会失败。**这不是交易策略错误**，也不是隐含下单失败。  
- **本仓库处理**：Strategy Lab **只读** CLI（看盘、拉缓存 K 线、`build-watchlist --ibkr`、部分 fetch）在进程内会为不同用途路由到 **独立默认 client id 段**（见 `bot/ibkr_client_ids.py`），并对「类 326」的占用提示在**只读连接**上做 **至多 3 次顺延重试**。**长期跑的纸引擎/下单链**仍可继续使用环境中的默认纸面会话 ID（通常仍是 **1**，由 `.env`/配置决定）；两条线尽量**互不抢同一个 ID**。  
- **自检**：同一时间少开并行监听；结束前序 CLI/Telegram/listener；确认 TWS 未卡死会话。仍可手动调整 `IBKR_CLIENT_ID`，但需保证**全局不冲突**。

---

## TWS 弹窗：「需要访问公网 api.ibkr.com，但当前网络已阻止」

- **和本机 Socket API 不是一回事**：本 bot 的常规路径是 **`127.0.0.1:7497`（或你配置的 `IBKR_HOST`/`IBKR_PORT`）** 上运行的 TWS/IB Gateway，经 **TWS API（ib_async / ib_insync）** 通信。这**不依赖**你在浏览器里访问 `https://api.ibkr.com`。  
- **弹窗更常关联网页 / Client Portal / 部分 TWS 内嵌功能**：若公司网络或防火墙 **拦截** 对 `api.ibkr.com` 的 **HTTPS**，TWS 里某些**需要连公网 IBKR 主机**的菜单或登录辅助功能会提示；**不一定**影响本地 7497 的 API。  
- **自测本机 API 是否仍可用**（只读、不下单）：
  1. `lsof -i :7497` 或看 TWS 是否 **Listen** 在预期端口。  
  2. `python3 -m bot.cli ibkr-session-status` 应显示 `connected: true`、**paper** 账户。  
  3. 再试 `open-orders`、`portfolio`、`paper-reconcile`。  
- **自测公网是否被拦**（不提交凭证）：在终端对 `https://api.ibkr.com` 做 `curl -I --max-time 10` 或 `socket.getaddrinfo("api.ibkr.com", 443)`。若 DNS/HTTPS 失败，弹窗**可能属实**，需网络/DNS/防火墙/代理放行。  
- **历史 K 经本地 API**：`fetch-candles --ibkr` 走 TWS 的 `reqHistoricalData`；**一般仍走 7497**。若拉历史失败，更常见是 **无历史行情订阅/权限、162/354 等信息码、时段无数据、节流**，**不一定**是 `api.ibkr.com` 被拦。以 CLI 与 `ibkr_session_status` 的报错/日志为准。  
- **下单/路由**：纸面/实盘下单仍经 **TWS/网关**；与「本机 7497 是否连通」直接相关。若仅公网被拦、本地 API 正常，**策略里的下单路径通常仍可工作**（以你环境实测为准）。  

---

## 想看的报告在邮件里，但邮件没发 / Resend 未配

- **正常**：**`/reports` 是主报告台**；日/周纸面报告、路径与摘要**不依赖**邮件。配置好 Resend 后 `--email` 才送邮箱；未配时**在 UI 与 `data/reports/paper/` 仍有全文**。  
- Telegram 仅为**短讯**；完整回顾用 **Reports、Journal、Paper**。

## Telegram 能收到引擎推送，但发 `/status` 没有回复

- **原因**：**推送**用 `sendMessage`；**入站命令**需要某个进程在跑 **`getUpdates`** 长轮询。若未启动 `telegram-command-listener`（或相应 launchd），Telegram 上的命令不会有回复。  
- **处理**：终端前台 `python3 -m bot.cli telegram-command-listener` 或 `bash scripts/run_telegram_command_listener.sh`；常开可走 **`bash scripts/install_telegram_command_listener_launchd.sh`**。  
- **自查**：`python3 -m bot.cli telegram-command-listener --dry-run --json`（不联网，只看本地 `data/runtime/telegram_command_listener_state.json`）；`bash scripts/status_telegram_command_listener_launchd.sh`。  
- **安全**：只处理 `config/telegram.yaml` 里 `allowed_chat_ids`（常解析为 `TELEGRAM_CHAT_ID`）的聊天。详见 `docs/telegram-commands.md`。

## 完全用 UI 时 TWS 连不上

1. 在 **Dashboard** 用 **IBKR Session Status** 看输出（**显式点按钮**后才会走 CLI）。  
2. 本机 TWS/网关是否登录**纸面**、API 是否启用。  
3. 端口/Client ID 是否与 `config` 与 `docs/ibkr-setup.md` 一致。  
4. 仍失败：在终端用 `strategy_lab_doctor.sh` 或同文档排查；**不要**在 UI 里关 TWS 的 Order 保护。

## launchd：`Operation not permitted` / `getcwd: cannot access parent directories`

- **原因**：仓库若在 **`~/Documents`**、Desktop 或与 iCloud 同步的目录，macOS **隐私（TCC）** 可能禁止 **launchd 后台任务** 在该路径下**执行脚本**或把 **WorkingDirectory** 设在该路径，从而出现 `Operation not permitted`，或 `shell-init: getcwd` 报错。这与 **交易引擎本身**无关；在 **终端**里手动跑通常仍正常。  
- **本仓库已做的安装侧修复**：`install_full_auto_paper_launchd.sh` 会把 **wrapper** 装到 `~/Library/Application Support/StrategyLab/run_full_auto_paper_supervisor.sh`，plist 的 **WorkingDirectory** 也在该目录；通过环境变量 **`STRATEGY_LAB_REPO_DIR`** + **`PYTHONPATH`/`IBKR_TRADING_PROJECT_ROOT`** 调用 `python3 -m bot.cli`，并把 **launchd 的 stdout/stderr** 写到 **`~/Library/Logs/StrategyLab/`**，避免强依赖对 Documents 下路径的 `exec`。  
- **若仍失败**：把仓库移到非受保护路径，例如 **`~/StrategyLab/ibkr-trading-bot`**，然后 `export STRATEGY_LAB_REPO_DIR="$HOME/StrategyLab/ibkr-trading-bot"` 再执行 **install**；或在 **系统设置 → 隐私与安全性 → 完全磁盘访问权限** 中为 `/bin/bash`、实际使用的 **`python3`**（或 `.venv` 内解释器）授权。  
- **重装 launchd**：`bash scripts/uninstall_full_auto_paper_launchd.sh` 后 `bash scripts/install_full_auto_paper_launchd.sh`。

## launchd 装了但似乎没有跑 / 日志为空

- **先查**：`bash scripts/status_full_auto_paper_launchd.sh`；重点看 **`~/Library/Logs/StrategyLab/`** 下 `launchd_full_auto.err.log`、`full_auto_paper_supervisor.log`，以及仓库内 `logs/`（若仍写入）。  
- **确认 TWS 纸面 + API 端口** 与 `IBKR_PORT` 一致；监督器在阻塞时会尝试 Telegram（若已配置），**不**在 UI 里执行 `launchctl`。  
- **单实例锁**：若已有一份监督器在跑，新实例会**跳过**并在 supervisor 日志里写 `skip: another instance holds the lock`（防重复进程）。

## Full Auto Paper Supervisor：未开 TWS / 一直 blocked

- **若已配置 Telegram**：监督器在**平日美东约 08:30–16:30** 且门控失败时，可能发一条**去重后的阻塞告警**（例如 TWS 未在配置端口监听）；**不会**替你在本机启动 TWS。  
- **自查**：`python3 -m bot.cli full-auto-paper-readiness --json`；需探针时加 `--probe-ibkr`（会连 TWS API）。  
- **干跑**（不写单、不启内层循环）：`python3 -m bot.cli run-full-auto-paper-supervisor --dry-run --json`。  
- **状态文件**：`data/runtime/full_auto_paper_supervisor_state.json`（若存在）可在 **Reports** 页看到摘要；完整报告仍以 **Reports / Journal** 为主。

## 在 /strategies 里选不了某策略的「纸面」

- 只有 `config/strategy_ui.yaml` 里标注 **`paper_enabled: true`** 的模型才允许写入 **Set paper strategy**；当前为 **ICT/SMC Intraday**。  
- **Chanlun / ORB** 等占位策略会显示为 **未就绪 / 未来开发**；这是预期，**不是** bug。  
- 清理错误的手改文件可删除本机 `data/runtime/selected_strategy.json`，下次会回退为默认 **ict_smc_intraday_v1**。

## 盘前简报发不出 / 没有邮件

- **没有配置 SMTP 等邮件环境**：`email_status` 会显示**跳过/缺凭证**，简报文件仍可生成 — **属预期**，不是崩溃。  
- **没有第三方新闻 key**：相应 provider 为 **skipped_missing_credentials**，整份仍可用其它源/占位 — **不**需要为此改交易安全边界。  
- 确认命令：`python3 -m bot.cli premarket-brief --latest` 能读到 `data/premarket_briefs/` 下最近 JSON。  
- **再强调**：盘前内容**不触发**发单；若误以为是「交易信号」——仍以 **ICT/SMC + 1m 触发** 与 Journal/门控 为准。  

## Resend 显示「未配置」但 `.env` 已改

- 运行 `python3 -m bot.cli email-config-status --json`（**只输出布尔与缺项名**）与 `news-monitor-readiness --json`：看 `missing_fields`（如 `RESEND_API_KEY`、`REPORT_EMAIL_FROM`、或收件人）。  
- 收件人可由 **`reports.email_to`**（`config/settings.yaml`）满足，不必再设 `REPORT_EMAIL_TO`。  
- `.env` 必须从**仓库根**加载；若 `dotenv_load_warning` 非空，说明解析阶段出错（类型名见输出，**不含**密钥原文）。  
- Gmail 作发件人需在 Resend 控制台完成域名验证；`from_address_may_need_resend_verification` 为提示，**不**等于配置无效。  
- Resend **HTTP 403** 且响应里含 **1010**：常见为缺 **User-Agent** 或访问策略；本仓库请求已带 `User-Agent: StrategyLab/1.0 ...`。若仍 403/1010，查 Resend 控制台与网络策略。

## 多标的回测只跑了一两只（其余被跳过）

- **原因**：`backtest-intraday-smc-watchlist` 对每个标的只读**本地** `data/candles/{SYMBOL}/1min/*.csv`；**没有 1m 文件就跳过**该标的，不会自动联网拉取。  
- **诊断**：`python3 -m bot.cli candle-coverage --core-basket --start YYYY-MM-DD --end YYYY-MM-DD` 或在 **Backtest** 点 **Check Data Coverage**（只读盘，不连 IBKR）。看 **Ready / Partial / Missing**。  
- **补数据**：在 **Backtest** 用 **Fetch missing candles from IBKR**（每通常一次一个标的+区间），TWS/网关需在线；**不要**以为「点了回测就会下载全市场」。  
- **一键**：**Fetch Missing Data & Run Backtest** 会先检查本地，再按需拉 1m（需 TWS），最后回测；仍**不下单**。若 TWS 未开且未勾选 **Allow partial**，可能在缺口未补时**不跑回测**并提示。  
- 详见 `docs/strategy-lab-user-manual.md` **§2.6**。

## 有信号 / 流程正常，但「没有下单」或 Paper pass 全跳过

1. 在 **Paper** 页看 **Intraday** 卡片区 **skipped** 原因（如 `trading.intraday_paper.enabled=false`、未开 runtime、对账、Kill、时段外、无 READY 等）。  
2. 运行 `python3 -m bot.cli intraday-paper-status --json` 看结构化输出（只读、不连 IBKR）。若在做 **13I 纸前测**，先看 `python3 -m bot.cli paper-activation-status`，确认 `settings.local`、runtime 与 `final_readiness`。  
3. 读 **`data/runtime/intraday_auto_paper_loop_state.json`** 里 `last_reason` / `skipped_reasons`（**勿**当作文档提交到 git）。  
4. 确认 `paper-reconcile` 为 **PASS**（若你期望通过券商下纸单）。  
5. 研究层 **`auto_paper_allowed`** 为否**不一定**表示执行被挡：先查是否有 **per-symbol hard block**；宏观/VIX 多为软提醒（见 `docs/strategy-lab-user-manual.md` §6.2）。  
6. 若已启用 **edge 门控**（`edge_profile_enabled`），在当次 `auto-paper-intraday` / Journal 的 `edge_audit` 中查看：`edge_profile_missing`、`edge_recommended_mode_watch_only`、`edge_recommended_mode_disabled`、`edge_strict_only_blocks_aggressive`、`edge_risk_multiplier_applied`。无 `data/edge_profiles/` 时 aggressive 可能默认被挡——先跑 `build-edge-profiles` 或只试 STRICT 小风险（见 `unknown_edge_policy`）。  

## 准备「自动纸日内循环 / Automatic Paper Engine」前状态不对

1. **自动引擎**（`run-automatic-paper-engine`）专用只读门控：`python3 -m bot.cli automatic-paper-engine-readiness --json`（可选 `--probe-ibkr`）；**不**要求 `READY_FOR_PAPER_TEST`，但要求 **$10k/$100k 帽**、**ict_smc_intraday_v1**、`intraday_paper.enabled`、纸账户、Kill 关等（见 `bot/automatic_paper_preflight.py`）。  
2. **旧版 60 分钟烟测读数**（与引擎可能不一致）：`python3 -m bot.cli auto-loop-readiness`；Dashboard 的 **Check Auto Loop Readiness** 同源；`paper_activation` **非 READY** 时此处常为 **Not ready**，但**不**一定阻止自动引擎（若 1 的门控全绿）。  
3. 默认**不加** `--probe-ibkr`（不连 TWS）；需要券商对账结果时再加。  
4. 在 **Dashboard / Paper** 的 **Automatic Paper Trading Engine** 可点 **Start Morning / Full-Day**（白名单子命令，子进程 8h 内超时）；`run-auto-paper-intraday-loop` 仍**不**在 UI 白名单。  
5. 收工后：`--report-on-exit` 会写**本地**日报告；`paper-daily-report --email` 见清单。干跑：`run-automatic-paper-engine --dry-run --json`。

## Morning paper readiness 一直 Not ready

- **正常原因**：**周末 / 美东 09:45 前** → `wait_for_market_open`；**11:30 后** 早晨窗口已结束；**日预算 0**；**Kill**；**对账未过**；**无当日 intraday 扫描文件**（`no_recent_scan`）。  
- **与全日烟测一样**：`active_paper_strategy` 须为 **ICT/SMC 且 paper_enabled**；`paper-activation` 的 `final_readiness` 应为 **READY_FOR_PAPER_TEST**（见 Paper 页说明）。  
- **UI 只有「Check Morning Paper Readiness」**，没有开始循环的按钮；完整 CLI 名见 `docs/strategy-lab-user-manual.md`（**不要**在 HTML 里搜索该字符串，页面可能不展示全名以防误点）。

## EOD 报告 / 邮件

- **推荐步骤打印**：`python3 -m bot.cli eod-paper-checklist`（只读）。  
- **缺邮件凭证**：`paper-daily-report --email` 会 **跳过发信、不崩**；`report_email_status` 常见为 `skipped_missing_credentials`。

## Telegram 未收到市场新闻

- **先干跑**：`python3 -m bot.cli market-news-check --core-basket --market-moving-only --dry-run --json` 看 `providers` 是否为 `skipped_missing_credentials`（未配 Finnhub/FMP 等 key）。  
- **无达标标题**：`items_scored` 可能 >0 但 `best` 低于 `min_market_moving_score`（默认 70）— **不发送、非故障**。  
- **去重**：同标题在 24h 内第二次 → `telegram_status` = `skipped_duplicate`。  
- **勿期待「无新闻」提示**：`send_no_news_messages` 为 false 时，无合格新闻 = **完全静默**。

---

## UI 起不来

0. **macOS**：双击打 `Start Strategy Lab.command` 若被系统拦截、或提示无法执行，见 `docs/mac-launchers.md`（`chmod +x`、右键「打开」等）。  
1. 看日志：`logs/strategy-lab-ui.stdout.log` 与 `logs/strategy-lab-ui.stderr.log`。  
2. 确认 venv 已 `pip install -r requirements.txt`，且 `python3 -c "import uvicorn, fastapi"` 正常。  
3. 用 `./scripts/strategy_lab_doctor.sh` 看 Python/依赖/gitignore。  
4. 若前台可跑：`python3 -m bot_ui` 对比与后台 `start_strategy_lab_ui.sh` 差异。

---

## 端口被占用

- 错误里若出现 `Address already in use`：另进程占用 **8765**。  
- `lsof -i :8765` 查 PID 后结束该进程，或设 `STRATEGY_LAB_PORT` 为其他端口**并**在启动 `bot_ui` 时一致。  
- 确认只监听 **127.0.0.1**，不要改为 `0.0.0.0` 除非你知道风险。

---

## TWS 未连接 / 命令报连接错误

- 确认 TWS/网关已登录**纸面**，API 已启用。  
- `IBKR_HOST` / `IBKR_PORT` / `IBKR_CLIENT_ID` 与本地实例一致；**多实例**时 client id 不能冲突。  
- 防火墙面板允许本机回环。  
- `paper-reconcile` 等**必须**在能连上 IBKR 时运行；不连时失败为预期。  

---

## paper-reconcile 失败

- 读终端**原文**与 `reconcile` 输出中的符号列表。  
- 常见：本地 journal 与券商持仓不一致、缺少止损单等——按报告逐项处理，**不是**在 UI 里「强制下单」解决。  
- 仍失败时看 Telegram/ `memory/DAILY-SUMMARY.md` 回退是否写入。

---

## 没有成交 / 没有纸单

- **回测**不会产生真实或纸面券商成交；只写报告。  
- **Intraday 纸**受：`trading.intraday_paper.enabled`、**runtime 文件**、Kill Switch、对账、时段、**无新信号** 等约束——在 Journal 与 `engine-status` 里看原因。  
- 默认 **dry_run** 可能不实际发单；查配置中 `intraday_paper.dry_run`。

---

## Telegram 没收到

- 查 `config/telegram.yaml` 与 **环境变量**（不打印到公共日志）是否配置。  
- 查网络与 Bot 被屏蔽情况。  
- 未配置时系统可能**仅写** `memory/DAILY-SUMMARY.md`——非故障。

---

## 回测没有数据

- `data/candles/` 下是否已有对应标的/周期；无则 `fetch-candles`（只读拉取）。  
- 回测日期范围是否在缓存覆盖内。  
- 看 `bot/backtests` 相关错误行（在终端完整输出中）。

---

## 括号不完整 (bracket_integrity ≠ complete) 或 broker 错码

1. 打开 **Journal** 行内 **Integrity** 与 **Order IDs**；看 `bracket_protected`、错误码。  
2. 在 TWS 纸面人工核对三腿；不要为「修括号」在 UI 里找**非**白名单命令。  
3. 阅读该次 `skipped_reasons` 与 `paper-reconcile` 结果。

## Journal Trade Review 没有生成图表

- **正常现象**：若该交易所在 **NY 历日**没有在 `data/candles/<SYMBOL>/1min/<YYYY-MM-DD>.csv` 的本地 1m 缓存，Strategy Lab **绝不会**为出图而替你连 IBKR 拉线——无论 Journal 自动尝试、复盘页还是 `report-on-exit` 批量，都**仅**用本地缓存。请先在 **Backtest / Data Coverage** 或按需使用白名单 **`fetch-candles`**（只读拉缓存）准备数据，再打开 Journal / 跑 `generate-trade-charts --latest`，或复盘页 **生成复盘图**，或单条 CLI：`generate-trade-chart` / `journal-generate-trade-chart --trade-id <id>`。  
- **没有复盘图 ≠ 策略或下单「坏了」**——只说明本机暂无用于绘图的缓存（主表会显示「缺少K线」一类状态）。仍可用主表与 **Trade Review** 阅读文本原因、价位与保护状态。  
- **状态含义（主表 Chart 列大致对应）**：「图表可用」= 已有 PNG；「缺少K线」= 当日无本地 CSV；「等待中」= 保护未完整等尚不宜出图；不适用 = 跳过或未提交等。  
- 生成图输出在 `data/reports/trade_charts/`；批量摘要可能落在 `data/runtime/trade_chart_batch_last.json`（均为运行时，勿提交 git）。

## 日额度已满 (daily cap)

- **Paper** 与 **Dashboard** 显示 `today_submitted_notional` / `daily_remaining`；**Journal** 的 sizing 列可能含 `daily_cap` 相关说明。  
- 当日不再应有新单直至 UTC/本地日切或配置调整（勿通过提交受保护 `settings.yaml` 绕过）。

## 缺少 JSON / 报告文件

- **Research / 扫描** 等未运行则 `data/research/`、`data/intraday_smc/` 可能为空。  
- `engine-status --json` 的 `artifacts` 会显示「最新」若存在。  
- 不要手工伪造审计 JSONL 以「骗过」界面。

---

## Kill Switch 处于激活

- 若存在 `data/KILL_SWITCH`：先确认是否**故意**；删除该文件前确保团队流程允许。  
- 激活时多数自动/纸路径会**硬拦截**新风险。  

---

## runtime 关掉了 auto / intraday

- 读 `data/runtime/mtf_auto_paper_enabled` 与 `intraday_auto_paper_enabled` 内容：  
  `0/off/false` 为显式关闭；**缺失**时回退配置。  
- UI **Settings** 也显示只读状态，与文件一致即可。

---

## git 很脏、全是 data / logs

- `data/*`、`logs/`、运行时 PID 等应在 `.gitignore` 中。  
- 若仍出现未忽略文件：检查是否把输出写在仓库外路径或 `.gitignore` 规则被覆盖。  
- **不要** `git add` 纸面数据与密钥。  
- 运行 `./scripts/strategy_lab_doctor.sh` 会提示 `data/runtime` / `logs` 是否被 ignore。

---

## 仍无法解决

1. 跑 `make strategy-lab-smoke` 与 `python3 -m pytest` 确认基础健康。  
2. 在 issue/笔记中附：**命令**、**无密钥** 的终端片段、`engine-status --json` 的 `ok/paper_only` 与 `kill_switch` 字段。  
3. 读 `docs/safety-rules.md` 与 `docs/deployment-architecture.md` 核对你的操作是否越界（例如试图在 UI 里直连 TWS）。
