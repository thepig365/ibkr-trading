"""Minimal EN/zh UI labels for Strategy Lab (display-only, no business logic)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import Request

COOKIE_NAME = "strategy_lab_lang"
SUPPORTED = frozenset({"en", "zh"})

# key -> {en: str, zh: str}
M: dict[str, dict[str, str]] = {
    # base / nav
    "app.title_suffix": {"en": "— Strategy Lab (Local)", "zh": "— Strategy Lab（本地）"},
    "brand.sub": {"en": "Local · Paper Only", "zh": "本地 · 仅纸面"},
    "nav.dashboard": {"en": "Trader Cockpit", "zh": "交易员驾驶舱"},
    "nav.research": {"en": "Research", "zh": "研究"},
    "nav.watchlist": {"en": "Watchlist", "zh": "自选股"},
    "nav.signals": {"en": "Signals", "zh": "信号"},
    "nav.backtest": {"en": "Backtest", "zh": "回测"},
    "nav.edge": {"en": "Edge", "zh": "Edge"},
    "nav.paper": {"en": "Paper Trading", "zh": "纸面交易"},
    "nav.trades": {"en": "Trade Records", "zh": "交易记录"},
    "nav.journal": {"en": "Audit Log", "zh": "技术流水"},
    "nav.reports": {"en": "Reports", "zh": "报告中心"},
    "nav.logs": {"en": "Logs", "zh": "运行日志"},
    "nav.strategies": {"en": "Strategies", "zh": "策略中心"},
    "nav.settings": {"en": "Settings / Doctor", "zh": "设置 / 诊断"},
    "help.repo": {"en": "Help (repo)", "zh": "帮助（仓库文档）"},
    "help.open_ide": {"en": "Open in IDE or editor.", "zh": "在 IDE 或编辑器中打开。"},
    "lang.en": {"en": "EN", "zh": "EN"},
    "lang.zh": {"en": "中文", "zh": "中文"},
    "footer.line": {
        "en": "Strategy Lab UI · bound to {host}:{port} · Project: {root} · This UI never connects to TWS at startup, never places orders.",
        "zh": "Strategy Lab UI · 绑定 {host}:{port} · 项目: {root} · 本页加载不会连接 TWS，不会下单。",
    },
    "badges.account": {"en": "ACCOUNT", "zh": "账户"},
    "badges.paper_only": {"en": "PAPER ONLY", "zh": "仅纸面"},
    "badges.backend": {"en": "BACKEND", "zh": "后端"},
    "safety.paper_only_line": {
        "en": "Paper only. Live trading remains disabled.",
        "zh": "仅纸面交易。实盘交易仍然禁用。",
    },
    "safety.news_edge": {
        "en": "News and Edge do not trigger trades.",
        "zh": "新闻与 Edge 评分不会直接触发交易。",
    },
    "safety.trade_requires": {
        "en": "Trading requires ICT/SMC readiness and 1-minute trigger.",
        "zh": "交易必须满足 ICT/SMC 就绪条件与 1 分钟触发。",
    },
    "safety.ui_no_ibkr": {
        "en": "UI rendering does not connect to IBKR.",
        "zh": "页面加载本身不会连接 IBKR。",
    },
    # dashboard
    "page.dashboard": {"en": "Trader Cockpit", "zh": "交易员驾驶舱"},
    "dashboard.sub": {
        "en": "Trader cockpit — local ledger + last explicit broker snapshot. Does not load TWS on refresh; click Connect / Refresh below when you want broker truth.",
        "zh": "交易员驾驶舱：本地账本 + 最近一次你显式拉取的券商快照。普通加载不会访问 TWS；需要券商实况请点击下方「连接 / 刷新 TWS」。",
    },
    "dashboard.current_strategy": {"en": "Current paper strategy", "zh": "当前纸面策略"},
    "dashboard.trigger_line": {
        "en": "1-minute trigger required (ICT/SMC intraday)",
        "zh": "需要 1 分钟触发（ICT/SMC 日内）",
    },
    "dashboard.report_center_title": {"en": "Report center (UI-first)", "zh": "报告中心（UI 优先）"},
    "dashboard.report_center_p": {
        "en": "Full paper, research, backtest, and edge artifacts:",
        "zh": "纸面、研究、回测、Edge 等产物：",
    },
    "dashboard.open_reports": {"en": "Open Reports", "zh": "打开报告中心"},
    "dashboard.today_safety": {"en": "Today’s safety & readiness", "zh": "今日安全与就绪"},
    "dashboard.card1": {"en": "1 · Is the engine safe today?", "zh": "1 · 今日引擎是否安全？"},
    "dashboard.card2": {"en": "2 · Trade readiness (research view)", "zh": "2 · 交易就绪（研究视角）"},
    "dashboard.card3": {"en": "3 · Today’s test budget (paper)", "zh": "3 · 今日测试预算（纸面）"},
    "dashboard.next_step": {"en": "5 · Next step", "zh": "5 · 下一步"},
    "dashboard.automatic_engine": {
        "en": "9 · Automatic Paper Trading Engine (ICT/SMC)",
        "zh": "9 · 自动纸面交易引擎（ICT/SMC）",
    },
    "dashboard.paper_safety": {"en": "Paper safety", "zh": "纸面安全"},
    "dashboard.trading_day_summary": {"en": "Trading Day Summary", "zh": "交易日摘要"},
    "dashboard.engine_label": {"en": "Engine", "zh": "引擎"},
    "dashboard.engine_waiting": {"en": "Waiting", "zh": "等待"},
    "dashboard.engine_running": {"en": "Running", "zh": "运行中"},
    "dashboard.engine_blocked": {"en": "Blocked", "zh": "阻断"},
    "dashboard.perf_pending_heading": {"en": "Mini Performance", "zh": "迷你表现"},
    "dashboard.mini_performance_pending_text": {
        "en": "Not enough closed trades yet — cumulative R appears after exits are recorded.",
        "zh": "表现等待中：已有平仓不足以绘制累计 R — 记录在平仓后继续。",
    },
    "dashboard.explain_open_no_closed_body": {
        "en": "Trades have been submitted/opened, but no closed trades with exit data are recorded yet. R curve and P&L analytics will appear after exits are recorded.",
        "zh": "当前已有开仓/提交记录，但尚无已记录平仓的交易；R曲线与盈亏统计会在记录到平仓后显示。",
    },
    "dashboard.explain_missing_candles_body": {
        "en": "Some trades are missing local 1-minute candles. Complete Trade Charts can fetch IBKR read-only candles for traded symbols and generate charts.",
        "zh": "部分交易缺少本地1分钟K线。可用「补齐交易图表」从 IBKR 只读补齐已交易标的K线并生成图表。",
    },
    "dashboard.quick_diagnostics_anchor": {"en": "Diagnostics", "zh": "诊断"},
    "dashboard.complete_trade_charts_card_title": {
        "en": "Complete Trade Charts",
        "zh": "补齐交易图表",
    },
    "dashboard.complete_trade_charts_card_sub": {
        "en": "Fetch missing IBKR 1m candles for traded symbols and generate charts. Read-only; no orders. Clicking submits the allowlisted CLI (same as Reports / Trades); nothing runs on dashboard load.",
        "zh": "从 IBKR 只读补齐已交易标的的1分钟K线并生成图表；不会下单。以下为已批准 CLI 表单（与报告/记录页同源）；不会在仪表板加载时自动执行。",
    },
    "dashboard.compact_strategy_line": {
        "en": "Strategy:",
        "zh": "策略：",
    },
    "dashboard.trader_today_r": {"en": "Today R (NY, closed)", "zh": "今日 R（美东已平仓）"},
    "dashboard.trader_cum_r": {"en": "Σ R (closed)", "zh": "Σ R（已平仓）"},
    "dashboard.cockpit_intro": {
        "en": "This dashboard shows local engine records and the latest broker snapshot file. Click Connect / Refresh TWS for current broker truth (read-only; no orders).",
        "zh": "本控制台显示本地引擎记录与最近一次券商快照文件。点击「连接 / 刷新 TWS」可更新券商真实持仓/委托（只读核对，不下单）。",
    },
    "dashboard.connect_tws_btn": {"en": "Connect / Refresh TWS", "zh": "连接 / 刷新 TWS"},
    "dashboard.broker_truth_heading": {"en": "Broker Truth", "zh": "券商真实状态"},
    "dashboard.local_engine_heading": {"en": "Local Engine Records", "zh": "本地引擎记录"},
    "dashboard.truth_vs_local_explainer": {
        "en": "Broker Truth comes from the latest TWS snapshot. Local Engine Records come from Strategy Lab logs.",
        "zh": "券商真实状态来自最近一次 TWS 快照；本地引擎记录来自 Strategy Lab 日志。",
    },
    "dashboard.local_records_explainer": {
        "en": "Submitted records, broker snapshots, and reconciled fills are different: run Reconcile Fills to align TWS executions with this ledger.",
        "zh": "已提交记录、券商快照与对账成交是三件事；请运行「成交对账」将 TWS 成交与本地账本对齐。",
    },
    "dashboard.fill_recon_heading": {"en": "Fill Reconciliation", "zh": "成交对账"},
    "dashboard.fill_recon_intro": {
        "en": "Last explicit reconcile-fills run (read-only TWS). Separate from Broker Truth above; both are useful.",
        "zh": "最近一次显式 reconcile-fills（只读 TWS）。与上方券商快照互补，请结合使用。",
    },
    "dashboard.fill_recon_last": {"en": "Last reconciled", "zh": "最近对账时间"},
    "dashboard.fill_recon_fills": {"en": "Fills found", "zh": "成交笔数"},
    "dashboard.fill_recon_closed": {"en": "Closed trades", "zh": "已平仓笔数"},
    "dashboard.fill_recon_filled_open": {"en": "Filled open", "zh": "已成交未平"},
    "dashboard.fill_recon_submitted_nf": {"en": "Submitted not filled", "zh": "已报未成交"},
    "dashboard.fill_recon_unknown": {"en": "Unknown / unmatched", "zh": "未知/未匹配"},
    "dashboard.fill_recon_r_sum": {"en": "Realized ΣR", "zh": "已实现ΣR"},
    "dashboard.fill_recon_btn": {"en": "Reconcile Fills", "zh": "成交对账"},
    "dashboard.fill_recon_none": {
        "en": "Fill reconciliation has not been run yet. Click Reconcile Fills to update trade outcomes.",
        "zh": "尚未运行成交对账。点击「成交对账」更新交易结果。",
    },
    "dashboard.flash_reconcile_fills": {
        "en": "{base}. Fills={fills}, closed={closed}, filled_open={fo}, submitted_nf={snf}.",
        "zh": "{base}。成交={fills}，已平={closed}，已成交未平={fo}，已报未成交={snf}。",
    },
    "dashboard.account_not_checked_short": {
        "en": "Not checked yet. Click Connect / Refresh TWS.",
        "zh": "尚未核对。请点击「连接 / 刷新 TWS」。",
    },
    "dashboard.card_account_value": {"en": "Account Value", "zh": "账户净值"},
    "dashboard.net_liquidation_label": {"en": "Net Liquidation", "zh": "账户净值"},
    "dashboard.avail_funds_card": {"en": "Available Funds", "zh": "可用资金"},
    "dashboard.buying_power_card": {"en": "Buying Power", "zh": "购买力"},
    "dashboard.cash_card": {"en": "Cash", "zh": "现金"},
    "dashboard.unrealized_pnl_card": {"en": "Unrealized P&L", "zh": "未实现盈亏"},
    "dashboard.realized_pnl_card": {"en": "Realized P&L", "zh": "已实现盈亏"},
    "dashboard.field_na_tooltip": {
        "en": "Not provided by latest broker snapshot.",
        "zh": "最新券商快照未提供该字段。",
    },
    "dashboard.complete_trade_charts_explicit": {
        "en": "Complete Trade Charts (+IBKR 1m, read-only)",
        "zh": "补齐交易图表（仅读 IBKR 1 分钟）",
    },
    "dashboard.open_trades_btn": {"en": "Open Trades", "zh": "交易记录"},
    "dashboard.open_reports_btn": {"en": "Open Reports", "zh": "打开报告"},
    "dashboard.open_paper_btn": {"en": "Open Paper", "zh": "打开纸面交易"},
    "dashboard.open_diagnostics_btn": {"en": "Open Diagnostics", "zh": "诊断 / 设置"},
    "dashboard.button_failed": {"en": "Button failed", "zh": "按钮执行失败"},
    "dashboard.flash_cmd_rejected_label": {"en": "Command rejected", "zh": "命令被拒绝"},
    "dashboard.flash_trade_charts_detail": {
        "en": (
            "{base}. Charts generated: {generated}. "
            "Eligible (limit window): {eligible}. Missing candle gaps: {missing}. "
            "Errors: {errors}. Trades without exit in selection: {no_exit}. Skipped status: {skipped}"
        ),
        "zh": (
            "{base}。本次生成 PNG：{generated}；窗口内可走图表笔数（eligible）：{eligible}；"
            "缺失 K 线段：{missing}；错误：{errors}；无平仓记录：{no_exit}；状态被跳过：{skipped}"
        ),
    },
    "dashboard.flash_trade_charts_none_hint": {
        "en": "If generated is 0, eligible trades often lack recorded exits — check /trades.",
        "zh": "若为 0，多为所选交易尚无已记录的平仓或非可生成状态，请到「交易记录」核对。",
    },
    "dashboard.flash_broker_snapshot_detail": {
        "en": "Snapshot status:{status}; positions:{positions}; open_orders:{orders}.",
        "zh": "快照状态：{status}；持仓计数：{positions}；未完成单：{orders}。",
    },
    "dashboard.tws_connected_short": {"en": "IBKR linked", "zh": "IBKR 已连接"},
    "dashboard.ibkr_socket_down": {"en": "IBKR not linked", "zh": "IBKR 未连接"},
    "dashboard.tws_listening_short": {"en": "Listening", "zh": "网关"},
    "dashboard.connect_tws_sub": {
        "en": "Read-only broker check. Updates positions, open orders, and fills. No orders are sent.",
        "zh": "只读券商核对。更新持仓、未完成订单与成交记录；不会下单。",
    },
    "dashboard.broker_card_title": {"en": "Broker snapshot", "zh": "券商快照"},
    "dashboard.broker_data_sources": {
        "en": "Data source: Local records + last broker snapshot file",
        "zh": "数据来源：本地记录 + 最近一次券商快照文件",
    },
    "dashboard.broker_no_snapshot_yet": {
        "en": "Broker has not been checked yet. Click Connect / Refresh TWS.",
        "zh": "尚未核对券商状态。请点击「连接 / 刷新 TWS」。",
    },
    "dashboard.broker_last_check": {"en": "Last TWS check", "zh": "最近一次 TWS 核对"},
    "dashboard.broker_tws_connected": {"en": "TWS session connected", "zh": "TWS 会话"},
    "dashboard.broker_account_mode": {"en": "Account mode", "zh": "账户模式"},
    "dashboard.broker_positions_n": {"en": "Broker-confirmed positions", "zh": "券商确认持仓笔数"},
    "dashboard.broker_open_orders_n": {"en": "Open orders", "zh": "未完成订单"},
    "dashboard.broker_execs_n": {"en": "Recent fills (broker)", "zh": "近期成交笔数"},
    "dashboard.broker_snapshot_status": {"en": "Snapshot status", "zh": "快照状态"},
    "dashboard.trader_submitted_records": {"en": "Submitted records", "zh": "已提交记录"},
    "dashboard.trader_sent_to_broker_records": {"en": "Sent to broker", "zh": "已发送至券商"},
    "dashboard.broker_positions_count": {"en": "Broker positions", "zh": "券商确认持仓"},
    "dashboard.explain_submit_vs_broker_pos": {
        "en": "Submitted records exist, but the latest broker snapshot shows no open positions. Orders may not have filled, may have expired, or broker state has not been refreshed.",
        "zh": "本地有已提交记录，但最近一次券商快照显示没有持仓。订单可能未成交、已过期，或需要重新刷新券商状态。",
    },
    "dashboard.trader_closed_exit": {"en": "Closed with exit", "zh": "已记录平仓"},
    "dashboard.trader_charts_miss_label": {"en": "Missing trade charts", "zh": "缺少交易图"},
    "dashboard.trader_open": {"en": "Open (ledger)", "zh": "持仓（台账）"},
    "dashboard.trader_closed": {"en": "Closed (ledger)", "zh": "已平仓（台账）"},
    "dashboard.trader_skipped": {"en": "Skipped", "zh": "已跳过"},
    "dashboard.trader_charts_missing": {"en": "Charts missing candles", "zh": "复盘图缺 K 线"},
    "dashboard.trader_action": {"en": "Action required", "zh": "需要处理"},
    "dashboard.trader_latest": {"en": "Latest trades", "zh": "最新交易"},
    "dashboard.trader_mini_r": {"en": "Mini cumulative R curve", "zh": "累计 R 示意"},
    "dashboard.trader_quick": {"en": "Quick links", "zh": "快捷入口"},
    "dashboard.trader_none_today_r": {"en": "No closed R today (NY)", "zh": "今日（美东）无已平仓 R"},
    "dashboard.action_protection": {
        "en": "Incomplete bracket protection rows present — review /trades or Journal.",
        "zh": "存在保护不完整 — 请到 /trades 或流水核对。",
    },
    "dashboard.action_charts": {
        "en": "Some trade review charts are incomplete — local 1-minute candles may still be missing.",
        "zh": "部分交易复盘图不齐 — 可能仍缺少本地 1 分钟 K 线数据。",
    },
    "dashboard.action_missing_exit_records": {
        "en": "Missing exit records for open/submitted trades — reconcile in Trade Records.",
        "zh": "开仓/提交类记录尚缺平仓明细 — 请在交易记录中核对。",
    },
    "dashboard.action_telegram_listener": {
        "en": "Telegram command listener appears down — check launchd / Settings.",
        "zh": "Telegram 命令监听可能未启动 — 请查看 launchctl 与 Settings。",
    },
    "dashboard.action_engine_gates": {
        "en": "Automatic paper engine readiness blocked (configuration / gates — see Diagnostics).",
        "zh": "自动纸面引擎就绪未通过 — 请到下方诊断区块查看门禁原因。",
    },
    "dashboard.action_tws_hints": {
        "en": "TWS / reconcile hints failing — refresh Open Orders / Paper Reconcile safely.",
        "zh": "本地对账提示失败 — 请使用安全 CLI 刷新 Open Orders / Paper Reconcile。",
    },
    "dashboard.action_tws_broker_not_checked": {
        "en": "Broker snapshot not refreshed — click Connect / Refresh TWS (read-only) to align local records with broker truth.",
        "zh": "尚未刷新券商快照 — 请点击「连接 / 刷新 TWS」（只读）以核对本地与券商状态。",
    },
    "dashboard.journal_core_heading": {
        "en": "Trading journal core",
        "zh": "交易日记核心",
    },
    "dashboard.journal_core_blurb": {
        "en": "Primary health: cumulative R from closed trades. USD equity only when every closed trade has explicit realized USD in JSON.",
        "zh": "策略健康主指标为已平仓累计 R；美元资金曲线仅当每笔已平仓在 JSON 中均有明确已实现美元盈亏时才显示。",
    },
    "dashboard.cum_r_curve_title": {"en": "Cumulative R curve", "zh": "累计R曲线"},
    "dashboard.cum_r_curve_empty": {
        "en": "Not enough closed trades yet. The R curve will appear after exit records are available.",
        "zh": "已平仓样本不足。记录到平仓后，R曲线会显示。",
    },
    "dashboard.usd_equity_title": {"en": "USD equity curve", "zh": "美元资金曲线"},
    "dashboard.usd_equity_hidden": {
        "en": "USD equity curve is hidden until reliable realized USD P&L exists for all closed trades.",
        "zh": "美元资金曲线会在可靠的已实现美元盈亏记录出现后显示。",
    },
    "dashboard.usd_reports_link": {
        "en": "Full analytics on Reports",
        "zh": "完整分析见 Reports",
    },
    "dashboard.edge_health_title": {"en": "Edge health", "zh": "Edge 健康度"},
    "dashboard.edge_total_r": {"en": "Total R", "zh": "累计R"},
    "dashboard.edge_curr_dd": {"en": "Current drawdown R", "zh": "当前回撤R"},
    "dashboard.edge_max_dd": {"en": "Max drawdown R", "zh": "最大回撤R"},
    "dashboard.edge_closed_n": {"en": "Closed trades", "zh": "已平仓笔数"},
    "dashboard.edge_win_rate": {"en": "Win rate", "zh": "胜率"},
    "dashboard.edge_avg_r": {"en": "Average R", "zh": "平均R"},
    "dashboard.edge_not_enough": {
        "en": "Not enough closed trades yet.",
        "zh": "已平仓样本不足。",
    },
    "dashboard.edge_stats_need_two": {
        "en": "Win rate and average R need at least two closed trades.",
        "zh": "胜率与平均R至少需要两笔已平仓交易。",
    },
    "dashboard.skipped_breakdown_title": {"en": "Skipped reasons (top)", "zh": "跳过原因（Top）"},
    "dashboard.data_quality_title": {"en": "Data Quality", "zh": "数据质量"},
    "dashboard.no_trade_records_yet": {"en": "No trade records yet.", "zh": "暂无交易记录。"},
    "dashboard.dq_closed_exit": {"en": "Closed trades with exit data", "zh": "已有平仓数据"},
    "dashboard.dq_missing_exit": {"en": "Trades missing exit", "zh": "缺少平仓数据"},
    "dashboard.dq_charts_ok": {"en": "Charts available", "zh": "图表可用"},
    "dashboard.dq_charts_miss": {"en": "Trades missing candles", "zh": "缺少K线"},
    "dashboard.dq_usd_rel": {"en": "Reliable USD P/L", "zh": "美元盈亏可靠"},
    "dashboard.charts_incomplete_explainer": {
        "en": "Some trade charts are missing because local 1-minute candles are not available yet.",
        "zh": "部分交易图表缺失，因为本地尚无对应的1分钟K线。",
    },
    "dashboard.col_time": {"en": "Time", "zh": "时间"},
    "dashboard.col_direction": {"en": "Direction", "zh": "方向"},
    "dashboard.col_status": {"en": "Status", "zh": "状态"},
    "dashboard.col_entry": {"en": "Entry", "zh": "入场"},
    "dashboard.col_exit": {"en": "Exit", "zh": "平仓"},
    "dashboard.col_r": {"en": "R", "zh": "R"},
    "dashboard.col_chart": {"en": "Chart", "zh": "图表"},
    "dashboard.chart_png_link": {"en": "PNG", "zh": "PNG"},
    "dashboard.dev_engine_diagnostics_fold": {
        "en": "Developer / Engine Diagnostics",
        "zh": "开发者与引擎诊断",
    },
    "dashboard.diagnostics_fold": {
        "en": "Developer / Engine Diagnostics — expand",
        "zh": "开发者与引擎诊断 — 展开",
    },
    # paper
    "page.paper": {"en": "Paper Trading", "zh": "纸面交易"},
    "paper.sub": {
        "en": "PAPER ONLY / paper account. See whether a paper test is allowed, your test budget, and the intraday paper switch — without placing orders. Toggles only affect local flag files read by a separate automatic run process.",
        "zh": "仅纸面账户。可查看是否允许纸面测试、测试预算与日内纸开关—不会下单。开关只影响由独立自动进程读取的本地标志文件。",
    },
    "paper.strategy_flash": {"en": "Paper trading strategy", "zh": "纸面交易策略"},
    "paper.journal_review_card": {
        "en": "Latest Journal trade review",
        "zh": "最新一条 · 交易复盘",
    },
    "paper.journal_open_review": {"en": "Open trade review card", "zh": "打开复盘页"},
    "paper.journal_no_tid": {
        "en": "No trade id yet — run paper once or open the full Journal.",
        "zh": "尚无 stable trade id — 先跑纸面或打开完整流水。",
    },
    "paper.journal_hub": {"en": "Open full Journal", "zh": "打开交易流水"},
    "paper.review_latest_decision": {
        "en": "View latest trade (Trade Records)",
        "zh": "查看最新交易（交易记录）",
    },
    "paper.check_readiness": {
        "en": "Check Automatic Engine Readiness",
        "zh": "检查自动引擎就绪状态",
    },
    "paper.start_morning": {"en": "Start Morning Paper Engine", "zh": "启动早盘纸面引擎"},
    "paper.start_full": {"en": "Start Full-Day Paper Engine", "zh": "启动全日纸面引擎"},
    "paper.engine_title": {
        "en": "Automatic Paper Trading Engine (ICT/SMC — dashboard controls)",
        "zh": "自动纸面交易引擎（ICT/SMC — 在控制台区操作）",
    },
    "paper.h2_automatic_engine": {"en": "Automatic Paper Trading Engine", "zh": "自动纸面交易引擎"},
    "paper.btn_check_engine": {
        "en": "Check Automatic Engine Readiness",
        "zh": "检查自动引擎就绪状态",
    },
    "paper.btn_morning_engine": {"en": "Start Morning Paper Engine", "zh": "启动早盘纸面引擎"},
    "paper.btn_full_engine": {"en": "Start Full-Day Paper Engine", "zh": "启动全日纸面引擎"},
    "paper.kill_emergency": {"en": "Emergency stop (kill switch)", "zh": "紧急停止（kill switch）"},
    "paper.resume_kill": {"en": "Resume (remove kill switch)", "zh": "恢复（解除 kill switch）"},
    "paper.broker_snap_h": {"en": "Broker snapshot", "zh": "券商快照"},
    "paper.broker_snap_intro": {
        "en": "Read-only TWS truth from the explicit refresh button (same as Dashboard). Ledger rows are not broker positions until filled.",
        "zh": "与 Dashboard 相同：显式点击后才从 TWS 只读拉取。账本行在成交前≠真实持仓。",
    },
    "paper.broker_to_dashboard": {"en": "Go to Dashboard broker card", "zh": "前往控制台券商卡"},
    # reports
    "page.reports": {"en": "Reports", "zh": "报告"},
    "reports.sub": {
        "en": "Primary report center — full paper, research, backtest, and edge artifacts live here (read from disk). No broker when this page loads. Email is optional; Telegram is for short alerts only.",
        "zh": "主报告台 — 纸面、研究、回测、Edge 产物在此只读。本页不连经纪商。邮件可选；Telegram 仅短讯。",
    },
    "reports.todays_summary": {"en": "Today’s report summary", "zh": "今日报告摘要"},
    "reports.paper_reports": {"en": "Paper trading reports", "zh": "纸面交易报告"},
    "reports.backtest_edge": {"en": "Backtest & Edge reports", "zh": "回测与 Edge 报告"},
    "reports.telegram_email": {"en": "Telegram & email delivery", "zh": "Telegram 与邮件投递状态"},
    "reports.regen": {"en": "Regenerate reports (safe CLI)", "zh": "重新生成报告（安全 CLI）"},
    "reports.journal_analytics_title": {
        "en": "Trading journal analytics (local ledger)",
        "zh": "交易日记分析（本地台账）",
    },
    "reports.journal_analytics_intro": {
        "en": "Computed from paper order JSONL only — no broker on page load. Cumulative R uses closed trades; exit-hour buckets use exit timestamps; skipped rows use submitted-time hour for decision counts; USD curve only when every closed row has explicit realized USD.",
        "zh": "由纸单 JSONL 计算，页面加载不连经纪商。累计 R 仅含已平仓；平仓小时按平仓时刻美东分组；跳过笔按提交时刻美东分组（决策分布，非绩效）；美元曲线仅当每笔已平仓均有明确美元盈亏。",
    },
    "reports.equity_curve": {"en": "Equity curve (USD)", "zh": "资金曲线"},
    "reports.usd_equity_curve": {"en": "USD Equity Curve", "zh": "美元资金曲线"},
    "reports.cumulative_r_curve": {"en": "Cumulative R Curve", "zh": "累计R曲线"},
    "reports.cumulative_pnl": {"en": "Cumulative P/L", "zh": "累计盈亏"},
    "reports.drawdown_r": {"en": "Drawdown (R peak-to-trough)", "zh": "回撤（R）"},
    "reports.daily_r": {"en": "Daily R", "zh": "每日 R 值"},
    "reports.r_distribution": {"en": "R distribution", "zh": "R 值分布"},
    "reports.performance_symbol": {"en": "Performance by symbol (R)", "zh": "按标的表现（R）"},
    "reports.performance_hour": {"en": "Performance by hour (NY bucket, submitted time)", "zh": "按小时表现（美东分组，提交时间）"},
    "reports.performance_exit_hour": {"en": "Performance by Exit Hour", "zh": "按平仓小时表现"},
    "reports.performance_exit_hour_blurb": {
        "en": "Closed trades only: ΣR by America/New_York hour at exit time.",
        "zh": "仅已平仓：按平仓时刻的美东小时汇总的 R。",
    },
    "reports.decisions_by_submitted_hour": {
        "en": "Decisions by Submitted Hour",
        "zh": "按提交小时的决策分布",
    },
    "reports.decisions_by_submitted_hour_blurb": {
        "en": "Skipped rows only: count by NY hour at engine submit time (not performance).",
        "zh": "仅统计已跳过：按引擎提交时刻的美东小时计数（非绩效曲线）。",
    },
    "reports.skipped_reasons_report": {"en": "Skipped reasons", "zh": "跳过原因"},
    "reports.not_enough_closed_trades": {
        "en": "Not enough closed trades yet.",
        "zh": "已平仓样本不足。",
    },
    "reports.not_enough_closed_trades_detail": {
        "en": "Not enough closed trades yet. R and drawdown analytics will appear after closed trades are recorded.",
        "zh": "已平仓样本不足。记录到平仓交易后，R值与回撤分析会显示。",
    },
    "reports.charts_missing_explainer": {
        "en": "Some trade charts are missing because local 1-minute candles are not available yet.",
        "zh": "部分交易图表缺失，因为本地尚无对应的1分钟K线。",
    },
    "reports.pnl_unavailable_note": {
        "en": "USD equity curve is hidden because reliable realized USD P/L is not available for every closed trade.",
        "zh": "由于并非每笔已平仓交易都有可靠的美元已实现盈亏，美元资金曲线暂不显示。",
    },
    "reports.journal_block": {
        "en": "Paper Journal (latest rows)",
        "zh": "纸面流水（节选）",
    },
    "reports.journal_skipped": {
        "en": "Skipped reasons (readable)",
        "zh": "跳过原因（可读摘要）",
    },
    "reports.latest_trade_reviews": {
        "en": "Latest trade reviews",
        "zh": "最新交易复盘快捷入口",
    },
    "reports.jr_latest_sent": {"en": "Latest sent ·", "zh": "最新已提交 ·"},
    "reports.jr_latest_skipped": {"en": "Latest skipped ·", "zh": "最新跳过 ·"},
    "reports.jr_latest_incomplete": {"en": "Latest incomplete protection ·", "zh": "最新保护不完整 ·"},
    # backtest
    "page.backtest": {"en": "Backtest", "zh": "回测"},
    "backtest.sub": {
        "en": "RESEARCH-ONLY VIEW. This page reads files from backtest CLI. It never connects to TWS. Buttons run allowlisted CLI only — no orders, no live trading.",
        "zh": "仅研究展示。本页读取回测 CLI 落盘，不连 TWS。按钮仅白名单 CLI — 不下单、不启用实盘。",
    },
    "backtest.data_needed": {"en": "Data needed for this backtest", "zh": "本回测所需数据"},
    "backtest.check_coverage": {"en": "Check Data Coverage", "zh": "检查数据覆盖"},
    "backtest.fetch_run": {
        "en": "Fetch missing data & run backtest",
        "zh": "拉取缺失数据并运行回测",
    },
    "backtest.btn_check_coverage": {"en": "Check Data Coverage", "zh": "检查数据覆盖"},
    "backtest.btn_fetch_run": {
        "en": "Fetch Missing Data & Run Backtest",
        "zh": "拉取缺失数据并运行回测",
    },
    "backtest.local_cache": {"en": "Local cache & Git", "zh": "本地缓存与 Git"},
    # settings
    "page.settings": {"en": "Settings", "zh": "设置"},
    "settings.sub": {
        "en": "Read-only view of safety configuration. Editing still uses config/*.yaml.",
        "zh": "只读安全与配置。编辑仍走 config/*.yaml。",
    },
    "settings.bg_runner": {"en": "Background Auto Runner (macOS)", "zh": "后台自动运行器（macOS）"},
    "settings.tg_listener": {"en": "Telegram Command Listener", "zh": "Telegram 命令监听器"},
    "settings.data_email": {"en": "Telegram, email & news", "zh": "数据与 Telegram / 邮件 / 新闻"},
    "settings.h2_safety": {"en": "Safety", "zh": "安全"},
    "settings.h2_allowlist": {"en": "Allowlisted UI commands", "zh": "已允许的 UI 命令"},
    "settings.h2_runtime": {"en": "Runtime flags", "zh": "运行态标志"},
    # other page titles
    "page.journal": {"en": "Journal", "zh": "交易日志"},
    "page.research_p": {"en": "Research", "zh": "研究"},
    "page.strategies_p": {"en": "Strategies", "zh": "策略"},
    "page.edge_p": {"en": "Edge", "zh": "Edge"},
    "page.signals_p": {"en": "Signals", "zh": "信号"},
    "page.watchlist_p": {"en": "Watchlist", "zh": "自选股"},
    "watchlist.subtitle": {
        "en": "Latest data/watchlists/*-dynamic-watchlist.json. GET /watchlist does not open IBKR/TWS.",
        "zh": "读取最新 data/watchlists/*-dynamic-watchlist.json。打开本页面不会连接 IBKR/TWS。",
    },
    "watchlist.card_how": {"en": "What you are seeing", "zh": "本页含义"},
    "watchlist.criteria_intro": {
        "en": "Selection comes from config/watchlist.yaml: a fixed static core plus optional buckets (liquidity, relative volume, volatility) when IBKR daily bars exist.",
        "zh": "标的来自 config/watchlist.yaml：固定 static_core；若存在 IBKR 日线再合并成交量/量比/波动等分层。",
    },
    "watchlist.criteria_univ": {
        "en": "Universe defaults: liquid US tech / semiconductor / mega-cap plus index ETFs (SPY/QQQ/…) for ICT/SMC scanning & backtests — research only.",
        "zh": "默认偏美股高流动性科技与半导体等大盘 + 指数 ETF（如 SPY/QQQ），作为 ICT/SMC 扫描与回测股票池 — 仅研究用途。",
    },
    "watchlist.price_not_on_load": {
        "en": "Prices are not fetched on page load. Latest price / Rel vol show only if the saved JSON already contains them (from a build that pulled daily bars).",
        "zh": "页面加载不会拉价。只有 JSON 里已有字段时才会显示最新价/量比（来自曾拉取过日线的构建）。",
    },
    "watchlist.refresh_how": {
        "en": "To fill metrics, run an explicit allowlisted rebuild with IBKR read-only daily bars (button below). No market orders; no strategy engine.",
        "zh": "要补全指标，请点下面白名单按钮，用 IBKR 只读日线重建；无市价单、不跑策略引擎。",
    },
    "watchlist.type_label": {"en": "Watchlist file `source`", "zh": "文件字段 source"},
    "watchlist.type_static": {
        "en": "static — offline build (no IBKR daily bars in that run). Price columns are usually —.",
        "zh": "static — 离线构建（该次未拉 IBKR 日线）。价格列多为 —。",
    },
    "watchlist.type_ibkr": {
        "en": "ibkr — that run connected for read-only daily bars; prices/Rel vol populated when bars succeeded.",
        "zh": "ibkr — 该次构建了只读日线；成功拉到 K 线时才会填价格/量比。",
    },
    "watchlist.reason_static_core": {
        "en": "Reason \"static_core\" means the symbol is always included from config static_core (mega-cap / index core).",
        "zh": "Reason 列为 static_core：来自配置里的固定核心池（大盘股/指数等）。",
    },
    "watchlist.rebuild_semantics": {
        "en": "If every row shows only static_core and you rebuild without `--ibkr`, symbols may stay the same — JSON file still updates timestamp and Recent commands shows OK.",
        "zh": "若各行仅有 static_core 且离线重建，符号集可能不变；文件时间仍会更新，“最近命令”也会显示 OK。",
    },
    "watchlist.last_file": {"en": "File on disk", "zh": "磁盘文件"},
    "watchlist.row_count": {"en": "{n} symbols in table", "zh": "表格共 {n} 个标的"},
    "watchlist.symbol_price_stats": {
        "en": "{shown} / {total} symbols with latest_price in JSON",
        "zh": "JSON 含 latest_price：{shown} / {total}",
    },
    "watchlist.missing_metrics": {"en": "Batch missing_data", "zh": "缺失字段摘要"},
    "watchlist.h2_actions": {"en": "Actions", "zh": "操作"},
    "watchlist.btn_rebuild": {"en": "Rebuild watchlist (offline)", "zh": "重建自选股（离线）"},
    "watchlist.btn_rebuild_lim": {"en": "Rebuild (offline, limit 30)", "zh": "离线重建（上限 30）"},
    "watchlist.btn_ibkr": {
        "en": "Rebuild with IBKR daily bars (read-only)",
        "zh": "用 IBKR 日线重建（只读行情）",
    },
    "watchlist.btn_ibkr_note": {
        "en": "Uses the same CLI as dashboard: fills latest_price / rel vol when TWS/paper is reachable. Never places trades.",
        "zh": "与控制台一致：纸面 TWS 可达时才填最新价与量比；不发单。",
    },
    "watchlist.th_symbol": {"en": "Symbol", "zh": "标的"},
    "watchlist.th_price": {"en": "Latest price", "zh": "最新价"},
    "watchlist.th_rv": {"en": "Rel vol", "zh": "量比"},
    "watchlist.th_reason": {"en": "Reason", "zh": "原因"},
    "watchlist.th_blocked": {"en": "Blocked", "zh": "被拦"},
    "page.logs_p": {"en": "Logs", "zh": "运行日志"},
    "page.journal_t": {"en": "Trade Journal (audit log)", "zh": "交易流水（审计）"},
    "page.trades": {"en": "Trade Records", "zh": "交易记录"},
    "journal.trade_records_banner": {
        "en": "For trader-friendly trade records and charts, open Trade Records.",
        "zh": "如需按交易查看图表与进出场，请打开「交易记录」。",
    },
    "trades.sub": {
        "en": "Normalized from local paper_orders JSONL only — UI does not connect to IBKR.",
        "zh": "由本地 paper_orders JSONL 归一化 — 页面加载不会连接 IBKR。",
    },
    "trades.summary_submitted": {"en": "Submitted rows", "zh": "已提交笔数"},
    "trades.summary_open": {"en": "Open", "zh": "持仓中"},
    "trades.summary_closed": {"en": "Closed", "zh": "已平仓"},
    "trades.summary_skipped": {"en": "Skipped", "zh": "已跳过"},
    "trades.summary_incomplete": {"en": "Protection incomplete", "zh": "保护不完整"},
    "trades.summary_charts": {"en": "Charts available", "zh": "已有图表"},
    "trades.summary_missing_candles": {"en": "Missing candles", "zh": "缺少 K 线"},
    "trades.summary_total": {"en": "Total records", "zh": "记录总数"},
    "trades.summary_pending": {"en": "Pending (TWS)", "zh": "待处理（已交券商）"},
    "trades.summary_realized_sum": {"en": "Σ Realized R (closed)", "zh": "已平仓合计 R"},
    "trades.status_pending": {"en": "Pending", "zh": "待处理"},
    "trades.cr_not_recorded": {"en": "Not recorded / open", "zh": "未记录 / 持仓"},
    "trades.cr_target_hit": {"en": "Target-like exit", "zh": "目标价附近止盈"},
    "trades.cr_stop_hit": {"en": "Stop-like exit", "zh": "止损价附近平仓"},
    "trades.cr_manual": {"en": "Manual / discretionary", "zh": "手动 / 择机"},
    "trades.cr_eod": {"en": "Session / EOD", "zh": "收盘 / 会话结束"},
    "trades.cr_unknown": {"en": "Unknown reason", "zh": "原因未知"},
    "trades.f_open": {"en": "Open", "zh": "持仓"},
    "trades.filter_closed": {"en": "Closed", "zh": "已平仓"},
    "trades.apply_filters": {"en": "Apply filters", "zh": "筛选"},
    "trades.dir_all": {"en": "All directions", "zh": "全部方向"},
    "trades.chart_all_short": {"en": "All charts", "zh": "全部图表"},
    "trades.chart_avail_opt": {"en": "Has chart PNG", "zh": "已有图表"},
    "trades.chart_miss_opt": {"en": "Missing candles", "zh": "缺少 K 线缓存"},
    "trades.chart_none_opt": {"en": "No chart PNG", "zh": "尚无图表"},
    "trades.lbl_close_classification": {"en": "Close classification", "zh": "平仓类型"},
    "trades.header_title": {"en": "Trade header", "zh": "本笔概要"},
    "trades.trade_scope_note": {"en": "Single-trade view · no unrelated symbols", "zh": "仅本笔视图 · 不混入其他标的"},
    "trades.price_plan": {"en": "Price plan", "zh": "价格计划"},
    "trades.lbl_exit_px": {"en": "Exit price", "zh": "平仓价"},
    "trades.chart_rules_hint": {"en": "Chart uses cached 1m candles only · labels from saved trade row · no broker calls.", "zh": "图表仅本地 1 分钟缓存；标签来自存档；不连接经纪商。"},
    "trades.ict_labels_section": {"en": "ICT / SMC labels", "zh": "ICT / SMC 标记"},
    "trades.section_notes": {"en": "Notes", "zh": "笔记"},
    "trades.notes_placeholder": {"en": "Manual notes and tags support can be added later (local-first).", "zh": "本地笔记 / 标签可后续按需添加。"},
    "trades.raw_sizing_fold": {"en": "Sizing / sizing-related JSON", "zh": "仓位与 sizing JSON"},
    "trades.col_view_trade": {"en": "View Trade", "zh": "查看本笔交易"},
    "trades.th_submitted": {"en": "Submitted", "zh": "提交时间"},
    "trades.th_symbol": {"en": "Symbol", "zh": "标的"},
    "trades.th_direction": {"en": "Direction", "zh": "方向"},
    "trades.th_local_state": {"en": "Local state", "zh": "本地状态"},
    "trades.th_broker_state": {"en": "Broker state", "zh": "券商侧状态"},
    "trades.ls_skipped_decision": {"en": "Skipped decision", "zh": "已跳过决策"},
    "trades.ls_closed_trade": {"en": "Closed (recorded)", "zh": "已平仓（账本）"},
    "trades.ls_protection_incomplete": {"en": "Protection incomplete", "zh": "保护不完整"},
    "trades.ls_rejected": {"en": "Rejected", "zh": "已拒绝"},
    "trades.ls_pending_local": {"en": "Pending (submitted to broker)", "zh": "待确认（已交券商）"},
    "trades.ls_sent_to_broker_local": {"en": "Sent to broker (record)", "zh": "已发送至券商（账本）"},
    "trades.ls_submitted_local_open": {"en": "Submitted (open record)", "zh": "已提交（未平仓账本）"},
    "trades.ls_open_unknown": {"en": "Open / unclear", "zh": "持仓 / 未定"},
    "trades.ls_unknown_local": {"en": "Unknown", "zh": "未知"},
    "trades.bs_not_checked": {"en": "Not checked", "zh": "尚未核对"},
    "trades.bs_broker_unavailable": {"en": "Broker snapshot unavailable", "zh": "券商快照不可用"},
    "trades.bs_broker_error": {"en": "Broker snapshot error", "zh": "券商快照错误"},
    "trades.bs_unknown": {"en": "Unknown", "zh": "未知"},
    "trades.bs_position_confirmed": {"en": "Position confirmed", "zh": "券商确认持仓"},
    "trades.bs_has_open_orders": {"en": "Open orders @ broker", "zh": "券商侧未完成委托"},
    "trades.bs_flat_no_position": {"en": "No position / flat", "zh": "无持仓 / 已平"},

    "trades.th_entry": {"en": "Entry", "zh": "入场"},
    "trades.th_exit": {"en": "Exit", "zh": "平仓"},
    "trades.th_stop": {"en": "Stop", "zh": "止损"},
    "trades.th_target": {"en": "Target", "zh": "目标"},
    "trades.th_planned_rr": {"en": "Planned R/R", "zh": "计划风报比"},
    "trades.th_result_r": {"en": "Result R", "zh": "实际 R 值"},
    "trades.th_reason": {"en": "Reason", "zh": "原因"},
    "trades.th_chart": {"en": "Chart", "zh": "图表"},
    "trades.view_trade": {"en": "View Trade", "zh": "查看本笔交易"},
    "trades.link_chart": {"en": "Chart", "zh": "图表"},
    "trades.link_missing_candles": {"en": "Missing candles", "zh": "缺少 K 线"},
    "trades.exit_not_recorded": {"en": "Exit not recorded yet", "zh": "尚未记录平仓"},
    "trades.col_submitted_short": {"en": "Submitted", "zh": "提交"},
    "trades.status_open": {"en": "Open", "zh": "持仓中"},
    "trades.status_closed": {"en": "Closed", "zh": "已平仓"},
    "trades.status_skipped": {"en": "Skipped", "zh": "已跳过"},
    "trades.status_rejected": {"en": "Rejected", "zh": "已拒绝"},
    "trades.status_protection_incomplete": {"en": "Protection incomplete", "zh": "保护不完整"},
    "trades.status_partial": {"en": "Partial", "zh": "部分提交"},
    "trades.status_unknown": {"en": "Unknown", "zh": "未知"},
    "trades.status_filled_open": {"en": "Filled open", "zh": "已成交未平"},
    "trades.status_submitted_not_filled": {"en": "Submitted, not filled", "zh": "已报未成交"},
    "trades.status_reconciliation_unknown": {"en": "Unknown / unmatched", "zh": "未知/未匹配"},
    "trades.th_entry_fill": {"en": "Entry fill (TWS)", "zh": "入场成交"},
    "trades.th_exit_fill": {"en": "Exit fill (TWS)", "zh": "出场成交"},
    "trades.recon_run_hint": {
        "en": "Run Fill Reconciliation to confirm fills from TWS.",
        "zh": "请运行「成交对账」以确认 TWS 成交。",
    },
    "trades.audit_journal": {"en": "Open audit log (Journal)", "zh": "打开技术流水（Journal）"},
    "trades.complete_charts_h": {"en": "Complete trade charts", "zh": "补齐交易复盘图"},
    "trades.complete_charts_blurb": {
        "en": "Local-only: generate PNGs from cached 1m candles. Fetch mode: read-only IBKR bars for missing cache days — explicit click only; never on normal page load.",
        "zh": "本地模式：用已有本地 1 分钟缓存生成 PNG。补齐模式：对缺失缓存日只读拉取 IBKR — 仅在显式点击时使用；不会在普通浏览页面自动请求。",
    },
    "trades.complete_charts_local_btn": {
        "en": "Generate charts from local candles",
        "zh": "用本地K线生成交易图",
    },
    "trades.complete_charts_fetch_btn": {
        "en": "Fetch IBKR 1m candles for traded tickers & generate charts (read-only, no orders)",
        "zh": "从 IBKR 补齐已交易标的 1分钟K线并生成图（只读·不下单）",
    },
    "trades.complete_charts_safe_blurb": {
        "en": "Read-only IBKR historical bars (separate client-id roster). No orders. No trading engine. Use explicit buttons or CLI/EOD only — not on page GET.",
        "zh": "IBKR 仅为只读历史数据（独立 client id）。不下单、不启动交易引擎。仅在显式按钮或 CLI/收市报告流程中拉取；页面打开不会连接 IBKR。",
    },
    "trades.chart_completion_action": {
        "en": "No local candles yet. Click “Complete trade charts” (local or IBKR read-only mode), then reload.",
        "zh": "暂无本地 K 线。请点击「补齐交易复盘图」（本地或 IBKR 只读补齐），完成后再刷新本页。",
    },
    "trades.detail_no_candles_yet": {
        "en": "No local candles yet. Click Complete Trade Charts on /trades or /reports to fetch IBKR 1m candles for traded symbols (read-only), then reload.",
        "zh": "尚无本地 K 线。请在「交易记录」或「报告」页点击「补齐交易复盘图」，从 IBKR 只读拉取已交易标的的 1 分钟 K 线并生成图，然后刷新。",
    },
    "trades.back_list": {"en": "← Trade Records", "zh": "← 交易记录"},
    "trades.sec_summary": {"en": "1 · Trade summary", "zh": "1 · 交易摘要"},
    "trades.sec_chart": {"en": "2 · Trade chart", "zh": "2 · 交易图表"},
    "trades.sec_timeline_heading": {"en": "3 · Execution timeline", "zh": "3 · 执行时间线"},
    "trades.execution_timeline": {"en": "Execution timeline", "zh": "执行时间线"},
    "trades.sec_risk": {"en": "4 · Risk plan & brackets", "zh": "4 · 风险与括号"},
    "trades.sec_ict": {"en": "5 · ICT / SMC context", "zh": "5 · ICT / SMC 上下文"},
    "trades.sec_engine": {"en": "6 · Engine decision", "zh": "6 · 引擎决策"},
    "trades.sec_protection": {"en": "Bracket protection", "zh": "括号保护"},
    "trades.sec_raw": {"en": "7 · Raw audit (collapsed)", "zh": "7 · 原始审计（折叠）"},
    "trades.lbl_entry_time": {"en": "Entry time", "zh": "入场时间"},
    "trades.lbl_exit_time": {"en": "Exit time", "zh": "平仓时间"},
    "trades.lbl_submitted_at": {"en": "Submitted at", "zh": "提交时间"},
    "trades.strategy": {"en": "Strategy", "zh": "策略"},
    "trades.not_found": {"en": "Trade not found", "zh": "未找到该笔交易"},
    "reports.link_trade_records": {"en": "Trade Records (trader view)", "zh": "交易记录（按笔复盘）"},
    "reports.latest_open": {"en": "Latest open (ledger)", "zh": "最近一笔持仓中"},
    "reports.latest_closed": {"en": "Latest closed (ledger)", "zh": "最近一笔已平仓"},
    "reports.latest_skipped": {"en": "Latest skipped (ledger)", "zh": "最近一笔已跳过"},
    "reports.latest_submitted": {"en": "Latest submitted row", "zh": "最近一条已提交"},
    "reports.ledger_card_title": {"en": "Latest Trade Records", "zh": "最新交易记录"},
    "reports.ledger_missing_candles_hint": {
        "en": "Approx. rows missing candle cache for chart: {n}",
        "zh": "约 {n} 笔缺少制图用本地 K 线",
    },
    "reports.open_trade_records": {"en": "Open Trade Records (/trades)", "zh": "打开交易记录（/trades）"},
    "reports.complete_trade_charts_title": {"en": "Complete trade charts", "zh": "补齐交易复盘图"},
    "reports.complete_trade_charts_blurb": {
        "en": "Explicit allowlisted CLI — no automatic IBKR on page views.",
        "zh": "显式允许的 CLI — 不会在页面浏览时自动连 IBKR。",
    },
    "journal.paper_only": {"en": "Paper account only", "zh": "仅纸面账户"},
    "journal.sub_readonly": {
        "en": (
            "A readable log of what the engine tried: sent to TWS, skipped, "
            "or incomplete protection — from local files only; "
            "no broker connection on load and no order buttons."
        ),
        "zh": (
            "引擎动作的只读摘要：发往 TWS、跳过或保护不完整。"
            "仅读本地文件；加载时不连券商，且无下单按钮。"
        ),
    },
    "journal.card_paper_submissions": {"en": "Paper submissions", "zh": "纸面提交条数"},
    "journal.no_jsonl": {
        "en": "No paper-bracket JSONL yet. Run the intraday paper loop once to populate.",
        "zh": "尚无纸面条目 JSONL。运行一次盘中纸面循环后会写入。",
    },
    "journal.card_backtest_latest": {"en": "Backtest trades (latest)", "zh": "回测成交（最新）"},
    "journal.no_backtest_csv": {"en": "No backtest trades CSV.", "zh": "无回测 trades CSV。"},
    "journal.h2_paper": {"en": "Paper bracket submissions", "zh": "纸面括号下单记录"},
    "journal.filter_view": {"en": "View", "zh": "视图"},
    "journal.f_all": {"en": "All", "zh": "全部"},
    "journal.f_sent": {"en": "Sent", "zh": "已提交"},
    "journal.f_skipped": {"en": "Skipped", "zh": "跳过"},
    "journal.f_incomplete": {"en": "Protection incomplete", "zh": "保护不完整"},
    "journal.f_long": {"en": "Long", "zh": "做多"},
    "journal.f_short": {"en": "Short", "zh": "做空"},
    "journal.f_has_chart": {"en": "Has chart", "zh": "有图"},
    "journal.f_no_chart": {"en": "No chart", "zh": "无图"},
    "journal.f_today": {"en": "Today only (NY)", "zh": "仅今日（NY）"},
    "journal.f_last_session": {"en": "Last NY session date", "zh": "上一交易日（NY）"},
    "journal.by_symbol": {"en": "By symbol", "zh": "按标的"},
    "journal.empty_hint": {
        "en": "Nothing here yet. Safe paper-engine runs populate this Journal from audit JSONL.",
        "zh": "暂无记录。只有通过安全审计 JSONL 的纸面运行才会写入。",
    },
    "journal.col_time": {"en": "Time", "zh": "时间"},
    "journal.col_symbol": {"en": "Symbol", "zh": "标的"},
    "journal.col_direction": {"en": "Dir", "zh": "方向"},
    "journal.col_mode": {"en": "Mode", "zh": "模式"},
    "journal.col_status": {"en": "Status", "zh": "状态"},
    "journal.col_entry": {"en": "Entry", "zh": "入场"},
    "journal.col_stop": {"en": "Stop", "zh": "止损"},
    "journal.col_target": {"en": "Target", "zh": "目标"},
    "journal.col_qty": {"en": "Qty", "zh": "数量"},
    "journal.col_notional": {"en": "Notional", "zh": "名义金额"},
    "journal.col_protection": {"en": "Protection", "zh": "保护"},
    "journal.col_edge": {"en": "Edge", "zh": "Edge"},
    "journal.col_reason": {"en": "Reason", "zh": "原因"},
    "journal.col_chart": {"en": "Chart", "zh": "图表"},
    "journal.review": {"en": "View Trade", "zh": "查看本笔交易"},
    "journal.details_engine": {
        "en": "Sizing / ticks / IDs / raw technical details",
        "zh": "仓位细节 / Tick / ID / 引擎原始明细",
    },
    "journal.show_sizing_details": {
        "en": "Show sizing details",
        "zh": "展开仓位细节",
    },
    "journal.footer_note": {
        "en": "Showing the {n} most recent rows · paper-only · no live path from this page.",
        "zh": "显示最近 {n} 条 · 仅纸面 · 本页无实盘路径。",
    },
    "journal.h2_backtest": {"en": "Backtest trades (latest)", "zh": "回测成交（最新）"},
    "journal.backtest_empty": {
        "en": "No backtest trades — run backtest-intraday-smc first.",
        "zh": "无回测成交 — 请先运行 backtest-intraday-smc。",
    },
    "journal.trade_title": {"en": "Trade review", "zh": "交易复盘"},
    "journal.trade_back": {"en": "Back to Journal", "zh": "返回流水"},
    "journal.trade_identity": {"en": "Identity", "zh": "标识"},
    "journal.trade_prices": {"en": "Prices & size", "zh": "价格与规模"},
    "journal.trade_decision": {"en": "Engine decision", "zh": "引擎决策"},
    "journal.trade_ict": {"en": "ICT chain", "zh": "ICT 链"},
    "journal.trade_edge": {"en": "Edge", "zh": "Edge"},
    "journal.trade_protection": {"en": "Bracket protection", "zh": "括号保护"},
    "journal.trade_chart": {"en": "Chart (local candles)", "zh": "图表（本地 K 线）"},
    "journal.trade_generate_chart": {
        "en": "Generate trade chart (local cache only; no IBKR on click)",
        "zh": "生成本地复盘图（仅用缓存；点击不连 IBKR）",
    },
    "journal.trade_no_candles": {
        "en": "No local candle data for this trade. Use Backtest / Data Coverage to fetch candles when you choose to.",
        "zh": "本地无该笔 K 线。请在需要时通过回测/数据覆盖页拉取（非自动）。",
    },
    "journal.trade_links": {"en": "Links", "zh": "链接"},
    "journal.not_found": {"en": "Trade not found in local journal files.", "zh": "本地日志中找不到该笔交易。"},
    "journal.link_chart": {"en": "Chart", "zh": "图表"},
    "journal.link_generate": {"en": "Generate", "zh": "生成"},
    "journal.status_skipped": {"en": "Skipped", "zh": "已跳过"},
    "journal.status_partial": {"en": "Partial (TWS)", "zh": "部分（已交券商）"},
    "journal.status_protection_incomplete": {
        "en": "Protection incomplete",
        "zh": "保护不完整",
    },
    "journal.status_sent": {"en": "Sent", "zh": "已提交"},
    "journal.status_unknown": {"en": "Unknown", "zh": "未知"},
    "journal.hdr_time": {"en": "When", "zh": "时间"},
    "journal.hdr_status": {"en": "Status", "zh": "状态"},
    "journal.hdr_strategy": {"en": "Strategy", "zh": "策略"},
    "journal.hdr_mode": {"en": "Signal mode", "zh": "信号模式"},
    "journal.trade_header": {"en": "Summary", "zh": "摘要"},
    "journal.sec_trade_plan": {"en": "Trade plan", "zh": "计划"},
    "journal.lbl_entry": {"en": "Entry", "zh": "入场"},
    "journal.lbl_stop": {"en": "Stop", "zh": "止损"},
    "journal.lbl_target": {"en": "Target", "zh": "目标"},
    "journal.lbl_risk_per_share": {"en": "Risk/share", "zh": "每股风险"},
    "journal.lbl_reward_per_share": {"en": "Reward/share", "zh": "每股盈亏"},
    "journal.lbl_rr": {"en": "R/R plan", "zh": "计划盈亏比"},
    "journal.lbl_submitted_yesno": {"en": "Submitted to broker?", "zh": "是否已向券商提交？"},
    "journal.lbl_reason_short": {"en": "Reason", "zh": "原因"},
    "journal.lbl_broker_line": {"en": "Broker", "zh": "券商"},
    "journal.lbl_htf": {"en": "HTF", "zh": "大周期"},
    "journal.lbl_5m": {"en": "5m", "zh": "5 分钟"},
    "journal.lbl_1m": {"en": "1m", "zh": "1 分钟"},
    "journal.chk_yes": {"en": "yes", "zh": "满足"},
    "journal.chk_missing": {"en": "missing", "zh": "缺失"},
    "journal.edge_score": {"en": "Edge score", "zh": "Edge 分数"},
    "journal.edge_unavailable": {
        "en": "Edge detail not available for this record.",
        "zh": "本行无 Edge 细节。",
    },
    "journal.protection_complete": {"en": "Protection complete", "zh": "保护完整"},
    "journal.protection_incomplete_warn": {
        "en": "Urgent — verify parent / stop / target legs in TWS.",
        "zh": "紧急：请在 TWS 核对父单/止损/止盈腿。",
    },
    "journal.not_submitted": {"en": "No broker submission on this row.", "zh": "此行未向券商提交。"},
    "journal.alt_chart": {"en": "trade review chart", "zh": "交易复盘图"},
    "journal.chart_local_only_hint": {
        "en": "Generates a PNG from your local 1m cache only (no IBKR on click).",
        "zh": "仅用本地 1 分钟缓存生成 PNG（点击不连 IBKR）。",
    },
    "journal.generate_chart_btn": {"en": "Generate Chart", "zh": "生成复盘图"},
    "journal.chart_file_updated": {
        "en": "Chart file updated from local candles.",
        "zh": "已用本地蜡烛更新图表文件。",
    },
    "journal.chart_cell_available_short": {
        "en": "Chart available",
        "zh": "图表可用",
    },
    "journal.chart_missing_candles_short": {
        "en": "No candles",
        "zh": "缺少K线",
    },
    "journal.chart_cell_pending": {
        "en": "Chart pending",
        "zh": "图表等待中",
    },
    "journal.cli_generate_trade_charts_hint": {
        "en": "Batch: python3 -m bot.cli generate-trade-charts --latest --limit 50 --json",
        "zh": "批量：generate-trade-charts --latest（仅本地蜡烛）",
    },
    "reports.trade_chart_batch_title": {
        "en": "Journal trade charts (latest batch)",
        "zh": "交易复盘图（最近一次批量）",
    },
    "reports.generate_trade_charts_hint": {
        "en": "CLI: generate-trade-charts --latest --limit N --json (local candles only).",
        "zh": "CLI：generate-trade-charts --latest（仅本地蜡烛）。",
    },
    "journal.generate_trade_charts_label": {
        "en": "Generate trade charts",
        "zh": "生成交易复盘图",
    },
    "journal.trade_charts_generated_note": {
        "en": "Trade charts generated",
        "zh": "交易复盘图已生成",
    },
    "journal.missing_candle_data_note": {
        "en": "Missing candle data",
        "zh": "缺少本地K线数据",
    },
    "paper.latest_chart_status": {
        "en": "Latest journal trade chart",
        "zh": "最近一条流水复盘图",
    },
    "paper.chart_status_available": {
        "en": "Chart available",
        "zh": "图表可用",
    },
    "paper.chart_status_missing_candles": {
        "en": "Missing local candles",
        "zh": "缺少本地K线",
    },
    "paper.chart_status_pending": {
        "en": "Chart pending",
        "zh": "图表等待中",
    },
    "paper.chart_status_na": {
        "en": "Not applicable",
        "zh": "不适用",
    },
    "journal.cli_fetch_note": {
        "en": "Use fetch-candles only when you explicitly want IBKR-read-only cache fill.",
        "zh": "仅在主动需要时用 fetch-candles（只读缓存）补数据。",
    },
    "journal.open_reports_link": {"en": "Open Reports", "zh": "打开报告"},
    "journal.open_paper_link": {"en": "Open Paper", "zh": "打开纸面"},
    "journal.verify_tws": {"en": "Verify in TWS", "zh": "请在 TWS 核对"},
    "journal.raw_skip_summary": {"en": "Raw skip strings", "zh": "原始跳过文本"},
    "journal.raw_audit_summary": {"en": "Raw audit", "zh": "原始审计"},
    "journal.sizing_audit_block": {"en": "Sizing audit JSON", "zh": "仓位审计 JSON"},
    "journal.raw_row_heading": {"en": "Truncated journal row JSON", "zh": "截取后的流水 JSON"},
    "journal.yes": {"en": "yes", "zh": "是"},
    "journal.no": {"en": "no", "zh": "否"},
    "page.edge_t": {"en": "Ticker edge profiles", "zh": "标的 Edge 画像"},
    "page.strategies_t": {"en": "Strategies — Control Center", "zh": "策略中心"},
    "page.notfound": {"en": "Not Found", "zh": "未找到"},
}


def get_locale(request: Request) -> str:
    q = (request.query_params.get("lang") or "").strip().lower()
    if q in SUPPORTED:
        return q
    c = (request.cookies.get(COOKIE_NAME) or "").strip().lower()
    if c in SUPPORTED:
        return c
    return "en"


def t(key: str, locale: str, **kwargs: str | float) -> str:
    loc = locale if locale in SUPPORTED else "en"
    entry = M.get(key)
    if not entry:
        return key
    text = entry.get(loc) or entry.get("en") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def _normalize_href_to_path(href: Any) -> str:
    """Starlette `url_for` can return a URL; templates also pass /path str."""
    if href is None:
        return "/"
    if hasattr(href, "path"):
        p = (href.path or "/").rstrip() or "/"
        q = getattr(href, "query", None)
        if not q:
            return p
        if isinstance(q, (bytes, bytearray)):
            q = q.decode("utf-8", errors="replace")
        return f"{p}?{q}"
    t = str(href).strip()
    if t.startswith("http://") or t.startswith("https://"):
        u = urlparse(t)
        out = (u.path or "/") or "/"
        return f"{out}?{u.query}" if u.query else out
    return t


def append_lang_to_path(path: Any, loc: str) -> str:
    """Ensure path (may include ?query) has lang= for forms and redirects."""
    path = _normalize_href_to_path(path)
    if not path.startswith("/"):
        path = "/" + path
    if "?" in path:
        base, qstr = path.split("?", 1)
        q = dict(parse_qsl(qstr, keep_blank_values=True))
    else:
        base, q = path, {}
    q["lang"] = loc if loc in SUPPORTED else "en"
    return base + "?" + urlencode(q)


def lang_switch_href(request: Request, target_lang: str) -> str:
    if target_lang not in SUPPORTED:
        target_lang = "en"
    path = request.url.path or "/"
    q = dict(request.query_params)
    q["lang"] = target_lang
    return path + "?" + urlencode(q)
