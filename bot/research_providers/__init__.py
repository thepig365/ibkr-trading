"""Research providers for the v2 Intelligence Layer.

Each provider exposes a small, narrow API. Providers MUST:

* Never call ``broker.place_order`` or any order-submission path.
* Connect to IBKR only when an explicit CLI / worker command runs them.
  Importing this package must NOT establish any external connection.
* Degrade gracefully when entitlements / credentials are missing
  (return empty lists + a structured ``provider_status`` dict; never
  raise unhandled exceptions on that path).

Future providers (Benzinga / FMP / Finnhub) belong here too, but Prompt
13B intentionally ships only IBKR + manual macro calendar.
"""

from .ibkr_news_provider import (
    IBKRNewsProviderStatus,
    fetch_ibkr_news,
    get_provider_status,
)
from .manual_macro_calendar import (
    DEFAULT_MACRO_CATEGORIES,
    MacroCalendar,
    load_macro_calendar,
)

__all__ = [
    "DEFAULT_MACRO_CATEGORIES",
    "IBKRNewsProviderStatus",
    "MacroCalendar",
    "fetch_ibkr_news",
    "get_provider_status",
    "load_macro_calendar",
]
