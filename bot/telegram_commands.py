"""Telegram command interface (Prompt 9.2, Part B).

This module accepts a **small, safe** set of Telegram commands and
dispatches them to existing read-only CLI entry points. It never
places orders, never enables execution, and hard-rejects any message
that looks like a trade instruction with a Chinese safety reply.

Design rules
------------
* Authorization: only incoming messages from a chat whose id matches
  ``telegram.command_interface.allowed_chat_ids`` (or the
  ``TELEGRAM_CHAT_ID`` env expansion) are dispatched.  Everything
  else is ignored / logged as ``unauthorized``.
* Safety: any message matching the unsafe pattern set (``buy``,
  ``sell``, ``trade``, ``order``, ``execute``, ``short``, ``options``,
  ``close position``, ``enable trading``, ``live``, ``place order``) is
  rejected with the Chinese safety reply.
* V0 transport: long-polling via ``httpx.get`` against Telegram's
  ``getUpdates`` endpoint. No webhook. ``polling_interval_seconds``
  controls how quickly we re-poll on empty responses.
* Logging: every incoming message is appended to
  ``data/telegram_commands/YYYY-MM-DD.jsonl`` with a redacted chat id.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx

from .config import AppConfig, load_config
from .journal import Journal
from .notifications import send_telegram_message

logger = logging.getLogger(__name__)


TELEGRAM_GET_UPDATES = "https://api.telegram.org/bot{token}/getUpdates"


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------
# Case-insensitive substring matching against incoming text. We keep
# the list intentionally conservative - Telegram commands are literal
# strings so ``buy AAPL``, ``sell 10 MSFT``, ``/order TSLA`` all hit
# this fence.
_UNSAFE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bbuy\b", re.I),
    re.compile(r"\bsell\b", re.I),
    re.compile(r"\btrade\b", re.I),
    re.compile(r"\border\b", re.I),
    re.compile(r"\bexecute\b", re.I),
    re.compile(r"\benable\s+trading\b", re.I),
    re.compile(r"\blive\b", re.I),
    re.compile(r"\bplace\s+order\b", re.I),
    re.compile(r"\bclose\s+position\b", re.I),
    re.compile(r"\bshort\b", re.I),
    re.compile(r"\boptions?\b", re.I),
    re.compile(r"\blong\b", re.I),
    re.compile(r"\bmarket\s+order\b", re.I),
    re.compile(r"\bcrypto\b", re.I),
    re.compile(r"\bforex\b", re.I),
    re.compile(r"\bfutures?\b", re.I),
    re.compile(r"\bnaked\s+", re.I),
)

SAFETY_MESSAGE_ZH = (
    "该 Telegram bot 当前只允许研究报告和人工复核，"
    "不允许下单、平仓、自动交易或任何 live execution。"
    "execution_allowed=false。"
)


def is_unsafe_command(text: str) -> bool:
    """Return True if ``text`` matches any execution-style pattern."""
    if not text:
        return False
    for pat in _UNSAFE_PATTERNS:
        if pat.search(text):
            return True
    return False


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
@dataclass
class CommandInterfaceConfig:
    enabled: bool
    allowed_chat_ids: list[str]
    language: str
    polling_interval_seconds: int
    reports_only: bool
    execution_allowed: bool
    max_message_length: int
    log_dir: str

    @property
    def is_usable(self) -> bool:
        return self.enabled and bool(self.allowed_chat_ids)


def _expand_env(value: str) -> str:
    if not isinstance(value, str):
        return ""
    s = value.strip()
    m = re.match(r"^\$\{([A-Z0-9_]+)\}$", s)
    if m:
        return os.getenv(m.group(1)) or ""
    return s


def load_command_config(cfg: AppConfig) -> CommandInterfaceConfig:
    """Parse ``telegram.command_interface`` into a typed record."""
    raw = (cfg.telegram_cfg or {}).get("command_interface") or {}
    expanded: list[str] = []
    for chat in raw.get("allowed_chat_ids") or []:
        resolved = _expand_env(str(chat))
        if resolved:
            expanded.append(resolved)
    return CommandInterfaceConfig(
        enabled=bool(raw.get("enabled", False)),
        allowed_chat_ids=expanded,
        language=str(raw.get("language") or "zh").lower(),
        polling_interval_seconds=int(raw.get("polling_interval_seconds") or 5),
        reports_only=bool(raw.get("reports_only", True)),
        execution_allowed=False,  # hard-forced regardless of file
        max_message_length=int(raw.get("max_message_length") or 3500),
        log_dir=str(raw.get("log_dir") or "data/telegram_commands"),
    )


# ---------------------------------------------------------------------------
# Command logging
# ---------------------------------------------------------------------------
def _redact_chat_id(chat_id: str | int) -> str:
    s = str(chat_id)
    if not s:
        return ""
    if len(s) <= 4:
        return "****"
    return s[:2] + "***" + s[-2:]


def _log_path(cfg: AppConfig, ci: CommandInterfaceConfig, date: str | None = None) -> Path:
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = cfg.absolute(f"{ci.log_dir}/{day}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def log_command(
    cfg: AppConfig,
    ci: CommandInterfaceConfig,
    *,
    chat_id: str | int,
    command: str,
    status: str,
    details: str = "",
) -> Path:
    """Append a command log entry. Never raises."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chat_id_redacted": _redact_chat_id(chat_id),
        "command": command,
        "status": status,
        "execution_allowed": False,
        "details": details[:1000],
    }
    path = _log_path(cfg, ci)
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram-commands log append failed: %s", exc)
    return path


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------
CommandFn = Callable[[list[str]], int]


