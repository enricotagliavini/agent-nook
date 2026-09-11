"""Sandbox runner — orchestrates bubblewrap execution.

This module handles the full lifecycle of a sandboxed execution:
1. Load configuration (from global nook_config)
2. Build bwrap command
3. Execute
4. Handle errors with specific exception types
5. Clean up

Error handling follows AGENTS.md guidelines:
- Specific exception types (BwrapError, ConfigError)
- Clear user-facing messages
- Stack traces logged at DEBUG level, not shown to user
- Non-zero exit codes for unrecoverable failures
"""

from __future__ import annotations

import subprocess
import logging
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_nook.config.config import SandboxConfig

from agent_nook.config.config import SandboxConfig
from agent_nook.config.loader import ConfigValidationError
from agent_nook.sandbox.builder import BwrapBuilder, BwrapError


ConfigError = ConfigValidationError


# Try to import the global nook_config
try:
    from agent_nook.config import nook_config
except ImportError:
    _nook_config = None


def get_global_config() -> SandboxConfig | None:
    """Get the global nook_config or return None."""
    global _nook_config
    if _nook_config is None:
        try:
            from agent_nook.config import nook_config
            _nook_config = nook_config
        except (ImportError, RuntimeError):
            _nook_config = None
    return _nook_config


# Create a dedicated logger for this module
_runner_logger = logging.getLogger("agent_nook.runner")
_sandbox_logger: logging.Logger | None = None


def _get_sandbox_logger() -> logging.Logger:
    """Get or create a dedicated sandbox logger."""
    global _sandbox_logger
    if _sandbox_logger is None:
        _sandbox_logger = logging.getLogger("agent_nook.runner.sandbox")
    return _sandbox_logger


@dataclass
class SandboxResult:
    """Result of a sandboxed command execution.

    Attributes:
        success: Whether the command exited with code 0.
        return_code: The exit code of the command.
        stdout: Standard output from the command (if captured).
        stderr: Standard error from the command (if captured).
    """

    success: bool
    return_code: int
    stdout: str | None = None
    stderr: str | None = None


def build_command(config: SandboxConfig, command: list[str] | None = None) -> list[str]:
    """Build the bwrap command line from a configuration.

    Args:
        config: The sandbox configuration (must be a SandboxConfig dataclass).
        command: The command and arguments to run inside the sandbox.
            If None, no command is appended to the bwrap command line.

    Returns:
        A list representing the bwrap command line.

    Raises:
        ValueError: If config is not a SandboxConfig dataclass.
    """
    builder = BwrapBuilder(config)
    cmd_args = command if command is not None else []
    return builder.build(cmd_args)


def validate_config(config: SandboxConfig) -> None:
    """Validate a sandbox configuration.

    Args:
        config: The sandbox configuration to validate.

    Raises:
        ConfigValidationError: If the configuration is invalid.
    """
    # Validate mounts
    if not isinstance(config.mounts, list):
        raise ConfigValidationError("mounts must be a list")

    for mount in config.mounts:
        try:
            mount.build()
        except ValueError as e:
            raise ConfigValidationError(f"Invalid mount: {e}")

    # Validate capabilities
    try:
        config.capabilities.build()
    except ValueError as e:
        raise ConfigValidationError(f"Invalid capabilities: {e}")


