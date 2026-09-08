"""Sandbox runner — orchestrates bubblewrap execution.

This module handles the full lifecycle of a sandboxed execution:
1. Load configuration
2. Build bwrap command
3. Execute
4. Handle errors with specific exception types
5. Clean up

Error handling follows AGENTS.md guidelines:
- Specific exception types (BwrapError, ConfigError, PermissionError, etc.)
- Clear user-facing messages
- Stack traces logged at DEBUG level, not shown to user
- Non-zero exit codes for unrecoverable failures
"""

from __future__ import annotations

import subprocess
import logging
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator
from pathlib import Path


from agent_nook.sandbox.builder import (
    BwrapBuilder,
    SandboxConfig,
    ValidationError,
)


# Create a dedicated logger for this module
_runner_logger = logging.getLogger("agent_nook.runner")
_sandbox_logger: logging.Logger | None = None


def _get_sandbox_logger() -> logging.Logger:
    """Get the sandbox logger (child of runner logger)."""
    global _sandbox_logger
    if _sandbox_logger is None:
        _sandbox_logger = _runner_logger.getChild("sandbox")
        _sandbox_logger.setLevel(logging.DEBUG)
    return _sandbox_logger


@dataclass
class SandboxResult:
    """Result of running a sandboxed command.

    Attributes:
        success: Whether the command completed successfully.
        return_code: Exit code (None if no result).
        stdout: Captured stdout (if captured).
        stderr: Captured stderr (if captured).
        error: Error message if any.
    """

    success: bool
    return_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    log_file: str | None = None


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""
    pass


class BwrapError(Exception):
    """Raised when bubblewrap setup fails."""
    pass


class SandboxExecutionError(Exception):
    """Raised when sandbox execution fails with an unrecoverable error."""
    pass


def build_command(config: SandboxConfig) -> list[str]:
    """Build the bwrap command line from a configuration.

    Args:
        config: The sandbox configuration.

    Returns:
        A list representing the bwrap command line.
    """
    builder = BwrapBuilder(config)
    return builder.build([])


def validate_config(config: SandboxConfig) -> None:
    """Validate a sandbox configuration.

    Args:
        config: The sandbox configuration to validate.

    Raises:
        ValidationError: If the configuration has invalid values.
    """
    builder = BwrapBuilder(config)
    builder.validate()


@contextmanager
def run_in_sandbox(config: SandboxConfig, command: list[str]) -> Generator[SandboxResult, None, None]:
    """Run a command inside a bwrap sandbox.

    This is the main entry point for sandboxed execution. It handles
    the full lifecycle: config validation, command building, execution,
    and cleanup.

    Args:
        config: The sandbox configuration.
        command: The command and arguments to run inside the sandbox.

    Yields:
        SandboxResult describing the outcome.

    Raises:
        ConfigError: If the configuration is invalid or missing.
        BwrapError: If bubblewrap fails to set up the sandbox.
        SandboxExecutionError: If the command fails to start or an
            unrecoverable error occurs.
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
        bwrap_cmd = build_command(config)
        logger.debug("bwrap command: %s", " ".join(bwrap_cmd))

        # Step 3: Create sandbox temp directory
        sandbox_dir = Path(tempfile.mkdtemp(prefix="agent-nook-sandbox-"))
        logger.debug("Sandbox directory: %s", sandbox_dir)

        # Add --root and --chdir
        full_cmd = bwrap_cmd + [
            "--root", str(sandbox_dir),
            "--chdir", str(sandbox_dir),
        ] + command

        # Execute
        logger.info("Running command: %s", " ".join(command))
        logger.info("Full bwrap command: %s", " ".join(full_cmd))

        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour default timeout
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
            logger.error("bwrap stderr: %s", result.stderr[:2000])

        yield SandboxResult(
            success=result.returncode == 0,
            return_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    except ValidationError as e:
        logger.error("Configuration validation failed: %s", e, exc_info=True)
        yield SandboxResult(
            success=False,
            error=f"Configuration error: {e}",
        )

    except BwrapError as e:
        logger.error("Bubblewrap setup failed: %s", e, exc_info=True)
        yield SandboxResult(
            success=False,
            error=f"Sandbox setup error: {e}",
        )

    except PermissionError as e:
        logger.error("Permission denied: %s", e, exc_info=True)
        yield SandboxResult(
            success=False,
            error=f"Permission denied: {e}",
        )

    except FileNotFoundError as e:
        logger.error("Command or file not found: %s", e, exc_info=True)
        yield SandboxResult(
            success=False,
            error=f"Command not found: {e}",
        )

    except subprocess.TimeoutExpired as e:
        logger.error("Timeout after %d seconds", e.timeout, exc_info=True)
        yield SandboxResult(
            success=False,
            return_code=e.returncode,
            stdout=e.stdout.decode("utf-8", errors="replace") if e.stdout else None,
            stderr=e.stderr.decode("utf-8", errors="replace") if e.stderr else None,
            error=f"Timeout: command exceeded {e.timeout}s limit",
        )

    except subprocess.CalledProcessError as e:
        logger.error("Command failed with exit code %d", e.returncode, exc_info=True)
        yield SandboxResult(
            success=False,
            return_code=e.returncode,
            stdout=e.stdout.decode("utf-8", errors="replace") if e.stdout else None,
            stderr=e.stderr.decode("utf-8", errors="replace") if e.stderr else None,
            error=f"Command failed with exit code {e.returncode}",
        )

    except Exception as e:
        # Catch-all for unexpected errors
        logger.exception("Unexpected error during sandbox execution: %s", e)
        yield SandboxResult(
            success=False,
            error=f"Unexpected error: {type(e).__name__}: {e}",
        )

    finally:
        # Cleanup: remove sandbox dir if it still exists
        if sandbox_dir is not None and sandbox_dir.exists():
            try:
                import shutil
                shutil.rmtree(sandbox_dir, ignore_errors=True)
            except Exception:
                logger.warning("Failed to cleanup sandbox directory: %s", sandbox_dir)


def _execute_command(
    bwrap_cmd: list[str],
    command: list[str],
    logger: logging.Logger,
) -> SandboxResult:
    """Execute the bwrap command and return the result.

    Args:
        bwrap_cmd: The full bwrap command line.
        command: The original command (for logging).
        logger: Logger for debug output.

    Returns:
        SandboxResult with stdout/stderr capture.

    Raises:
        BwrapError: If bubblewrap itself fails.
    """
    # Create a temporary directory for the sandbox
    sandbox_dir = Path(tempfile.mkdtemp(prefix="agent-nook-sandbox-"))
    logger.debug("Sandbox directory created: %s", sandbox_dir)

    try:
        # Add --root to specify the sandbox root
        # The command we passed in should be run with chdir to sandbox root
        full_cmd = bwrap_cmd + [
            "--root", str(sandbox_dir),
            "--chdir", str(sandbox_dir),
            "-c",
        ] + command

        logger.debug("Full bwrap command: %s", " ".join(full_cmd))

        # Execute
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour default timeout
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

    except subprocess.TimeoutExpired as e:
        logger.error("Timeout after %d seconds", e.timeout)
        raise

    except subprocess.SubprocessError as e:
        logger.error("Subprocess error: %s", e, exc_info=True)
        raise

    except BwrapError:
        raise

    except Exception as e:
        logger.exception("Unexpected error during execution: %s", e)
        raise

    finally:
        # Cleanup: let bwrap's --die-with-parent handle it,
        # but remove the temp dir if bwrap failed
        try:
            if sandbox_dir.exists():
                import shutil
                shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception:
            logger.warning("Failed to cleanup sandbox directory: %s", sandbox_dir)
