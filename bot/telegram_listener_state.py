"""Persistent state for Telegram getUpdates (offset + unknown-command dedup)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_RELPATH = "data/runtime/telegram_command_listener_state.json"


@dataclass
class TelegramListenerFileState:
    update_offset: int = 0
    unknown_last_text: str = ""
    unknown_last_ts: float = field(default_factory=lambda: 0.0)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TelegramListenerFileState:
        return cls(
            update_offset=int(raw.get("update_offset") or 0),
            unknown_last_text=str(raw.get("unknown_last_text") or ""),
            unknown_last_ts=float(raw.get("unknown_last_ts") or 0.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "update_offset": self.update_offset,
            "unknown_last_text": self.unknown_last_text,
            "unknown_last_ts": self.unknown_last_ts,
            "saved_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            ),
        }


def state_path_for(cfg_root: Path) -> Path:
    return Path(cfg_root).resolve() / DEFAULT_RELPATH


def load_state(path: Path) -> TelegramListenerFileState:
    if not path.is_file():
        return TelegramListenerFileState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return TelegramListenerFileState()
    if not isinstance(raw, dict):
        return TelegramListenerFileState()
    return TelegramListenerFileState.from_dict(raw)


def save_state(path: Path, st: TelegramListenerFileState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(st.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "DEFAULT_RELPATH",
    "TelegramListenerFileState",
    "load_state",
    "save_state",
    "state_path_for",
]
