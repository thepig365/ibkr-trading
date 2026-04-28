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
    "nav.dashboard": {"en": "Dashboard", "zh": "控制台"},
    "nav.research": {"en": "Research", "zh": "研究"},
    "nav.watchlist": {"en": "Watchlist", "zh": "自选股"},
    "nav.signals": {"en": "Signals", "zh": "信号"},
    "nav.backtest": {"en": "Backtest", "zh": "回测"},
    "nav.edge": {"en": "Edge", "zh": "Edge"},
    "nav.paper": {"en": "Paper Trading", "zh": "纸面交易"},
    "nav.journal": {"en": "Journal", "zh": "日志"},
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
    "page.dashboard": {"en": "Dashboard", "zh": "控制台"},
    "dashboard.sub": {
        "en": "Your day at a glance — everything here is from saved files or your last approved safe action (button). No broker connection when this page loads. Nothing runs until you click a button.",
        "zh": "今日一览 — 数据来自已保存文件或你上次点击的「已批准安全操作」。本页加载时不连接经纪商。未点击按钮前不会执行任何操作。",
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
        "en": "Review latest paper decision",
        "zh": "复盘最近一条纸面决策",
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
    "page.journal_t": {"en": "Trade Journal", "zh": "交易流水"},
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
    "journal.review": {"en": "Review", "zh": "复盘"},
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
