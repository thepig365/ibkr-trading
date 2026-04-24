# 后台自动纸面 MTF 循环 (10H)

本说明仅针对 **IBKR 模拟/纸面账户** 与 `LIMIT` 括号单路径；**不得**用于实盘。仓库内 `block_live_trading: true`、`allow_live_trading: false` 等门控需持续保持为安全默认值。

## 作用

- `run-auto-paper-mtf-loop` 在美国常规交易时段（美东 09:45–15:30、工作日）内按间隔执行：市场体制 → 动态 watchlist → `auto-paper-mtf`（仅当配置 + FULL_ALIGNMENT 等条件满足时可能下纸面括号单）→ 诊断报告 → 接近对齐观察（不通过 `--telegram` 刷频）。
- 状态写入 `data/runtime/auto_paper_loop_state.json`；按日 JSONL 在 `data/auto_paper_loop/YYYY-MM-DD-loop.jsonl`。
- LaunchAgent 可在登录后由 `launchctl` 启动/停止，无需一直开着「终端里手工跑 `auto-paper-mtf`」。

## 先决条件

- Mac 保持醒着（可配合「防止睡眠」或 `caffeinate`，按需自行配置）。
- 网络稳定。
- **TWS 或 IB Gateway** 以 **纸面账户** 登录，且与 `.env` 中 `IBKR_ACCOUNT_MODE=paper` 等设置一致。
- 已在项目根创建 `.venv` 并 `pip install -r requirements.txt`。
- 已根据 `config/settings.yaml` 与 `data/runtime/mtf_auto_paper_enabled`、Telegram `/auto_mtf_on` 等门控，明确启用**仅纸面**自动路径。

## 安装 LaunchAgent

```bash
cd "/path/to/ibkr-trading-bot"
bash scripts/install_launch_agent.sh
```

- 会生成 `~/Library/LaunchAgents/com.leon.ibkr-trading-bot.auto-paper.plist`（**不含** Token、账户号；密钥仍在 `.env`，且 `.env` 应被 git 忽略）。

## 启动 / 停止 / 状态

```bash
bash scripts/start_auto_paper.sh
bash scripts/stop_auto_paper.sh
bash scripts/status_auto_paper.sh
```

- 标准输出与错误分别写入 `logs/auto-paper.stdout.log`、`logs/auto-paper.stderr.log`。

## 卸载

```bash
bash scripts/uninstall_launch_agent.sh
```

## Telegram 控制（需 command bot 已配置并轮询）

- `/auto_mtf_status`：KILL、runtime 文件、最近 FULL 计数、上次是否下单等（只读 + 门控，**不是**下单）。
- `/auto_mtf_on` / `/auto_mtf_off`：写入 `data/runtime/mtf_auto_paper_enabled`；`0` 会覆盖配置中的 `fully_automatic`，**停止**新的纸面提交直到再次 `/auto_mtf_on` 或删文件并依赖 yaml。
- `/kill` / `/resume`：创建或删除 `data/KILL_SWITCH`。
- `/paper_orders`：展示 `data/orders.jsonl` 尾部。
- `/loop_status` / `/heartbeat`：LaunchAgent/循环状态与心跳线索。

不安全的自然语言（如显式 `buy`/`live`/违规资产类别等）仍会被安全门拒绝。

## 一次性自检（不常驻）

```bash
python -m bot.cli run-auto-paper-mtf-loop --once --market-hours-only --telegram --limit 20
```

## 干跑短时间（调试用）

```bash
python -m bot.cli run-auto-paper-mtf-loop --interval-minutes 1 --market-hours-only --telegram --limit 20 --stop-after-minutes 3
```

## 安全摘要

- 仅 PAPER；若 TWS/环境与配置不一致，循环会跳过或拒绝提交。
- 仅 **FULL_ALIGNMENT** 路径会进入纸面括号单逻辑；接近对齐为提醒性质。
- 无 `market` 裸单、无无止损/无目标单（由现有 `mtf_paper` 与 bracket 实现保证）。
- 所有提交应对账、防重复、且应在日志与 Telegram 留痕（见实现与 `settings.yaml` 开关）。
