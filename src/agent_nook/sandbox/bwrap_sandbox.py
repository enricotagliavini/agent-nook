"""Unified sandbox executor combining config + builder + execution.

Usage:
    from agent_nook.sandbox import BwrapSandbox, BwrapBuilder, SandboxConfig

    # Preferred: use BwrapSandbox.run() which handles execution
    result = BwrapSandbox.run(
        command=["python3", "agent.py"],
        config=config,
    )

    # Manual builder
    cmd = BwrapBuilder(config).build(["python3", "agent.py"])

    # Context manager
    with BwrapSandbox(config).run(command=["echo", "hello"]) as result:
        if result.success:
            _logger.info("Command executed successfully")
"""

import subprocess
from dataclasses import dataclass

# BwrapError and SandboxExecutionError have been removed; use RuntimeError instead
from pathlib import Path

from agent_nook.config.config import SandboxConfig
from agent_nook.sandbox.builder import BwrapBuilder
from agent_nook.utils.logger import main_logger


# Module-level logger — uses centralized main_logger
_logger = main_logger(__name__)


@dataclass
class SandboxResult:
    """Result of a sandboxed command execution.

    Attributes:
        success: Whether the command exited with code 0.
        return_code: The exit code of the command.
    """

    success: bool
    return_code: int

    def __post_init__(self) -> None:
        """Validate the result."""
        pass

    def __enter__(self) -> "SandboxResult":
        """Context manager entry - returns self."""
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: object | None) -> None:
        """Context manager exit - no-op for clean exit paths."""


class BwrapSandbox:
    """Unified sandbox executor combining config, builder, and execution.

    A single object that holds a SandboxConfig and can build or run
    commands in a bubblewrap sandbox.

    Usage:
        # Quick API
        result = BwrapSandbox.run(["python3", "agent.py"])

        # With config
        result = BwrapSandbox(config).run(
            command=["python3", "agent.py"],
        )

        # Context manager
        with BwrapSandbox(config).run(command=["echo", "hello"]) as result:
            if result.success:
                _logger.info("Command executed successfully")

        # Manual builder (for advanced use)
        builder = BwrapSandbox(config).builder
        cmd = builder.build(["python3", "agent.py"])
        subprocess.run(cmd)

    The BwrapSandbox class encapsulates the full lifecycle:
      1. Holds the SandboxConfig
      2. Builds the bwrap command line via BwrapBuilder
      3. Executes the command
      4. Handles errors and cleanup
    """

    def __init__(
        self,
        config: SandboxConfig | None = None,
        command: list[str] | None = None,
    ) -> None:
        """Initialize BwrapSandbox with a config and/or command.

        Args:
            config: REQUIRED sandbox configuration. Cannot be None.
            command: The command to run. Can be provided at init time
                and passed to `.build()` or `.run()`.

        Example:
            sandbox = BwrapSandbox(config)
            result = sandbox.run(["echo", "hello"])
        """
        if config is None:
            raise ValueError("config is required and cannot be None")
        self._config = config
        self._command = command
        self._builder = BwrapBuilder(self._config)

    @property
    def config(self) -> SandboxConfig:
        """Return the sandbox configuration."""
        return self._config

    @property
    def builder(self) -> BwrapBuilder:
        """Return the underlying BwrapBuilder for manual control.

        Use this if you need granular control over command construction.
        """
        return self._builder

    def build(self, command: list[str]) -> list[str]:
        """Build the bwrap command line.

        Args:
            command: The command and arguments to execute.

        Returns:
            The complete bwrap command line.
        """
        return self._builder.build(command)

    @staticmethod
    def run(
        command: list[str],
        config: SandboxConfig,
    ) -> SandboxResult:
        """Run a command inside a sandbox using the given config.

        Args:
            command: The command and arguments to execute.
            config: REQUIRED sandbox configuration. Cannot be None.

        Returns:
            SandboxResult with execution details.

        Example:
            result = BwrapSandbox.run(["/bin/echo", "hello"])
            if result.success:
                _logger.info("Command executed successfully")
        """
        if config is None:
            raise ValueError("config is required and cannot be None")

        _logger.debug("Running sandbox with config: name=%s, command=%s", config.name, " ".join(command))

        bwrap_cmd = BwrapSandbox._build_command(config, command)

        _logger.info("Full bwrap command: %s", " ".join(bwrap_cmd), extra={"command_only": True})

        # Run without capturing output - stdout/stderr go directly to console
        result = subprocess.run(
            bwrap_cmd,
            timeout=config.timeout,
            check=False,
        )

        return SandboxResult(
            success=result.returncode == 0,
            return_code=result.returncode,
        )

    @staticmethod
    def build_command(
        config: SandboxConfig,
        command: list[str] | None = None,
    ) -> list[str]:
        """Build the bwrap command line from a configuration.

        Args:
            config: The sandbox configuration.
            command: Optional command to append.

        Returns:
            The complete bwrap command line.
        """
        return BwrapSandbox._build_command(config, command)

    @staticmethod
    def _build_command(
        config: SandboxConfig,
        command: list[str] | None,
    ) -> list[str]:
        """Internal: build the complete bwrap command line.

        This delegates to BwrapBuilder for the actual construction.
        """
        builder = BwrapBuilder(config)
        cmd_args = command if command is not None else []
        return builder.build(cmd_args)

    @classmethod
    def from_config_file(
        cls,
        path: str | Path,
        command: list[str] | None = None,
    ) -> "BwrapSandbox":
        """Load config from a file and create a BwrapSandbox.

        Args:
            path: Path to the YAML config file.
            command: Optional default command.

        Returns:
            A BwrapSandbox instance loaded from the config file.
        """
        from agent_nook.config.loader import ConfigLoader

        config_loader = ConfigLoader()
        config = config_loader.load(path)
        return cls(config=config, command=command)


__all__ = ["BwrapSandbox", "SandboxConfig", "SandboxResult"]
