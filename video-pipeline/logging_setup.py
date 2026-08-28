"""
Structured logging setup using structlog.

All pipeline events go through one logger so audit_log.py can write a parallel
JSONL stream for compliance / post-hoc analysis.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import structlog


def setup_logging(
    log_dir: Optional[Path] = None,
    level: str = "INFO",
    json_logs: bool = True,
) -> None:
    """Configure structlog for console + optional file output."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Standard logging -> stdout
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_logs:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Optional file logging
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "pipeline.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(file_handler)


def get_logger(name: str = "pipeline") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
