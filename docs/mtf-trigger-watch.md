# MTF 5 分钟触发观察（Prompt 10F）

## 作用

- **Near-alignment** 诊断会列出最接 FULL 的标的（如 `SETUP_READY_WAITING_TRIGGER`、5m 未确认等）。
- 本模块在**不修改策略参数、不启用交易**的前提下，**重复拉取 IBKR K 线**并重新跑 `run_mtf_smc`，只观察 **5m `trigger_state`** 是否从 `waiting_for_pullback` / `waiting_for_choch` 等变为 **confirmed**。
- 与「接近入场」类 Telegram 不同：这里关注 **短线触发是否已确认**，为将来若开启 `mtf_paper_bracket` 时的人工复核做准备；**本提示词范围内仍不下单。**

## CRM 式示例

- 日/4H/30m 已走到「在 30m 区附近等 5m」：诊断里常显示阻碍层为 **`FIVE_MIN_TRIGGER`**。
- 使用 **`mtf-trigger-check`** 或 **`mtf-trigger-watch`** 在盘中刷新 5m 数据，当引擎给出 **`confirmed`** 时，会发高优先级 **「5分钟触发确认」** Telegram，并写 `data/mtf_smc/…-trigger-check.json`。

## 5 分钟触发在引擎中的含义

- 5m 子逻辑见 `classify_5min_trigger` / `config/strategy.yaml` 的 `5min` 节（sweep、ChoCH、FVG/位移等，研究向）。
- `confirmed` 表示 5m 研究链在阈值内满足可标记为「已触发」；**仍不自动下单。**

## 一次检查：`mtf-trigger-check`

- 从 **`YYYY-MM-DD-mtf-diagnostic-report.json`** 的 **`near_alignment_candidates`** 中筛选观察列表（FIVE 或 `SETUP_READY`；可 `--include-premium`）。
- **需要 `--ibkr`** 拉取 5m（及 4H/30m/日 等与现有 MTF 扫描一致的数据）。
- 输出：  
  `data/mtf_smc/YYYY-MM-DD-trigger-check.json`  
  且合并 **`runtime_trigger_state.json`**（去重、状态变化、同日重复确认不 spam）。

## 观察循环：`mtf-trigger-watch`

- 在总时长内按间隔重复调用同一条检查链（默认 5 分钟、120 分钟）。
- 仅 **首轮** 且当前无轮询标的时，可能发一次「无 FIVE 观察候选」类提示；循环中**不会**对「仍等待」**每轮**都发，详见代码内 `still_waiting_telegram_worthy`（首轮 / 状态变化 / 约 30 分钟心跳等）。
- 日志：`data/mtf_smc/YYYY-MM-DD-trigger-watch.jsonl`（`research_only: true`, `execution_allowed: false`）。

## Telegram 摘要类型

- **5 分钟确认**：`【MTF SMC/ICT 5分钟触发确认】` + 多周期行 + **明确提醒 paper gate 未开、仅观察**。
- **仍等待**（有节制）：`【MTF SMC/ICT 触发观察中】` + 每标的当前 5m 状态。
- **无候选**：`【MTF SMC/ICT 触发观察】` + 无 FIVE 级观察行。

## 本提示词范围内：不交易

- 不调用 `Broker.place_order`、不调用 `ib.placeOrder`、不改 `config/settings.yaml` 中的 `trading.enabled` / `mtf_paper_bracket_enabled` / `mtf_paper_dry_run`。
- 输出 JSON 中固定 **`execution_allowed: false`**, **`research_only: true`**。

## 与将来纸面执行的关系

- 当且仅当**未来**在配置中显式打开 paper bracket、且你仍用同一套 MTF 报告流程时，本观察阶段帮助你**提前知道**哪些标的 5m 已 **confirmed**；**当前不创建订单。**

## 命令示例

```text
python -m bot.cli mtf-trigger-check --latest --ibkr --telegram --top 5

python -m bot.cli mtf-trigger-watch --latest --ibkr --telegram --interval-minutes 5 --duration-minutes 120 --top 5
```

**前置**：同日期下已存在 `mtf-diagnostic-report`（通常由 `mtf-diagnostic-report` 命令生成）。
