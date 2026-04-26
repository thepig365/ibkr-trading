"""Paper trading report engine (Prompt 13M). File-based; no TWS/IBKR by default."""

from .paper_daily import build_daily_paper_report
from .paper_weekly import build_weekly_latest, build_weekly_paper_report
from .render_markdown import (
    format_paper_daily_telegram_zh,
    render_paper_daily_markdown,
    render_paper_weekly_markdown,
)
from .report_paths import (
    default_report_dir,
    infer_latest_report_date,
    utc_today_str,
)

__all__ = [
    "build_daily_paper_report",
    "build_weekly_latest",
    "build_weekly_paper_report",
    "default_report_dir",
    "format_paper_daily_telegram_zh",
    "infer_latest_report_date",
    "render_paper_daily_markdown",
    "render_paper_weekly_markdown",
    "utc_today_str",
]
