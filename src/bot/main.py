"""Entry point: python -m bot"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from bot.config import ConfigError, Settings
from bot.logging_setup import setup_logging


def main() -> None:
    load_dotenv()
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(2)
    setup_logging(settings.log_level)

    from bot.app import build_application

    app = build_application(settings)
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
