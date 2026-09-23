"""Structured logging with structlog. See AGENTS.md > Logging.

The first argument is a static event name; everything else is a keyword
argument with a primitive value.

    logger.info("transfer initiated", transfer_id=str(t.id), amount_minor=t.amount_minor)
"""

import logging

import structlog

from app.config import settings


def configure_logging() -> None:
    logging.basicConfig(format="%(message)s", level=settings.log_level.upper())

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Human-readable locally, machine-readable everywhere else. Same call sites.
    if settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
