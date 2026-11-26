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

import io
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
    "log_file_handle",
    "main_logger",
    "set_sandbox_name",
]

# The resolved log-file path, recorded once by _setup_logger() from the file
# handler it creates (or its /tmp fallback). It is the single source of truth
# for get_log_directory()/get_log_file_path(), so those always agree with where
# the logger actually writes — no matter when or how often they are called.
# It is None until main_logger() finishes setup, at which point the getters
# raise instead of re-running the (order-dependent) path resolution logic.
_log_file_path: str | None = None


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

    Returns the directory of the log file the configured logger actually
    writes to (the XDG state dir, or ``/tmp/agent-nook-logs`` on fallback).

    Returns:
        The absolute path to the log directory
        (e.g., ~/.local/state/agent-nook).

    Raises:
        RuntimeError: If called before main_logger() has finished setting up
            the logger, so the resolved path is not yet known.
    """
    if _log_file_path is None:
        raise RuntimeError(
            "Log path not resolved yet: main_logger() has not finished "
            "setting up the logger. Call main_logger() before "
            "get_log_directory()/get_log_file_path()."
        )
    return os.path.dirname(_log_file_path)


def get_log_file_path() -> str:
    """Get the path to the main log file.

    Returns the exact path the configured file handler writes to (the XDG
    state dir, or ``/tmp/agent-nook-logs`` on fallback) — the single source of
    truth recorded at setup, not a freshly recomputed path.

    Returns:
        The absolute path to the log file
        (e.g., ~/.local/state/agent-nook/agent-nook.log).

    Raises:
        RuntimeError: If called before main_logger() has finished setting up
            the logger, so the resolved path is not yet known.
    """
    if _log_file_path is None:
        raise RuntimeError(
            "Log path not resolved yet: main_logger() has not finished "
            "setting up the logger. Call main_logger() before "
            "get_log_file_path()."
        )
    return _log_file_path


def log_file_handle() -> io.TextIOWrapper:
    """Open the log file for child-process stdio.

    Returns a file handle in append mode that a child process — e.g. an
    orchestration command — can inherit for its stdout/stderr. The child's
    output is therefore appended to the log instead of reaching the
    terminal, keeping the agent TUI clean.

    The path follows the same XDG / fallback rules as the logger's own file
    handler. The handle must be closed by the caller; closing it does not
    stop a still-running child, which keeps its own inherited copy of the
    file descriptor open until it exits.
    """
    log_file = get_log_file_path()
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    return open(log_file, "a", encoding="utf-8")


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
    global _log_file_path

    log_level = getattr(logging, level.upper(), logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Check if already configured (has handlers)
    if logger.handlers:
        # Re-apply the level to the console handler. Module-level loggers may
        # have configured the logger at import time, so an explicit level
        # given later (e.g. by the CLI) must still take effect. The file
        # handler intentionally stays at DEBUG to capture everything on disk.
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.setLevel(log_level)
        return logger

    # Create formatter (shared by the file and console handlers below)
    formatter = SandboxFormatter()

    # File handler (only if we can write to the state directory)
    if use_file:
        file_handler: logging.handlers.RotatingFileHandler | None = None
        try:
            log_dir = get_state_dir()
            os.makedirs(log_dir, exist_ok=True)
            log_file = log_file or os.path.join(log_dir, "agent-nook.log")

            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=10,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)  # File gets everything
            file_handler.setFormatter(formatter)
            file_handler.addFilter(lambda record: record.levelno >= logging.DEBUG)
            logger.addHandler(file_handler)
        except (PermissionError, OSError) as e:
            # If we can't write to XDG state, fallback to /tmp
            log_dir = os.path.join("/tmp", "agent-nook-logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "agent-nook.log")
            logger.warning(
                "Cannot write to XDG state dir (%s), falling back to /tmp: %s",
                get_state_dir(),
                e,
            )

            try:
                file_handler = logging.handlers.RotatingFileHandler(
                    log_file,
                    maxBytes=10 * 1024 * 1024,
                    backupCount=10,
                    encoding="utf-8",
                )
                file_handler.setLevel(logging.DEBUG)
                file_handler.setFormatter(formatter)
                file_handler.addFilter(lambda record: record.levelno >= logging.DEBUG)
                logger.addHandler(file_handler)
            except (PermissionError, OSError) as e2:
                logger.warning(
                    "Cannot write logs to %s or /tmp: %s",
                    log_file,
                    e2,
                )

        # Record the resolved path (XDG state dir, or the /tmp fallback) from
        # the handler itself. This is the single source of truth for
        # get_log_directory()/get_log_file_path(), so they always agree with
        # where the logger writes — no matter when they are called.
        if file_handler is not None:
            _log_file_path = file_handler.baseFilename

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


def main_logger(name: str = "agent_nook", level: str | None = None) -> logging.Logger:
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
        level: Log level to force (e.g. by the CLI at startup). When None,
            the default INFO level is applied only on the first
            configuration, so later import-time loggers cannot clobber a
            level the CLI has already set.

    Returns:
        Configured logger instance.
    """
    # An explicitly requested level always wins (e.g. CLI -v/--quiet).
    # Without one, only the first configuration applies, so import-time
    # module loggers can't clobber a level set earlier at startup.
    root_logger = logging.getLogger("agent_nook")
    effective_level = level if level is not None else ("INFO" if not root_logger.handlers else None)
    if effective_level is not None:
        _setup_logger(
            name="agent_nook",
            level=effective_level,
            use_file=True,
            use_console=True,
        )

    # Return logger for the requested name (will inherit root handlers)
    logger = logging.getLogger(name)
    logger.propagate = True  # Ensure messages propagate to root logger
    return logger
