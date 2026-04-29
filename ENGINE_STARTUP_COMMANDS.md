# IBKR 交易引擎 — 本地启动命令速查

本页汇总「一键启动」脚本相关命令与安全提醒。详细说明见仓库根目录 `README.md` 的 **One-command local startup** 与 `scripts/README.md`。

---

## 一次性：为脚本加执行权限（首次克隆或 chmod 丢失时）

```bash
chmod +x scripts/*.sh
```

---

## 每日启动前：TWS / IB Gateway（Paper）— 需人工操作

1. 打开 **TWS** 或 **IB Gateway**。  
2. 使用 **Paper Trading（模拟）** 账户登录，**不要**用实盘窗口误登。  
3. 确认 API 选项（见下节 **7497**）。  
4. 保持应用运行，再执行启动脚本。

---

## API 端口（Paper）：7497

- TWS Paper **套接字端口**一般为 **7497**（与 `config.yaml` / `config.example.yaml` 中 `ibkr.port` 一致）。  
- **Read-Only API** 应为 **OFF**（下单/数据需写权限类行为时）。  
- **Enable ActiveX / Socket Clients** 应为 **ON**。  
- 本机信任：**127.0.0.1**（按 TWS/Gateway 文档配置）。

---

## 切勿使用实盘端口 7496（除非你知道后果）

- **7496** 为 TWS **实盘**常用端口。本项目默认按 **Paper（7497）** 设计且 `config.example.yaml` 中 `allow_live_trading: false` 会拦截对 7496 的误连。  
- **不要**在无充分验证、未改配置与未接受风险的情况下把引擎指向实盘。  
- 任何实盘相关修改须由你本地 `config.yaml` 与账户环境**自行负责**。

---

## 每日启动引擎（后端 :8000 + 前端 :3000）

在项目根目录执行：

```bash
./scripts/start_engine.sh
```

脚本会检查 `.venv`、`/.env`、`IBKR_ACCOUNT`、`npm`、（可选）7497 提示，并在后台写 PID 至 `.runtime/`、日志至 `logs/`。成功后通常会打开 Dashboard（默认 `http://127.0.0.1:3000`）。

---

## 查看状态

```bash
./scripts/status_engine.sh
```

可查看端口 **7497 / 8000 / 3000** 是否在监听；若后端可用会请求 `/api/connection-status`（**账户字段在输出中会做脱敏**）。

---

## 停止引擎

```bash
./scripts/stop_engine.sh
```

按下述顺序尝试：根据 `.runtime/backend.pid`、`frontend.pid` 结束进程；若无 PID 文件，会给出 `lsof` 自查说明，**请勿**随意 `kill` 不明进程。

---

## 禁止提交机密与本地配置

**不要**将下列文件提交到 Git（已被 `.gitignore` 忽略，仍请注意）：

- **`.env`** — API 密钥、Telegram、**IBKR 账户 ID** 等  
- **`config.yaml`** — 本地配置（与 `.env` 配合使用）

仅提交 **`config.example.yaml`**、**`.env.example`** 等模板即可。

---

## 相关路径

| 说明 | 路径 |
|------|------|
| 启动脚本 | `scripts/start_engine.sh` |
| 停止脚本 | `scripts/stop_engine.sh` |
| 状态脚本 | `scripts/status_engine.sh` |
| 脚本说明 | `scripts/README.md` |
