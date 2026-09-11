"""Logging configuration with a confidentiality guard.

Policy: log events, counts and identifiers - never deck contents. Code must not pass
slide text to a logger. As a backstop, ``ConfidentialityFilter`` truncates any message
that is long enough to plausibly contain pasted deck text.
"""

from __future__ import annotations

import logging

MAX_LOG_MESSAGE_CHARS = 300
_CONFIGURED = False


class ConfidentialityFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if len(message) > MAX_LOG_MESSAGE_CHARS:
            record.msg = message[:MAX_LOG_MESSAGE_CHARS] + " [truncated: confidentiality guard]"
            record.args = None
        return True


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(ConfidentialityFilter())
    root = logging.getLogger("app")
    root.addHandler(handler)
    root.setLevel(level.upper() if isinstance(level, str) else level)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name if name.startswith("app") else f"app.{name}")
    if not any(isinstance(f, ConfidentialityFilter) for f in logger.filters):
        logger.addFilter(ConfidentialityFilter())
    return logger
