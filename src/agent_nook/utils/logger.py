"""XDG-compliant logging setup for Agent Nook.

All log files are written to ~/.local/state/agent-nook/
by default, following the XDG Base Directory Specification.

Environment variables respected:
- XDG_STATE_HOME  -> $XDG_STATE_HOME/agent-nook/
- XDG_CONFIG_HOME -> ~/.config/agent-nook/logging.yaml

Usage:
    from agent_nook.utils.logger import main_logger

    # Initialize logging (call once at application startup)
    logger = main_logger()
    logger.info("This is an info message")

    # Get child loggers (automatically configured)
    log = logger.getChild("my_module")
    log.debug("This is a debug message")

    # After loading the config, tag subsequent lines with the sandbox name
    from agent_nook.utils.logger import set_sandbox_name
    set_sandbox_name(config.name)
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path


__all__ = [
    "SandboxFormatter",
    "get_log_directory",
    "get_log_file_path",
    "get_state_dir",
    "main_logger",
    "set_sandbox_name",
]


def get_state_dir() -> str:
    """Resolve the agent-nook state directory using XDG_STATE_HOME.

    XDG-compliant: the application gets its own subdirectory.
    Reads XDG_STATE_HOME from the environment, falls back to
    ~/.local/state if not set.

    Returns:
        The absolute path to the state directory
        (e.g., ~/.local/state/agent-nook).
    """
    state_dir = os.environ.get("XDG_STATE_HOME")
    if not state_dir:
        state_dir = os.path.expanduser("~/.local/state")
    return os.path.join(Path(state_dir).expanduser(), "agent-nook")


def get_log_directory() -> str:
    """Get the log directory path.

    Log files live directly in the application state directory.

    Returns:
        The absolute path to the log directory
        (e.g., ~/.local/state/agent-nook).
    """
    return get_state_dir()


def get_log_file_path() -> str:
    """Get the path to the main log file.

    Returns:
        The absolute path to the log file
        (e.g., ~/.local/state/agent-nook/agent-nook.log).
    """
    return os.path.join(get_log_directory(), "agent-nook.log")


class SandboxFormatter(logging.Formatter):
    """Formatter that tags log lines with the active sandbox name.

    Before a sandbox name is set (config not loaded yet) the logger name
    renders as-is (e.g. ``agent_nook``). Once set, it renders as
    ``name[sandbox]`` (e.g. ``agent_nook[mybox]``). Child loggers keep
    their dotted names, e.g. ``agent_nook.sandbox.bwrap_sandbox[mybox]``.

    The log file itself is never changed by the sandbox name.
    """

    def __init__(self, sandbox_name: str = "") -> None:
        super().__init__(
            fmt="%(asctime)s - %(agent_nook_name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.sandbox_name = sandbox_name

    def format(self, record: logging.LogRecord) -> str:
        # Compute a new attribute instead of mutating record.name: the same
        # LogRecord is formatted by every handler, so mutation would
        # double-suffix the name (e.g. agent_nook[box][box]).
        name = record.name
        if self.sandbox_name:
            name = f"{name}[{self.sandbox_name}]"
        record.agent_nook_name = name
        return super().format(record)


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

    Log file location: ~/.local/state/agent-nook/agent-nook.log

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

    # Create formatter (shared by the file and console handlers below)
    formatter = SandboxFormatter()

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


def set_sandbox_name(sandbox_name: str) -> None:
    """Tag subsequent log lines with the active sandbox name.

    Updates the SandboxFormatter on every handler of the "agent_nook"
    logger so both console and file output render the logger name as
    ``name[sandbox_name]`` from this point on. The log file itself is
    not changed.

    Args:
        sandbox_name: The name of the sandbox from the config file.
    """
    logger = logging.getLogger("agent_nook")
    for handler in logger.handlers:
        if isinstance(handler.formatter, SandboxFormatter):
            handler.formatter.sandbox_name = sandbox_name


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
