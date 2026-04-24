"""Tests for the Telegram command interface (Prompt 9.2, Part B)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from bot.config import load_config
from bot.journal import Journal
from bot.telegram_commands import (
    SAFETY_MESSAGE_ZH,
    CommandInterfaceConfig,
    Dispatcher,
    _PollState,
    deliver_reply,
    is_authorized,
    is_unsafe_command,
    load_command_config,
    log_command,
    poll_once,
    process_message,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------
def _silence_outbound(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture every httpx.post made by send_telegram_message."""
    sent: list[dict] = []

    def fake_post(url, json=None, timeout=None, **kw):  # noqa: A002
        sent.append({"url": url, "json": json})

        class R:
            status_code = 200
            text = "{}"

            def json(self_inner):
                return {"ok": True}

        return R()

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def _setup_auth(monkeypatch: pytest.MonkeyPatch, chat_id: str = "42") -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tkn:XYZ")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", chat_id)


class _FakeRunner:
    """Capture CLI invocations without running them."""

    def __init__(self, exit_code: int = 0, stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self.exit_code = exit_code
        self.stdout = stdout

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(list(argv))
        return self.exit_code, self.stdout


# ---------------------------------------------------------------------------
# Test 1: /help returns the supported command list
# ---------------------------------------------------------------------------
def test_help_lists_all_supported_commands(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)

    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci)
    result = dispatcher.run("/help")

    assert result.status == "success"
    for cmd in ("/help", "/news", "/regime", "/watchlist", "/smc", "/review",
                "/opening", "/status"):
        assert cmd in result.reply_zh
    assert "execution_allowed=false" in result.reply_zh


# ---------------------------------------------------------------------------
# Test 2: /news runs pre-open-news and returns the Chinese full report
# ---------------------------------------------------------------------------
def test_news_triggers_chinese_news_report(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)

    # Seed a fake pre_open_news JSON so /news can read the Chinese body.
    pre_dir = tmp_project / "data" / "pre_open_news"
    pre_dir.mkdir(parents=True, exist_ok=True)
    body_zh = "【盘前重大市场新闻报告】2026-04-24\n一、市场机制判断\n- 市场状态：neutral"
    (pre_dir / "2026-04-24.json").write_text(
        json.dumps({
            "date": "2026-04-24",
            "language": "zh",
            "telegram_report_language": "zh",
            "full_chinese_report": body_zh,
            "execution_allowed": False,
            "research_only": True,
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    runner = _FakeRunner(exit_code=0)
    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner)
    result = dispatcher.run("/news")

    assert runner.calls == [["pre-open-news"]]
    assert result.status == "success"
    assert "【盘前重大市场新闻报告】" in result.reply_zh
    assert "一、市场机制判断" in result.reply_zh


# ---------------------------------------------------------------------------
# Test 3: /regime returns a Chinese summary
# ---------------------------------------------------------------------------
def test_regime_returns_chinese_summary(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)

    # Seed a regime snapshot so _resolve_regime_context has something
    # concrete to read.
    mr_dir = tmp_project / "data" / "market_regime"
    mr_dir.mkdir(parents=True, exist_ok=True)
    (mr_dir / "2026-04-24.json").write_text(
        json.dumps({
            "market_regime": "neutral",
            "regime_confidence": "medium",
            "new_positions_allowed": False,
            "research_scans_allowed": True,
            "market_data": {"missing_fields": ["VIX", "VIX3M"]},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    runner = _FakeRunner(exit_code=0)
    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner)
    result = dispatcher.run("/regime")

    assert result.status == "success"
    assert runner.calls == [["market-regime", "--ibkr"]]
    assert "市场机制" in result.reply_zh
    assert "neutral" in result.reply_zh
    assert "execution_allowed=false" in result.reply_zh


# ---------------------------------------------------------------------------
# Test 4: /smc triggers the research scan only
# ---------------------------------------------------------------------------
def test_smc_triggers_research_scan_only(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    runner = _FakeRunner(exit_code=0)
    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner)

    result = dispatcher.run("/smc")

    assert result.status == "success"
    assert runner.calls == [[
        "scan-smc-watchlist", "--source", "dynamic",
        "--timeframe", "daily", "--ibkr", "--chart",
        "--limit", "20", "--telegram",
    ]]
    assert "仅研究" in result.reply_zh


# ---------------------------------------------------------------------------
# Test 5: /review triggers the SMC review queue
# ---------------------------------------------------------------------------
def test_review_triggers_review_queue_only(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    runner = _FakeRunner(exit_code=0)
    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner)

    result = dispatcher.run("/review")

    assert result.status == "success"
    assert runner.calls == [[
        "smc-review-queue", "--telegram", "--markdown",
        "--top", "10", "--include-charts",
    ]]
    assert "人工复核" in result.reply_zh


# ---------------------------------------------------------------------------
# Test 6: unsafe commands are rejected
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "buy AAPL",
        "sell 10 MSFT",
        "/order TSLA",
        "place order",
        "execute immediately",
        "enable trading",
        "close position",
        "short NVDA",
        "go live",
        "trade AMD",
        "options play",
    ],
)
def test_unsafe_command_is_rejected(
    tmp_project: Path, monkeypatch, text: str
) -> None:
    _setup_auth(monkeypatch)
    sent = _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)

    # Verify the pure predicate.
    assert is_unsafe_command(text) is True

    # And end-to-end the dispatcher must refuse before running anything.
    runner = _FakeRunner(exit_code=0)
    result = process_message(
        cfg, journal, ci,
        chat_id="42", text=text,
        dispatcher=Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner),
    )

    assert result.status == "rejected"
    assert result.reply_zh == SAFETY_MESSAGE_ZH
    assert runner.calls == []  # CLI never touched
    # A reply was delivered to Telegram so the user sees the rejection.
    assert sent, "safety rejection must still deliver the Chinese warning"


