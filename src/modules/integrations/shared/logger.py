# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Logging Configuration

Centralized logging setup for integrations module.

Created: 2025-11-04
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

from src.modules.shared.log_levels import resolve_log_level


def setup_logging(
    name: str = "integrations",
    log_level: str = "INFO",
    log_dir: Optional[str] = None,
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Set up logging for a component.

    Creates a logger with both console and file handlers.
    File logs are rotated (10MB max, 5 backups).

    Args:
        name: Logger name (e.g., "integrations", "connector.azure_ad")
        log_level: Logging level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            An unrecognised value falls back to INFO and logs a warning rather
            than raising.
        log_dir: Directory for log files (default: ./logs)
        log_file: Specific log file name (default: auto-generated with timestamp)

    Returns:
        Configured logger instance

    Example:
        logger = setup_logging(
            name="connector.azure_ad",
            log_level="INFO",
            log_dir="/var/log/assetatlas"
        )
        logger.info("Starting connector...")
    """
    # Create logger
    logger = logging.getLogger(name)

    # Avoid adding handlers multiple times
    if logger.hasHandlers():
        return logger

    logger.setLevel(resolve_log_level(log_level, warn=True))
    logger.propagate = False  # Don't propagate to root logger

    # Configure format
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(log_format, date_format)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_dir or log_file:
        # Determine log file path
        if log_file:
            log_path = Path(log_file)
        else:
            log_dir_path = Path(log_dir) if log_dir else Path("logs")
            log_dir_path.mkdir(parents=True, exist_ok=True)

            # Generate filename with date
            timestamp = datetime.now().strftime("%Y%m%d")
            log_path = log_dir_path / f"{name.replace('.', '_')}_{timestamp}.log"

        # Create rotating file handler (10MB max, 5 backups)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        logger.debug(f"Logging to file: {log_path}")

    # Reduce noise from noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("connectionpool").setLevel(logging.WARNING)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get an existing logger or create a basic one.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)

    # If logger has no handlers, set up a basic console handler
    if not logger.hasHandlers():
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger


def configure_root_logger(log_level: str = "INFO"):
    """
    Configure the root logger for the entire application.

    Args:
        log_level: Logging level name; an unrecognised value falls back to INFO
            and logs a warning rather than raising.
    """
    logging.basicConfig(
        level=resolve_log_level(log_level, warn=True),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Reduce noise
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
