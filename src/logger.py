"""Structured logging configuration."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog

from src.config import settings


class _ScraperOnlyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith("src.scrapers")


def configure_logging() -> None:
    """Configure structlog with JSON or console renderer."""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Avoid duplicate handlers in long-lived processes/tests.
    root_logger.handlers.clear()
    root_logger.addHandler(stdout_handler)

    # Persist all app logs to disk.
    app_log_path = Path(settings.app_log_path)
    app_log_path.parent.mkdir(parents=True, exist_ok=True)
    app_file_handler = logging.FileHandler(app_log_path, encoding="utf-8")
    app_file_handler.setFormatter(formatter)
    root_logger.addHandler(app_file_handler)

    # Persist scraper-only logs separately for troubleshooting.
    scraper_log_path = Path(settings.scraper_log_path)
    scraper_log_path.parent.mkdir(parents=True, exist_ok=True)
    scraper_file_handler = logging.FileHandler(scraper_log_path, encoding="utf-8")
    scraper_file_handler.setFormatter(formatter)
    scraper_file_handler.addFilter(_ScraperOnlyFilter())
    root_logger.addHandler(scraper_file_handler)

    root_logger.setLevel(log_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structured logger."""
    return structlog.get_logger(name)