# ---------------------------------------------------------------------------
# Test 7: messages from an unauthorized chat are rejected
# ---------------------------------------------------------------------------
def test_unauthorized_chat_is_rejected(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch, chat_id="42")
    sent = _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)

    assert is_authorized(ci, "42") is True
    assert is_authorized(ci, "99") is False

    runner = _FakeRunner(exit_code=0)
    result = process_message(
        cfg, journal, ci,
        chat_id="99", text="/news",
        dispatcher=Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner),
    )

    assert result.status == "unauthorized"
    assert runner.calls == []  # CLI never dispatched
    # We intentionally do NOT reply to unknown chats.
    assert sent == []


# ---------------------------------------------------------------------------
# Test 8: command logs are written
# ---------------------------------------------------------------------------
def test_command_logs_are_written(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    runner = _FakeRunner(exit_code=0)
    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner)

    # Success path
    process_message(cfg, journal, ci, chat_id="42", text="/help",
                    dispatcher=dispatcher)
    # Rejected path
    process_message(cfg, journal, ci, chat_id="42", text="buy AAPL",
                    dispatcher=dispatcher)
    # Unauthorized path
    process_message(cfg, journal, ci, chat_id="99", text="/help",
                    dispatcher=dispatcher)

    log_dir = tmp_project / "data" / "telegram_commands"
    files = list(log_dir.glob("*.jsonl"))
    assert files, "command log file must exist"
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    statuses = [p["status"] for p in parsed]
    assert {"success", "rejected", "unauthorized"}.issubset(set(statuses))
    for p in parsed:
        assert p["execution_allowed"] is False
        # Redacted chat id - never contains the raw integer.
        assert "42" not in p["chat_id_redacted"] or len("42") <= 4
        assert "chat_id" not in p  # raw chat id must not appear


