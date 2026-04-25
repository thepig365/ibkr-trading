"""Allow ``python -m bot_ui`` to launch the local UI server."""

from __future__ import annotations

import sys

from .server import main

if __name__ == "__main__":
    sys.exit(main())
