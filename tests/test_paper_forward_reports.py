"""Reports: daily paper report stays file-based."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_paper_daily_module_docstring_says_no_ibkr() -> None:
    src = (REPO / "bot" / "reports" / "paper_daily.py").read_text(encoding="utf-8")
    assert "No IBKR connection" in src
    assert "from ..ibkr" not in src
