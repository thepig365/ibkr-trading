# 多周期 SMC/ICT 识别（MTF）— 研究专用

## 为什么仅有 30 分钟不够

单一周期的 SMC 结构容易被噪声与局部波动扭曲：缺少**宏观偏置**、**高一级结构是否确认**、以及**精确入场触发**。本引擎在**不下单、不启执行**的前提下，把日图偏置、4 小时结构、30 分钟设置与 5 分钟触发串联，用于研究与复盘。

## 各周期角色

| 周期 | 角色 |
|------|------|
| **Daily** | 宏观偏置与主要流动性背景 |
| **4H** | 结构确认与操作级偏置 |
| **30min** | 设置检测（入场区、止损、目标、R/R 等由既有 SMC 研究逻辑计算） |
| **5min** | 在 30 分钟有效设置附近的入场**触发**（非独立建仓） |

## 对齐分类含义

- **FULL_ALIGNMENT**：日/4H/30m/5m 与风险条件在 V1 规则下一致，且未因溢价/新闻/制度被阻断。仍仅为研究标记，**不触发下单**。
- **SETUP_READY_WAITING_TRIGGER**：偏置与结构可接受，30 分钟设置就绪或等待回踩，5 分钟仍在等回踩或等 ChoCH。
- **BIAS_OK_SETUP_INCOMPLETE**：偏置尚可，但 30 分钟结构或风险链不完整。

系统全局保持 `research_only=true`、`execution_allowed=false`；`eligible_for_future_paper_trade` 仅作**研究/沙盒前置标记**，**不是**执行信号。未来若需纸面执行，须单独经评审与硬开关，且不得与本识别引擎混为一谈。

## 为何本模块仍不交易

- 本仓库中**不**实现下单、**不**接纸面撮合、**不**调用 `broker.place_order`。
- Telegram 与 JSON 报告仅用于人类复盘与流程对接。

## 命令示例

单标的（需 IBKR 与可选 Telegram 配置时再加 `--ibkr` / `--telegram`）：

```bash
python -m bot.cli scan-mtf-smc --symbol AAPL --ibkr --chart --telegram
```

动态观察列表多标的：

```bash
python -m bot.cli scan-mtf-smc-watchlist --source dynamic --ibkr --chart --limit 20 --telegram
```

输出 JSON 默认在 `data/mtf_smc/`；若使用 `--chart`，各周期图在 `data/debug_charts/`，文件名含 `mtf-daily` / `mtf-4h` / `mtf-30min` / `mtf-5min`。

## 4 小时 K 线说明

若 IB 原生 `4 hours` 历史不足或返回过少，会尝试用 **1 小时 RTH** 聚合为 4 小时，并在结果 `warnings` 中说明（不崩溃）。

## 与既有 30 分钟模式的关系

30 分钟 SMC 扫描与 `scan-smc` 等仍保留；MTF 引擎在其上**叠加**日/4H/5m 与综合评分，**不重复**实现核心 30 分钟评算逻辑，而是复用 `evaluate_smc_liquidity_reversal` 等。