# ---------------------------------------------------------------------------
# Test 9: long replies are split into Part 1 / Part N
# ---------------------------------------------------------------------------
def test_long_reply_is_split_safely(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch)
    sent = _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = CommandInterfaceConfig(
        enabled=True,
        allowed_chat_ids=["42"],
        language="zh",
        polling_interval_seconds=5,
        reports_only=True,
        execution_allowed=False,
        max_message_length=500,
        log_dir="data/telegram_commands",
    )
    # Build a result whose reply is definitely longer than 500 chars.
    from bot.telegram_commands import CommandResult

    body = "\n".join(f"行 {i}" for i in range(300))
    result = CommandResult(command="/news", status="success", reply_zh=body)
    acked = deliver_reply(cfg, ci, result, journal=journal)
    assert acked == len(sent)
    assert acked > 1, "long reply must be split into multiple messages"
    # Every chunk is within the configured limit.
    for call in sent:
        assert len(call["json"]["text"]) <= ci.max_message_length
    # Part markers present on non-first parts.
    assert any(call["json"]["text"].startswith("(Part") for call in sent[1:])


# ---------------------------------------------------------------------------
# Test 10: execution_allowed stays false and broker.place_order is never called
# ---------------------------------------------------------------------------
def test_execution_allowed_stays_false_no_place_order(
    tmp_project: Path, monkeypatch
) -> None:
    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)

    from bot.broker import Broker

    sentinel = MagicMock(
        side_effect=AssertionError("place_order must not be called")
    )
    monkeypatch.setattr(Broker, "place_order", sentinel)

    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    assert ci.execution_allowed is False

    runner = _FakeRunner(exit_code=0)
    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner)

    for text in ("/help", "/news", "/review", "/regime", "/smc", "/watchlist",
                 "/opening", "/status", "buy AAPL"):
        process_message(
            cfg, journal, ci,
            chat_id="42", text=text, dispatcher=dispatcher,
        )

    sentinel.assert_not_called()
    # Every log entry records execution_allowed=false.
    log_dir = tmp_project / "data" / "telegram_commands"
    lines = next(log_dir.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()
    for line in lines:
        payload = json.loads(line)
        assert payload["execution_allowed"] is False


# ---------------------------------------------------------------------------
# Polling tests (bonus) - we never sleep against a real socket.
# ---------------------------------------------------------------------------
def test_poll_once_dispatches_one_update(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch)
    _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    runner = _FakeRunner(exit_code=0)
    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner)

    class FakeHttp:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, url, params=None, timeout=None):
            self.calls += 1
            payload = {"ok": True, "result": [
                {"update_id": 10, "message": {"chat": {"id": 42},
                                               "text": "/help"}},
            ]}

            class R:
                status_code = 200

                def json(self_inner):
                    return payload

            return R()

    http = FakeHttp()
    state = _PollState()

    results = poll_once(cfg, journal, ci, state, http=http, dispatcher=dispatcher)

    assert len(results) == 1
    assert results[0].status == "success"
    assert state.offset == 11  # update_id + 1
    assert http.calls == 1


def test_poll_once_ignores_unauthorized_chat(
    tmp_project: Path, monkeypatch
) -> None:
    _setup_auth(monkeypatch, chat_id="42")
    sent = _silence_outbound(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    journal = Journal(cfg)
    ci = load_command_config(cfg)
    runner = _FakeRunner(exit_code=0)
    dispatcher = Dispatcher(cfg=cfg, journal=journal, ci=ci, runner=runner)

    class FakeHttp:
        def get(self, url, params=None, timeout=None):
            payload = {"ok": True, "result": [
                {"update_id": 5, "message": {"chat": {"id": 99},
                                              "text": "/news"}},
            ]}

            class R:
                status_code = 200

                def json(self_inner):
                    return payload

            return R()

    results = poll_once(cfg, journal, ci, _PollState(), http=FakeHttp(),
                        dispatcher=dispatcher)
    assert len(results) == 1
    assert results[0].status == "unauthorized"
    assert runner.calls == []  # CLI never ran
    assert sent == []  # No outbound reply


def test_log_command_redacts_chat_id(tmp_project: Path, monkeypatch) -> None:
    _setup_auth(monkeypatch)
    cfg = load_config(project_root=tmp_project)
    ci = load_command_config(cfg)
    path = log_command(
        cfg, ci,
        chat_id="1234567890", command="/help",
        status="success", details="",
    )
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert line["chat_id_redacted"] == "12***90"
    assert line["execution_allowed"] is False
