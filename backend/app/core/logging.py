"""Logging setup for the Router backend."""

from __future__ import annotations

import logging
import logging.config

from app.core.config import Settings


_CONFIGURED = False


def configure_logging(settings: Settings) -> None:
    """Configure process logging and emit non-secret startup context."""

    global _CONFIGURED

    if not _CONFIGURED:
        root_logger = logging.getLogger()
        existing_handlers = list(root_logger.handlers)
        logging.config.dictConfig(
            {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "default": {
                        "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                    },
                },
                "handlers": {
                    "console": {
                        "class": "logging.StreamHandler",
                        "formatter": "default",
                    },
                },
                "root": {
                    "level": settings.log_level,
                    "handlers": ["console"],
                },
            }
        )
        for handler in existing_handlers:
            if handler not in root_logger.handlers:
                root_logger.addHandler(handler)
        _CONFIGURED = True

    logging.getLogger("app").info(
        "Starting %s in %s environment",
        settings.app_name,
        settings.app_env,
    )