def _default_command_runner(argv: list[str]) -> tuple[int, str]:
    """Invoke the Typer app in-process.  Returns (exit_code, stdout)."""
    from typer.testing import CliRunner

    from .cli import app

    runner = CliRunner()
    result = runner.invoke(app, argv, catch_exceptions=False)
    if result.exit_code != 0:
        logger.warning(
            "telegram command step exited with code %s: argv=%s",
            result.exit_code, argv,
        )
    return result.exit_code, result.stdout or ""


@dataclass
class CommandResult:
    command: str
    status: str  # success | failed | rejected | unauthorized
    reply_zh: str
    details: str = ""
    parts_sent: int = 0


# Supported Telegram commands. Each entry maps the canonical name to:
#   * a list of CLI argv sequences to run (in order)
#   * a Chinese reply summary
HELP_REPLY_ZH = (
    "支持的命令：\n"
    "/help       - 显示命令列表\n"
    "/news       - 立即生成盘前/即时重大新闻报告（中文）\n"
    "/regime     - 返回市场机制判断\n"
    "/watchlist  - 动态 watchlist + TWS CSV/TXT 导出\n"
    "/smc        - SMC 研究扫描（仅研究）\n"
    "/review     - SMC 人工复核队列\n"
    "/opening    - 完整 opening review 流程"
    "（regime → watchlist → TWS 导出 → smc → review）\n"
    "/status    - 返回 TWS / 最近报告 / scheduler 状态\n"
    "/auto_mtf_status - MTF 纸面自动循环：KILL、runtime 开关、最近 FULL/订单\n"
    "/auto_mtf_on    - 写 runtime 允许纸面提交（不启用 live）\n"
    "/auto_mtf_off   - 写 runtime 禁止纸面提交（可覆盖配置里的 fully_automatic）\n"
    "/kill          - 创建 KILL_SWITCH，阻止新纸面单\n"
    "/resume        - 移除 KILL_SWITCH（纸面研究用）\n"
    "/paper_orders  - 今日 orders.jsonl 尾部摘要\n"
    "/loop_status   - LaunchAgent/循环状态线索\n"
    "/heartbeat     - 最近循环心跳/周期时间\n"
    "\n安全规则：所有命令仅用于研究和纸面门控，execution_allowed=false；"
    "禁止 live/期权/短空等。"
)


def _status_reply_zh(cfg: AppConfig) -> str:
    """Return the Chinese /status reply by inspecting on-disk state."""
    root = cfg.project_root
    lines: list[str] = ["状态报告："]
    # Last pre-open JSON
    pre_dir = root / "data" / "pre_open_news"
    latest_pre = _latest_file(pre_dir, "*.json")
    lines.append(
        "- 最近 pre-open 报告：" + (latest_pre.name if latest_pre else "无")
    )
    # Last review queue
    rq_dir = root / "data" / "review_queue"
    latest_rq = _latest_file(rq_dir, "*.json")
    lines.append(
        "- 最近 SMC review queue：" + (latest_rq.name if latest_rq else "无")
    )
    # Scheduler log
    sch_dir = root / "data" / "scheduler"
    latest_sch = _latest_file(sch_dir, "*.jsonl")
    lines.append(
        "- 最近 scheduler 日志：" + (latest_sch.name if latest_sch else "无")
    )
    # Static safety flags
    lines.append("- execution_allowed=false")
    lines.append("- research_only=true")
    return "\n".join(lines)


