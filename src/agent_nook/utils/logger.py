"""XDG-compliant logging setup for Agent Nook.

All log files are written to ~/.local/state/agent-nook/logs/
by default, following the XDG Base Directory Specification.

Environment variables respected:
- XDG_STATE_HOME  -> ~/.local/state/agent-nook/logs/
- XDG_CONFIG_HOME -> ~/.config/agent-nook/logging.yaml

Usage:
    from agent_nook.utils import logger

    log = logger.setup_logger("my_module", level="DEBUG")
    log.debug("This is a debug message")
    log.info("This is an info message")
"""

from __future__ import annotations

import os
import sys
import logging
import logging.handlers
from pathlib import Path
from typing import Optional


def get_state_dir() -> str:
    """Get the XDG state directory for agent-nook.

    Reads XDG_STATE_HOME from environment, falls back to
    ~/.local/state/agent-nook.
    """
    xdg_state = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    return os.path.join(xdg_state, "agent-nook")


def get_log_directory() -> str:
    """Get the log directory path."""
    state_dir = get_state_dir()
    return os.path.join(state_dir, "logs")


def get_log_file_path() -> str:
    """Get the path to the main log file."""
    return os.path.join(get_log_directory(), "agent-nook.log")


def setup_logger(
    name: str = "agent_nook",
    level: str = "INFO",
    use_file: bool = True,
    use_console: bool = True,
) -> logging.Logger:
    """Configure and return a logger for Agent Nook.

    Args:
        name: The logger name.
        level: Log level as string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        use_file: Whether to enable file logging (requires write permissions).
        use_console: Whether to enable console logging.

    Returns:
        Configured logging.Logger instance.

    Log file location: ~/.local/state/agent-nook/logs/agent-nook.log

    The logger uses:
    - RotatingFileHandler: writes to disk, rotates at 10MB, keeps 10 backups.
    - StreamHandler: outputs INFO+ to stdout/stderr.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Avoid duplicate handlers if logger already has handlers
    if logger.handlers:
        return logger

    # Format
    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler (only if we can write to the state directory)
    if use_file:
        try:
            log_dir = get_log_directory()
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "agent-nook.log")

            handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=10,
                encoding="utf-8",
            )
            handler.setLevel(logging.DEBUG)  # File gets everything
            handler.setFormatter(formatter)
            handler.addFilter(
                lambda record: record.levelno >= logging.DEBUG
            )
            logger.addHandler(handler)
        except (PermissionError, OSError) as e:
            # If we can't write to XDG state, fallback to /tmp
            log_dir = os.path.join("/tmp", "agent-nook-logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "agent-nook.log")
            logger.warning(
                "Cannot write to XDG state dir (%s), falling back to /tmp: %s",
                get_log_directory(),
                e,
            )

            try:
                handler = logging.handlers.RotatingFileHandler(
                    log_file,
                    maxBytes=10 * 1024 * 1024,
                    backupCount=10,
                    encoding="utf-8",
                )
                handler.setLevel(logging.DEBUG)
                handler.setFormatter(formatter)
                handler.addFilter(
                    lambda record: record.levelno >= logging.DEBUG
                )
                logger.addHandler(handler)
            except (PermissionError, OSError) as e2:
                logger.warning(
                    "Cannot write logs to %s or /tmp: %s",
                    log_file,
                    e2,
                )

    # Console handler
    if use_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


# --- Convenience ---

def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get or create a logger with default configuration.

    Args:
        name: Optional logger name. Defaults to "agent_nook".

    Returns:
        A configured logging.Logger.
    """
    default_name = name or "agent_nook"
    logger = logging.getLogger(default_name)

    if not logger.handlers:
        setup_logger(name=default_name, level="INFO")

    return logger


def get_global_config() -> dict:
    """Get the global nook_config.

    Returns:
        The global configuration dictionary.
    """
    try:
        from agent_nook.config import nook_config
        return nook_config
    except (ImportError, RuntimeError):
        return {}


def main_logger(name: str = "agent_nook", level: str = "INFO") -> logging.Logger:
    """Set up logging and return the logger.

    This is the main entry point for setting up logging.
    It configures the logger with INFO level by default.

    Args:
        name: Logger name.
        level: Log level.

    Returns:
        Configured logger.
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler
    log_dir = get_log_directory()
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "agent-nook.log")

    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger
