# 故障排除（Strategy Lab 本地，简体中文）

> 安全提示：不要在此粘贴 **API 密钥、Token、账户号**。  
> 若问题与券商连接有关，先确认**纸面**端口与 `IBKR_CLIENT_ID` 未冲突。

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
