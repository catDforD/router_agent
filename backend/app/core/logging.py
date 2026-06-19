"""Logging setup for the Router backend."""

from __future__ import annotations

import json
import logging
import logging.config
import re
from typing import Any

from app.core.config import Settings


_CONFIGURED = False
MAX_LOG_VALUE_CHARS = 500
SENSITIVE_KEY_TOKENS = (
    "api_key",
    "apikey",
    "authorization",
    "database_url",
    "password",
    "secret",
    "token",
)
OMITTED_CONTENT_KEYS = {
    "body",
    "content",
    "full_code",
    "full_report",
    "mcp_payload",
    "plc_code",
    "raw_model_output",
    "raw_response",
    "report_body",
    "request_body",
    "response_body",
    "replay_log",
}
OMITTED_CONTENT_KEY_SUFFIXES = (
    "artifact_content",
)


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


def safe_log_context(**context: Any) -> dict[str, Any]:
    """Return bounded, non-secret context suitable for process logs."""

    return {
        str(key): _sanitize_log_value(str(key), value)
        for key, value in context.items()
        if value is not None
    }


def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    **context: Any,
) -> None:
    """Emit a log line with stable, redacted context fields."""

    safe_context = safe_log_context(**context)
    if safe_context:
        logger.log(level, "%s | context=%s", message, _context_json(safe_context))
    else:
        logger.log(level, "%s", message)


def _context_json(context: dict[str, Any]) -> str:
    return json.dumps(
        context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sanitize_log_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if _is_sensitive_key(lowered):
        return "[redacted]"
    if _is_content_key(lowered):
        return "[omitted]"
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize_log_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_log_value(key, item)
            for item in value[:20]
        ]
    text = _redact_url_credentials(str(value))
    if len(text) > MAX_LOG_VALUE_CHARS:
        return f"{text[: MAX_LOG_VALUE_CHARS - 15].rstrip()}... [truncated]"
    return text


def _is_sensitive_key(lowered_key: str) -> bool:
    return any(token in lowered_key for token in SENSITIVE_KEY_TOKENS)


def _is_content_key(lowered_key: str) -> bool:
    return lowered_key in OMITTED_CONTENT_KEYS or any(
        lowered_key.endswith(suffix) for suffix in OMITTED_CONTENT_KEY_SUFFIXES
    )


def _redact_url_credentials(value: str) -> str:
    return re.sub(r"://([^/@:]+):([^/@]+)@", "://[redacted]:[redacted]@", value)
