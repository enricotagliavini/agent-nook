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


__all__ = [
    "get_log_directory",
    "get_log_file_path",
    "get_state_dir",
    "main_logger",
]


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


def get_log_file_path(sandbox_name: str | None = None) -> str:
    """Get the path to the main log file.

    Returns:
        The absolute path to the log file.
        If sandbox_name is provided, returns nook-{sandbox_name}.log.
        Otherwise, returns agent-nook.log.
    """
    filename = f"nook-{sandbox_name}.log" if sandbox_name else "agent-nook.log"
    return os.path.join(get_log_directory(), filename)


def _setup_logger(
    name: str = "agent_nook",
    level: str = "INFO",
    use_file: bool = True,
    use_console: bool = True,
    log_file: str | None = None,
) -> logging.Logger:
    """Internal function to configure and return a logger for Agent Nook.

    This function is private and should only be called by main_logger().

    Args:
        name: The logger name.
        level: Log level as string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        use_file: Whether to enable file logging (requires write permissions).
        use_console: Whether to enable console logging.
        log_file: Optional absolute path to the log file.

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
            log_file = log_file or get_log_file_path()

            handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=10,
                encoding="utf-8",
            )
            handler.setLevel(logging.DEBUG)  # File gets everything
            handler.setFormatter(formatter)
            handler.addFilter(lambda record: record.levelno >= logging.DEBUG)
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
                handler.addFilter(lambda record: record.levelno >= logging.DEBUG)
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


def reconfigure_logger(sandbox_name: str) -> None:
    """Get or reconfigure logger for sandbox-specific log file.

    Note: For CLI usage, this is rarely needed as logging is typically
    configured once at startup. This function is primarily for testing.

    Args:
        sandbox_name: The name of the sandbox from the config file.
    """
    logger = logging.getLogger("agent_nook")

    if not logger.handlers:
        # First time setup - call _setup_logger
        _setup_logger(
            name="agent_nook",
            level="INFO",
            use_file=True,
            use_console=True,
            log_file=get_log_file_path(sandbox_name),
        )
        return

    # Reconfigure existing handler if it's a RotatingFileHandler
    for i, handler in enumerate(logger.handlers):
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.close()
            logger.removeHandler(handler)

            # Add new handler with sandbox-specific log file
            new_handler = logging.handlers.RotatingFileHandler(
                get_log_file_path(sandbox_name),
                maxBytes=10 * 1024 * 1024,
                backupCount=10,
                encoding="utf-8",
            )
            new_handler.setLevel(logging.DEBUG)
            new_handler.setFormatter(
                logging.Formatter(
                    fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            new_handler.addFilter(lambda record: record.levelno >= logging.DEBUG)
            logger.addHandler(new_handler)
            break


def main_logger(name: str = "agent_nook", level: str = "INFO") -> logging.Logger:
    """Set up logging and return a logger for the given module.

    This is the main entry point for setting up logging. It ensures the root
    "agent_nook" logger is configured, then returns a logger for the given module
    that will inherit the root's handlers and formatters.

    Usage:
        from agent_nook.utils.logger import main_logger
        logger = main_logger()
        logger.info("Application started")

    Args:
        name: Logger name. Defaults to "agent_nook".
        level: Log level. Defaults to "INFO".

    Returns:
        Configured logger instance.
    """
    # Ensure the root "agent_nook" logger is configured first
    root_logger = logging.getLogger("agent_nook")
    if not root_logger.handlers:
        _setup_logger(
            name="agent_nook",
            level=level,
            use_file=True,
            use_console=True,
        )

    # Return logger for the requested name (will inherit root handlers)
    logger = logging.getLogger(name)
    logger.propagate = True  # Ensure messages propagate to root logger
    return logger
