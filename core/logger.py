"""
Logging configuration for the trading system.
"""
import logging
import sys
from datetime import date
from config import settings


def get_logger(name: str) -> logging.Logger:
    """Create a logger with console + optional file handler."""
    logger = logging.getLogger(f"trading.{name}")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Daily log file (optional — skip if filesystem is read-only)
    try:
        log_file = settings.LOG_DIR / f"{date.today()}.log"
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass  # file logging unavailable — console only

    return logger
