"""
Structured Logging Utility for SEBI-Compliant Algo Trading System.

Provides:
- ``setup_logging``: configures the root logger with a structured format.
- ``DBLogWriter``: persists log entries to the database via the ``Log`` model.
- ``get_logger``: convenience factory for named loggers.
"""

import sys
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import Log


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger with structured console and rotating file handlers."""
    import os
    from logging.handlers import RotatingFileHandler

    log_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)

    # 2. Rotating File Handler (50 MB per file, max 30 backups)
    os.makedirs("logs", exist_ok=True)
    file_handler = RotatingFileHandler(
        filename=os.path.join("logs", "trading_system.log"),
        maxBytes=50 * 1024 * 1024,
        backupCount=30,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Avoid duplicate handlers on repeated calls
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


class DBLogWriter:
    """Writes structured log entries to the database.

    All write operations are wrapped in try/except so that a logging
    failure never propagates and crashes the application.
    """

    def __init__(self, db: Session):
        """Initialise with an active SQLAlchemy session.

        Args:
            db: SQLAlchemy ``Session`` used to persist log records.
        """
        self.db = db

    def write(
        self,
        level: str,
        source: str,
        message: str,
        context: dict | None = None,
        user_id: UUID | None = None,
    ) -> None:
        """Persist a log entry to the ``logs`` table.

        Args:
            level: Severity level (``INFO``, ``WARNING``, ``ERROR``, etc.).
            source: Originating module or component name.
            message: Human-readable log message.
            context: Optional JSON-serialisable dict with extra data.
            user_id: Optional UUID of the associated user.
        """
        try:
            log_entry = Log(
                level=level.upper(),
                source=source,
                message=message,
                context=context,
                user_id=user_id,
            )
            self.db.add(log_entry)
            self.db.commit()
        except Exception as exc:
            # Roll back so the session stays usable for subsequent queries
            self.db.rollback()
            logging.getLogger(__name__).error(
                "Failed to write log to DB: %s", exc
            )

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def info(self, source: str, message: str, **kwargs) -> None:
        """Write an INFO-level log entry.

        Args:
            source: Originating module or component name.
            message: Log message.
            **kwargs: Forwarded to :meth:`write` (``context``, ``user_id``).
        """
        self.write("INFO", source, message, **kwargs)

    def warning(self, source: str, message: str, **kwargs) -> None:
        """Write a WARNING-level log entry.

        Args:
            source: Originating module or component name.
            message: Log message.
            **kwargs: Forwarded to :meth:`write` (``context``, ``user_id``).
        """
        self.write("WARNING", source, message, **kwargs)

    def error(self, source: str, message: str, **kwargs) -> None:
        """Write an ERROR-level log entry.

        Args:
            source: Originating module or component name.
            message: Log message.
            **kwargs: Forwarded to :meth:`write` (``context``, ``user_id``).
        """
        self.write("ERROR", source, message, **kwargs)

    def critical(self, source: str, message: str, **kwargs) -> None:
        """Write a CRITICAL-level log entry.

        Args:
            source: Originating module or component name.
            message: Log message.
            **kwargs: Forwarded to :meth:`write` (``context``, ``user_id``).
        """
        self.write("CRITICAL", source, message, **kwargs)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger instance.

    Convenience wrapper around ``logging.getLogger`` to keep imports
    centralised within the project.

    Args:
        name: Logger name (typically ``__name__``).

    Returns:
        A ``logging.Logger`` instance.
    """
    return logging.getLogger(name)
