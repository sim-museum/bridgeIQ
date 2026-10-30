"""
Logging configuration for BridgeIQ application.
Provides centralized logging setup with file and console handlers.
"""

import logging
import os
import sys
from datetime import datetime
from typing import Optional


def setup_logging(
    log_level: int = logging.INFO,
    log_file: Optional[str] = None,
    console: bool = True
) -> logging.Logger:
    """
    Configure application logging.

    Args:
        log_level: Logging level (default: INFO)
        log_file: Path to log file (optional)
        console: Whether to log to console (default: True)

    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger('bridgeIQ')
    logger.setLevel(log_level)
    logger.propagate = False

    # Clear existing handlers
    logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    # File handler
    if log_file:
        try:
            # Ensure directory exists
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir)

            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            if console:
                logger.warning(f"Could not create log file: {e}")

    return logger


def get_logger(name: str = 'bridgeIQ') -> logging.Logger:
    """Get the application logger."""
    return logging.getLogger(name)


# Default log file location
def get_default_log_path() -> str:
    """Get the default log file path."""
    # Use XDG_DATA_HOME or fall back to ~/.local/share
    data_home = os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
    log_dir = os.path.join(data_home, 'bridgeIQ', 'logs')
    timestamp = datetime.now().strftime('%Y%m%d')
    return os.path.join(log_dir, f'bridgeIQ_{timestamp}.log')