def _latest_file(path: Path, pattern: str) -> Path | None:
    try:
        candidates = sorted(path.glob(pattern))
    except Exception:  # noqa: BLE001
        return None
    return candidates[-1] if candidates else None


# LaunchAgent label (10H); matches launchd/com.leon.ibkr-trading-bot.auto-paper.plist
_LAUNCHD_LABEL = "com.leon.ibkr-trading-bot.auto-paper"


def _set_runtime_mtf_paper_file(cfg: AppConfig, on: bool) -> None:
    p = cfg.absolute("data/runtime/mtf_auto_paper_enabled")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("1\n" if on else "0\n", encoding="utf-8")


def _auto_mtf_status_reply_zh(cfg: AppConfig) -> str:
    """Aggregate loop state, kill switch, and paper-only flags for Telegram."""
    lines: list[str] = [
        "【MTF 纸面自动】",
        "- execution_allowed=false；仅 PAPER/配置门控。",
    ]
    ks = cfg.absolute("data/KILL_SWITCH")
    lines.append(f"- KILL_SWITCH: {'是' if ks.is_file() else '否'}")
    rtf = cfg.absolute("data/runtime/mtf_auto_paper_enabled")
    if rtf.is_file():
        try:
            lines.append(f"- runtime mtf 文件: {rtf.read_text().strip()!r}")
        except OSError:
            lines.append("- runtime mtf 文件: 读取失败")
    else:
        lines.append("- runtime mtf 文件: 无")
    stp = cfg.absolute("data/runtime/auto_paper_loop_state.json")
    if stp.is_file():
        try:
            st = json.loads(stp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            st = {}
        lines.append(f"- 最近周期 UTC: {st.get('last_cycle_utc', '—')}")
        lines.append(f"- 上次 FULL_ALIGNMENT: {st.get('last_full_alignment_count', '—')}")
        lines.append(f"- 上次提交单数: {st.get('last_orders_submitted', '—')}")
        lines.append(
            f"- 上次 status/reason: {st.get('last_status', '—')}"
            f" / {st.get('last_reason', '')!s}"
        )
    else:
        lines.append("- auto_paper_loop_state.json: 无（循环尚未写状态）")
    t = cfg.settings.trading
    ap = t.mtf_auto_paper
    lines.append(
        f"- trading: enabled={t.enabled} mtf_paper_bracket={t.mtf_paper_bracket_enabled} "
        f"mtf_paper_dry_run={t.mtf_paper_dry_run} mtf_auto_paper: enabled={ap.enabled} "
        f"fully_automatic={ap.fully_automatic} allow_live_trading={ap.allow_live_trading}"
    )
    a = cfg.settings.account
    lines.append(
        f"- account: mode={a.mode!r} block_live_trading={a.block_live_trading}"
    )
    return "\n".join(lines)


def _paper_orders_reply_zh(cfg: AppConfig) -> str:
    p = cfg.absolute(cfg.settings.paths.orders_jsonl)
    if not p.is_file():
        return f"尚无 {p.name}。execution_allowed=false。"
    try:
        all_lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as exc:  # noqa: BLE001
        return f"读取 orders 失败: {exc}"
    if not all_lines:
        return "orders.jsonl 为空。"
    tail = all_lines[-60:]
    body = "\n".join(tail)
    if len(body) > 3600:
        body = "…(截断)\n" + body[-3600:]
    return f"orders.jsonl 尾部 (最多 60 行，execution_allowed=false)：\n{body}"


def _loop_status_reply_zh(cfg: AppConfig) -> str:
    agent = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
    parts = [
        "【循环 / LaunchAgent】",
        f"- 预期 LaunchAgents plist: {agent}",
    ]
    if not agent.is_file():
        parts.append("- plist: 未安装 (可运行 scripts/install_launch_agent.sh)")
    else:
        try:
            txt = agent.read_text(encoding="utf-8")
        except OSError as exc:  # noqa: BLE001
            parts.append(f"- plist: 读失败 {exc}")
        else:
            parts.append(
                "- plist: 无 Telegram token/账户号"
                if "TELEGRAM" not in txt and "token" not in txt.lower()
                else "- plist: 已检查(仍请人工确认无密钥)"
            )
    try:
        r = subprocess.run(
            ["/bin/launchctl", "list", _LAUNCHD_LABEL],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        out = (r.stdout or r.stderr or "").strip()
        parts.append(
            f"- launchctl list: exit={r.returncode} {out[:500]!s}"
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        parts.append(f"- launchctl: 不可用 ({exc!s})")
    stp = cfg.absolute("data/runtime/auto_paper_loop_state.json")
    if stp.is_file():
        try:
            st = json.loads(stp.read_text(encoding="utf-8"))
            parts.append(f"- 最近周期: {st.get('last_cycle_utc', '—')}")
        except (OSError, json.JSONDecodeError):
            parts.append("- 状态文件解析失败")
    return "\n".join(parts)


def _heartbeat_reply_zh(cfg: AppConfig) -> str:
    stp = cfg.absolute("data/runtime/auto_paper_loop_state.json")
    if not stp.is_file():
        return "无 auto_paper_loop_state.json 心跳数据。"
    try:
        st = json.loads(stp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "状态文件损坏。"
    return "\n".join(
        [
            "【auto-paper 心跳】",
            f"- last_cycle_utc: {st.get('last_cycle_utc', '—')}",
            f"- last_heartbeat_ts: {st.get('last_heartbeat_ts', '—')}",
            f"- last_full_alignment_count: {st.get('last_full_alignment_count', '—')}",
        ]
    )


def _regime_reply_zh(cfg: AppConfig, stdout: str) -> str:
    """Parse the freshest regime snapshot and format a Chinese summary."""
    from .cli import _resolve_regime_context  # local import to avoid cycles

    try:
        ctx = _resolve_regime_context(cfg, None)
    except Exception:
        ctx = None
    if not ctx:
        return "已执行 market-regime，但未解析到最新快照，请查看 data/market_regime/。"
    return (
        "市场机制：\n"
        f"- 市场状态：{ctx.get('market_regime', 'unknown')}\n"
        f"- 置信度：{ctx.get('regime_confidence', '-')}\n"
        f"- 研究扫描允许：{'是' if ctx.get('research_scans_allowed') else '否'}\n"
        f"- 新开仓允许：{'是' if ctx.get('new_positions_allowed') else '否'}\n"
        f"- 数据来源：{ctx.get('source_file') or '-'}\n"
        "- execution_allowed=false"
    )


def _news_reply_zh(cfg: AppConfig) -> str:
    """Build the Chinese full news report by reading the freshest JSON."""
    latest = _latest_file(cfg.project_root / "data" / "pre_open_news", "*.json")
    if not latest:
        return "未找到 pre-open 报告 JSON，请先运行 /news 或 pre-open-news。"
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"读取最新 pre-open 报告失败：{exc!r}"
    full = str(payload.get("full_chinese_report") or "").strip()
    if not full:
        return "最新 pre-open 报告缺少中文正文，请升级报告生成器后重试。"
    return full


def _generic_reply_zh(command: str, exit_code: int, artifact: str = "") -> str:
    if exit_code == 0:
        return (
            f"{command} 执行成功。{artifact}".strip()
            + "\n- execution_allowed=false"
        )
    return (
        f"{command} 执行失败，exit_code={exit_code}。"
        f"\n- execution_allowed=false"
    )


@dataclass
class Dispatcher:
    """Dispatches allowed Telegram commands to the CLI."""

    cfg: AppConfig
    journal: Journal
    ci: CommandInterfaceConfig
    runner: Callable[[list[str]], tuple[int, str]] = _default_command_runner

    def run(self, command: str) -> CommandResult:
        """Run a single Telegram command. Returns a :class:`CommandResult`."""
        raw = (command or "").strip()
        if not raw:
            return CommandResult(
                command=raw,
                status="rejected",
                reply_zh="空指令；请输入 /help 查看支持列表。",
                details="empty",
            )

        # 1) Safety gate - any trade-like word rejects immediately.
        if is_unsafe_command(raw):
            return CommandResult(
                command=raw,
                status="rejected",
                reply_zh=SAFETY_MESSAGE_ZH,
                details="unsafe pattern matched",
            )

        lower = raw.lower()
        head = lower.split()[0]

        if head in {"/help", "help"}:
            return CommandResult(
                command=raw,
                status="success",
                reply_zh=HELP_REPLY_ZH,
            )

        if head in {"/status", "status"}:
            return CommandResult(
                command=raw,
                status="success",
                reply_zh=_status_reply_zh(self.cfg),
            )

        if head in {"/news", "news"}:
            rc, _out = self.runner(["pre-open-news"])
            if rc != 0:
                return CommandResult(
                    command=raw,
                    status="failed",
                    reply_zh=_generic_reply_zh("/news", rc),
                    details=f"exit_code={rc}",
                )
            return CommandResult(
                command=raw,
                status="success",
                reply_zh=_news_reply_zh(self.cfg),
            )

        if head in {"/regime", "regime"}:
            rc, out = self.runner(["market-regime", "--ibkr"])
            if rc != 0:
                return CommandResult(
                    command=raw,
                    status="failed",
                    reply_zh=_generic_reply_zh("/regime", rc),
                    details=f"exit_code={rc}",
                )
            return CommandResult(
                command=raw,
                status="success",
                reply_zh=_regime_reply_zh(self.cfg, out),
            )

        if head in {"/watchlist", "watchlist"}:
            rc, _out = self.runner(
                ["build-watchlist", "--ibkr", "--limit", "50"]
            )
            # build-watchlist already auto-exports the TWS CSV/TXT; we
            # also kick the explicit export command to push a Telegram
            # note with the file paths (no orders, no broker state).
            if rc == 0:
                self.runner([
                    "export-tws-watchlist", "--latest", "--telegram",
                ])
            artifact = (
                "动态 watchlist 已更新，JSON 见 data/watchlists/。"
                "TWS CSV/TXT 已导出至 data/watchlists/latest-tws-*；"
                "请在 TWS Watchlist 导入 CSV 或复制 TXT。"
            )
            return CommandResult(
                command=raw,
                status="success" if rc == 0 else "failed",
                reply_zh=_generic_reply_zh("/watchlist", rc, artifact),
                details=f"exit_code={rc}",
            )

        if head in {"/smc", "smc"}:
            rc, _out = self.runner([
                "scan-smc-watchlist",
                "--source", "dynamic",
                "--timeframe", "daily",
                "--ibkr",
                "--chart",
                "--limit", "20",
                "--telegram",
            ])
            artifact = (
                "SMC 研究扫描已完成（仅研究）。Telegram 单独发送详细摘要。"
            )
            return CommandResult(
                command=raw,
                status="success" if rc == 0 else "failed",
                reply_zh=_generic_reply_zh("/smc", rc, artifact),
                details=f"exit_code={rc}",
            )

        if head in {"/review", "review"}:
            rc, _out = self.runner([
                "smc-review-queue",
                "--telegram",
                "--markdown",
                "--top", "10",
                "--include-charts",
            ])
            artifact = (
                "SMC 人工复核队列已生成，JSON 见 data/review_queue/。"
            )
            return CommandResult(
                command=raw,
                status="success" if rc == 0 else "failed",
                reply_zh=_generic_reply_zh("/review", rc, artifact),
                details=f"exit_code={rc}",
            )

        if head in {"/opening", "opening"}:
            sequence = [
                ["market-regime", "--ibkr"],
                ["build-watchlist", "--ibkr", "--limit", "50"],
                ["export-tws-watchlist", "--latest", "--telegram"],
                [
                    "scan-smc-watchlist", "--source", "dynamic",
                    "--timeframe", "daily", "--ibkr", "--chart",
                    "--limit", "20", "--telegram",
                ],
                [
                    "smc-review-queue", "--telegram", "--markdown",
                    "--top", "10", "--include-charts",
                ],
            ]
            overall = "success"
            exit_codes: list[int] = []
            for argv in sequence:
                rc, _out = self.runner(argv)
                exit_codes.append(rc)
                if rc != 0:
                    overall = "failed"
            return CommandResult(
                command=raw,
                status=overall,
                reply_zh=_generic_reply_zh(
                    "/opening",
                    0 if overall == "success" else 1,
                    "完整 opening review 流程已执行，分步骤 Telegram 已单独发送。",
                ),
                details=f"exit_codes={exit_codes}",
            )

        if head in {"/auto_mtf_status", "auto_mtf_status"}:
            return CommandResult(
                command=raw,
                status="success",
                reply_zh=_auto_mtf_status_reply_zh(self.cfg),
                details="auto_mtf status",
            )

        if head in {"/auto_mtf_on", "auto_mtf_on"}:
            _set_runtime_mtf_paper_file(self.cfg, True)
            return CommandResult(
                command=raw,
                status="success",
                reply_zh="已写 runtime=1；纸面可提交门仍受 config+KILL+对账+FULL 等约束。",
                details="mtf runtime on",
            )

        if head in {"/auto_mtf_off", "auto_mtf_off"}:
            _set_runtime_mtf_paper_file(self.cfg, False)
            return CommandResult(
                command=raw,
                status="success",
                reply_zh="已写 runtime=0；与 fully_automatic 同时存在时以显式 0 为优先，停止纸面提交。",
                details="mtf runtime off",
            )

        if head in {"/kill", "kill"}:
            kp = self.cfg.absolute("data/KILL_SWITCH")
            kp.parent.mkdir(parents=True, exist_ok=True)
            kp.write_text(
                f"{datetime.now(timezone.utc).isoformat()} via telegram /kill\n",
                encoding="utf-8",
            )
            return CommandResult(
                command=raw,
                status="success",
                reply_zh="已创建 KILL_SWITCH；新纸面单将被阻止。",
                details="kill switch",
            )

        if head in {"/resume", "resume"}:
            kp = self.cfg.absolute("data/KILL_SWITCH")
            try:
                if kp.is_file():
                    kp.unlink()
            except OSError as exc:  # noqa: BLE001
                return CommandResult(
                    command=raw,
                    status="failed",
                    reply_zh=f"无法移除 KILL_SWITCH: {exc}",
                )
            return CommandResult(
                command=raw,
                status="success",
                reply_zh="已移除 KILL_SWITCH（若存在）。",
                details="kill resume",
            )

        if head in {"/paper_orders", "paper_orders"}:
            return CommandResult(
                command=raw,
                status="success",
                reply_zh=_paper_orders_reply_zh(self.cfg),
                details="paper orders tail",
            )

        if head in {"/loop_status", "loop_status"}:
            return CommandResult(
                command=raw,
                status="success",
                reply_zh=_loop_status_reply_zh(self.cfg),
                details="launchd",
            )

        if head in {"/heartbeat", "heartbeat"}:
            return CommandResult(
                command=raw,
                status="success",
                reply_zh=_heartbeat_reply_zh(self.cfg),
                details="heartbeat",
            )

        return CommandResult(
            command=raw,
            status="rejected",
            reply_zh="未知指令。请输入 /help 查看支持列表。",
            details="unknown command",
        )


# ---------------------------------------------------------------------------
# Reply sending (splits long replies safely)
# ---------------------------------------------------------------------------
def _split_for_telegram(text: str, limit: int) -> list[str]:
    """Split on line boundaries, returns Part i / N prefixed chunks."""
    from .news_report_zh import split_for_telegram as base_split

    return base_split(text, limit=limit)


def deliver_reply(
    cfg: AppConfig,
    ci: CommandInterfaceConfig,
    result: CommandResult,
    *,
    chat_id: str | None = None,
    journal: Journal | None = None,
) -> int:
    """Send ``result.reply_zh`` to Telegram, splitting if too long.

    Returns the number of parts actually acknowledged.
    """
    parts = _split_for_telegram(result.reply_zh, limit=ci.max_message_length)
    acked = 0
    for part in parts:
        ok = send_telegram_message(part, cfg=cfg, journal=journal)
        if ok:
            acked += 1
    result.parts_sent = acked
    return acked


# ---------------------------------------------------------------------------
# Authorization + processing a single update
# ---------------------------------------------------------------------------
def is_authorized(ci: CommandInterfaceConfig, chat_id: str | int) -> bool:
    if not ci.allowed_chat_ids:
        return False
    return str(chat_id) in set(ci.allowed_chat_ids)


def process_message(
    cfg: AppConfig,
    journal: Journal,
    ci: CommandInterfaceConfig,
    *,
    chat_id: str | int,
    text: str,
    dispatcher: Dispatcher | None = None,
) -> CommandResult:
    """Authorize, dispatch, log, and reply to a single incoming message."""
    dispatcher = dispatcher or Dispatcher(cfg=cfg, journal=journal, ci=ci)

    if not is_authorized(ci, chat_id):
        result = CommandResult(
            command=text or "",
            status="unauthorized",
            reply_zh="未授权的聊天 ID，命令已忽略。",
            details="unauthorized chat",
        )
        log_command(
            cfg, ci,
            chat_id=chat_id, command=text or "",
            status=result.status, details=result.details,
        )
        return result

    # Safety pass BEFORE running any CLI so we never dispatch trade-y
    # text to the CLI surface.
    if is_unsafe_command(text):
        result = CommandResult(
            command=text,
            status="rejected",
            reply_zh=SAFETY_MESSAGE_ZH,
            details="unsafe pattern matched",
        )
        log_command(
            cfg, ci,
            chat_id=chat_id, command=text,
            status=result.status, details=result.details,
        )
        deliver_reply(cfg, ci, result, chat_id=str(chat_id), journal=journal)
        return result

    result = dispatcher.run(text)
    deliver_reply(cfg, ci, result, chat_id=str(chat_id), journal=journal)
    log_command(
        cfg, ci,
        chat_id=chat_id, command=text,
        status=result.status,
        details=result.details,
    )
    return result


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------
@dataclass
class _PollState:
    offset: int = 0
    started_at: float = field(default_factory=time.time)


def _fetch_updates(
    cfg: AppConfig,
    ci: CommandInterfaceConfig,
    state: _PollState,
    *,
    http: Any = httpx,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    token = cfg.telegram.bot_token
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN missing; cannot poll Telegram updates"
        )
    url = TELEGRAM_GET_UPDATES.format(token=token)
    params: dict[str, Any] = {
        "timeout": max(1, int(ci.polling_interval_seconds)),
    }
    if state.offset:
        params["offset"] = state.offset
    try:
        resp = http.get(url, params=params, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram getUpdates failed: %s", exc)
        return []
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not body.get("ok"):
        logger.warning("telegram getUpdates rejected: %s", body)
        return []
    return list(body.get("result") or [])


def _extract_message(update: dict[str, Any]) -> tuple[int, str, str | int] | None:
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = str(msg.get("text") or "").strip()
    update_id = int(update.get("update_id") or 0)
    if chat_id is None or not text:
        return None
    return update_id, text, chat_id


def poll_once(
    cfg: AppConfig,
    journal: Journal,
    ci: CommandInterfaceConfig,
    state: _PollState,
    *,
    http: Any = httpx,
    dispatcher: Dispatcher | None = None,
) -> list[CommandResult]:
    """Fetch one batch of updates and process them."""
    updates = _fetch_updates(cfg, ci, state, http=http)
    results: list[CommandResult] = []
    dispatcher = dispatcher or Dispatcher(cfg=cfg, journal=journal, ci=ci)
    for update in updates:
        parsed = _extract_message(update)
        if not parsed:
            continue
        update_id, text, chat_id = parsed
        state.offset = max(state.offset, update_id + 1)
        results.append(
            process_message(
                cfg, journal, ci,
                chat_id=chat_id, text=text, dispatcher=dispatcher,
            )
        )
    return results


def run_polling(
    cfg: AppConfig | None = None,
    journal: Journal | None = None,
    *,
    max_iterations: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    http: Any = httpx,
) -> None:
    """Blocking polling loop for ``python -m bot.cli telegram-listen``.

    Stops immediately when ``command_interface.enabled`` is false or
    ``allowed_chat_ids`` resolves to an empty list. Pass
    ``max_iterations`` in tests.
    """
    cfg = cfg or load_config()
    journal = journal or Journal(cfg)
    ci = load_command_config(cfg)
    if not ci.is_usable:
        logger.warning(
            "telegram command interface disabled or no allowed_chat_ids; "
            "refusing to poll."
        )
        return

    state = _PollState()
    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci)
    it = 0
    while True:
        poll_once(cfg, journal, ci, state, http=http, dispatcher=dispatcher)
        it += 1
        if max_iterations is not None and it >= max_iterations:
            return
        sleep_fn(max(1, int(ci.polling_interval_seconds)))


__all__ = [
    "SAFETY_MESSAGE_ZH",
    "CommandInterfaceConfig",
    "CommandResult",
    "Dispatcher",
    "deliver_reply",
    "is_authorized",
    "is_unsafe_command",
    "load_command_config",
    "log_command",
    "poll_once",
    "process_message",
    "run_polling",
]
