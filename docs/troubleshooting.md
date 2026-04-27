# 故障排除（Strategy Lab 本地，简体中文）

> 安全提示：不要在此粘贴 **API 密钥、Token、账户号**。  
> 若问题与券商连接有关，先确认**纸面**端口与 `IBKR_CLIENT_ID` 未冲突。

---

## 完全用 UI 时 TWS 连不上

1. 在 **Dashboard** 用 **IBKR Session Status** 看输出（**显式点按钮**后才会走 CLI）。  
2. 本机 TWS/网关是否登录**纸面**、API 是否启用。  
3. 端口/Client ID 是否与 `config` 与 `docs/ibkr-setup.md` 一致。  
4. 仍失败：在终端用 `strategy_lab_doctor.sh` 或同文档排查；**不要**在 UI 里关 TWS 的 Order 保护。

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

## 准备「自动纸日内循环」前状态不对

1. 运行 **只读** 检查：`python3 -m bot.cli auto-loop-readiness` 或加 `--json`；Dashboard / Paper 的 **Check Auto Loop Readiness** 等价。  
2. 看 `next_safe_action`：**Kill**、**对账**、**日预算**、**非 ICT 纸策略**、**paper activation 非 READY** 等都会让 `readiness` 为 **Not ready**。  
3. 默认**不加** `--probe-ibkr`（不连 TWS）；只有需要券商侧只读对账提示时才加。  
4. **不要**在 UI 里找「开始循环」——该 CLI **不在**白名单；有意烟测时只在终端、且遵守用户手册美东时间窗与帽位。  
5. 收工后日报邮件：见清单里 `paper-daily-report --email`；本检查**不**代跑、**不**启动循环。

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
