"""XDG-compliant logging setup for Agent Nook.

All log files are written to ~/.local/state/agent-nook/logs/
by default, following the XDG Base Directory Specification.

Environment variables respected:
- XDG_STATE_HOME  -> ~/.local/state/agent-nook/logs/
- XDG_CONFIG_HOME -> ~/.config/agent-nook/logging.yaml

Usage:
    from agent_nook.utils.logger import main_logger

    # Initialize logging (call once at application startup)
    logger = main_logger()
    logger.info("This is an info message")

    # Get child loggers (automatically configured)
    log = logger.getChild("my_module")
    log.debug("This is a debug message")
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path


# Module-level initialization flag
_initialized: bool = False

# Cached logger instance
_logger: logging.Logger | None = None


def get_state_dir() -> str:
    """Resolve the state directory path using XDG_STATE_HOME.

    Reads XDG_STATE_HOME from the environment, falls back to
    ~/.local/state if not set.

    Returns:
        The absolute path to the state directory.
    """
    state_dir = os.environ.get("XDG_STATE_HOME")
    if not state_dir:
        state_dir = os.path.expanduser("~/.local/state")
    return Path(state_dir).expanduser()


def get_log_directory() -> str:
    """Get the log directory path.

    Returns:
        The absolute path to the log directory
        (e.g., ~/.local/state/agent-nook/logs).
    """
    return os.path.join(get_state_dir(), "logs")


def get_log_file_path() -> str:
    """Get the path to the main log file.

    Returns:
        The absolute path to the log file
        (e.g., ~/.local/state/agent-nook/logs/agent-nook.log).
    """
    return os.path.join(get_state_dir(), "logs", "agent-nook.log")


def _setup_logger(
    name: str = "agent_nook",
    level: str = "INFO",
    use_file: bool = True,
    use_console: bool = True,
) -> logging.Logger:
    """Internal function to configure and return a logger for Agent Nook.

    This function is private and should only be called by main_logger().

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

    # Check if already configured (has handlers)
    if logger.handlers:
        return logger

    # Create formatter (used by file and console handlers below)
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
                formatter = logging.Formatter(
                    fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
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


def main_logger(name: str = "agent_nook", level: str = "INFO") -> logging.Logger:
    """Set up logging and return the logger.

    This is the main entry point for setting up logging.
    It configures the logger with INFO level by default.

    Usage:
        from agent_nook.utils.logger import main_logger
        logger = main_logger()
        logger.info("Application started")

    Args:
        name: Logger name. Defaults to "agent_nook".
        level: Log level. Defaults to "INFO".

    Returns:
        Configured logger instance.

    Note:
        This function ensures logging is initialized exactly once.
        Subsequent calls return the appropriate logger for the given module name.
        All loggers will inherit the root "agent_nook" logger's handlers and formatters.
    """
    global _initialized, _logger

    # Ensure the root "agent_nook" logger is initialized first
    # This must be done before returning any logger, to ensure all loggers
    # share the same handlers and formatters.
    if not _initialized:
        # Call _setup_logger() which will return the configured logger
        # We must use the returned logger to ensure all references point
        # to the same configured instance
        _logger = _setup_logger(
            name="agent_nook",
            level=level,
            use_file=True,
            use_console=True,
        )
        _initialized = True

    # Return the logger for the requested name (will inherit root handlers)
    logger = logging.getLogger(name)
    logger.propagate = True  # Ensure messages propagate to root logger
    return logger
