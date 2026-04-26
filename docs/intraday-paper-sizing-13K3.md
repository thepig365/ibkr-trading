# Paper 交易规模（13K.3）

本文件说明 ICT/SMC 日内**纸面**前向执行的数量与名义金额限制；与生产实盘路径无关。

## 规则摘要

- **单笔 paper trade 最大名义金额：10,000 USD**（`trading.intraday_paper.max_notional_per_order_usd`）
- **每日 paper trade 总名义金额：100,000 USD**（`trading.intraday_paper.max_daily_notional_usd`），按当日审计 JSONL 中 `submitted_to_broker=true` 的 `estimated_notional` 累加，不可读时安全失败（不假定为 0）
- **账户净值上限（安全阀）：** `max_equity_per_position_pct`（默认 10% 净值，转换为名义后折算为股数）
- **最大股数:** `max_quantity_per_order`（与上述限制一起取 **最小值**）

**最终股数 = min(**

- 按风险算出的 `risk_based_quantity`（与既有 risk% 一致）
- `floor(10_000 / entry_price)`（单笔 1 万刀名义 cap）
- `floor(当日剩余日限额 / entry_price)`（日累计 cap 剩余）
- `floor((净值 × 账户上限%) / entry_price)`（若配置为 10% 则为净值 10% 对应股数）
- `max_quantity_per_order`

**）**

## TIF

所有括号腿显式使用同一 TIF，当前仅支持 **DAY**（`trading.intraday_paper.tif`），以减少与 TWS 订单预设的冲突。

## 审计

`data/paper_orders/*-intraday-paper-orders.jsonl` 中记录 `tif` / `parent_tif` / `stop_tif` / `target_tif`、各 cap 计算字段与 `sizing_audit`。