def run_in_sandbox(
    config: SandboxConfig,
    command: list[str],
    timeout: int | None = None,
) -> SandboxResult:
    """Run a command inside a bwrap sandbox.

    This is the main entry point for sandboxed execution. It handles
    the full lifecycle: config validation, command building, execution,
    and cleanup.

    Args:
        config: The sandbox configuration (must be a SandboxConfig dataclass).
        command: The command and arguments to run inside the sandbox.
        timeout: Optional timeout in seconds. Defaults to None (no timeout).
            If set, the command will be terminated after the specified duration
            with a TimeoutExpired exception.

    Returns:
        SandboxResult describing the outcome.

    Raises:
        ConfigError: If the configuration is invalid or missing.
        BwrapError: If bubblewrap fails to set up the sandbox.
        SandboxExecutionError: If the command fails to start or an
            unrecoverable error occurs.
        subprocess.TimeoutExpired: If the command exceeds the timeout.
    """
    logger = _get_sandbox_logger()

    logger.debug("Running sandbox with config: name=%s, command=%s",
                 config.name, " ".join(command))

    sandbox_dir = None
    try:
        # Step 1: Validate configuration
        validate_config(config)
        logger.debug("Validated configuration: name=%s, mounts=%d, caps=%s",
                     config.name, len(config.mounts), config.capabilities)

        # Step 2: Build the command
        bwrap_cmd = build_command(config, command)
        logger.debug("bwrap command: %s", " ".join(bwrap_cmd))

        # Step 3: Execute
        logger.info("Running command: %s", " ".join(command))
        logger.info("Full bwrap command: %s", " ".join(bwrap_cmd))

        result = _execute_command(bwrap_cmd, command, logger, timeout=timeout)

        return result

    except BwrapError as e:
        logger.error("Bubblewrap setup failed: %s", e, exc_info=True)
        raise

    except ConfigValidationError as e:
        logger.error("Configuration error: %s", e, exc_info=True)
        raise

    except subprocess.TimeoutExpired as e:
        logger.error("Timeout after %d seconds", e.timeout, exc_info=True)
        raise

    except Exception as e:
        # Catch-all for unexpected errors
        logger.exception("Unexpected error during sandbox execution: %s", e)
        raise SandboxExecutionError(
            f"Unexpected error: {type(e).__name__}: {e}"
        )


def _execute_command(
    bwrap_cmd: list[str],
    command: list[str],
    logger: logging.Logger,
    timeout: int | None = None,
) -> SandboxResult:
    """Execute the bwrap command and return the result.

    Args:
        bwrap_cmd: The full bwrap command line.
        command: The original command (for logging).
        logger: Logger for debug output.
        timeout: Maximum time to wait for the command in seconds.
            If None (default), no timeout is applied and the command
            runs until completion.

    Returns:
        SandboxResult with stdout/stderr capture.

    Raises:
        BwrapError: If bubblewrap itself fails.
        subprocess.TimeoutExpired: If the command exceeds the timeout.
    """
    # bwrap runs the command as-is inside the sandbox
    full_cmd = bwrap_cmd

    logger.debug("Full bwrap command: %s", " ".join(full_cmd))

    # Execute
    result = subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    # Log output for debugging (truncate to avoid log flooding)
    if result.stdout and len(result.stdout) > 2000:
        logger.debug("STDOUT (truncated): %s...", result.stdout[:2000])
    elif result.stdout:
        logger.debug("STDOUT: %s", result.stdout)

    if result.stderr and len(result.stderr) > 2000:
        logger.debug("STDERR (truncated): %s...", result.stderr[:2000])
    elif result.stderr:
        logger.debug("STDERR: %s", result.stderr)

    # Check for bwrap-specific errors
    if result.returncode != 0:
        if "not permitted" in result.stderr.lower():
            raise BwrapError(
                f"bwrap failed with permission error: {result.stderr[:500]}"
            )
        if "No such file or directory" in result.stderr:
            raise BwrapError(
                f"bwrap path error: {result.stderr[:500]}"
            )

    return SandboxResult(
        success=result.returncode == 0,
        return_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def run_in_sandbox_with_config(path: str | Path, command: list[str]) -> SandboxResult:
    """Load configuration from a file and run a command in a sandbox.

    Args:
        path: Path to a YAML configuration file.
        command: The command and arguments to run inside the sandbox.

    Returns:
        SandboxResult describing the outcome.

    Raises:
        ConfigValidationError: If the configuration is invalid.
        BwrapError: If bubblewrap fails to set up the sandbox.
        SandboxExecutionError: If execution fails.
    """
    from agent_nook.config.loader import ConfigLoader

    config = ConfigLoader().load(path)
    return run_in_sandbox(config, command)


class SandboxExecutionError(Exception):
    """Raised when a sandboxed command fails to execute."""

    pass
