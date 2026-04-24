"""Notification adapters (Telegram, etc.)."""

from .telegram import notify_event, send_telegram_message

__all__ = ["send_telegram_message", "notify_event"]
