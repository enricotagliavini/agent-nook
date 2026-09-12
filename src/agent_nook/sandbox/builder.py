"""Sandbox builder for bubblewrap commands.

Provides a dataclass-driven API for building bwrap command lines.
Accepts ONLY SandboxConfig dataclass — no dict normalization.

No validation, no normalization, no defaults — the builder is a pure
consumer of a validated SandboxConfig.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, fields
from typing import Any

from agent_nook.config.config import SandboxConfig

# Re-export BwrapError from runner for CLI compatibility
try:
    from agent_nook.runner import BwrapError
except ImportError:
    BwrapError = RuntimeError


@dataclass
class BwrapBuilder:
    """Builds complete bwrap command lines from SandboxConfig.

    Usage:
        config = SandboxConfig(name="my-sandbox")
        builder = BwrapBuilder(config)
        cmd = builder.build(["python3", "agent.py", "arg1", "arg2"])
        subprocess.run(cmd)

    The builder does NOT validate the config — it assumes the config
    has already been validated by ConfigLoader. It is a pure consumer
    that produces the final bwrap command line.
    """

    def __init__(self, config: SandboxConfig) -> None:
        """Initialize the builder.

        Args:
            config: The sandbox configuration (must be a SandboxConfig dataclass).

        Raises:
            ValueError: If config is not a SandboxConfig dataclass.
        """
        if not isinstance(config, SandboxConfig):
            raise ValueError(
                f"Expected SandboxConfig dataclass, got {type(config).__name__}. "
                "Use ConfigLoader.load() to obtain a SandboxConfig."
            )
        self._config = config

    @property
    def config(self) -> SandboxConfig:
        """Return the config as-is (no validation, no transformation)."""
        return self._config

    def build(self, command: list[str]) -> list[str]:
        """Build the complete bwrap command line.

        Args:
            command: The command and arguments to run inside the sandbox.

        Returns:
            A list that can be passed directly to subprocess.run().
        """
        args: list[str] = ["bwrap", "--die-with-parent", "--new-session"]

        # Proc mount (from Mount with type="proc")
        for mount in self._config.mounts:
            if mount.type == "proc":
                args.extend(mount.build())

        # Dev mount (from Mount with type="dev")
        for mount in self._config.mounts:
            if mount.type == "dev":
                args.extend(mount.build())

        # Tmpfs mounts (from Mount with type="tmpfs")
        for mount in self._config.mounts:
            if mount.type == "tmpfs":
                args.extend(mount.build())

        # Other mounts (bind, ro-bind, dev-bind, dir)
        for mount in self._config.mounts:
            if mount.type not in ("proc", "dev", "tmpfs"):
                args.extend(mount.build())

        # Capabilities
        args.extend(self._config.capabilities.build())

        # Namespaces
        # bwrap uses --unshare-<type> syntax (not --unshare TYPE)
        if self._config.unshare.pid:
            args.extend(["--unshare-pid"])
        if self._config.unshare.uts:
            args.extend(["--unshare-uts"])
        if self._config.unshare.ipc:
            args.extend(["--unshare-ipc"])
        if self._config.unshare.cgroup:
            args.extend(["--unshare-cgroup"])
        if self._config.unshare.user:
            args.extend(["--unshare-user"])
        if self._config.unshare.network:
            args.extend(["--unshare-net"])

        # Hostname
        if self._config.hostname:
            args.extend(["--unshare-uts", "--hostname", self._config.hostname])

        # Environment variables
        for key, value in self._config.env_vars.items():
            args.extend(["--setenv", key, str(value)])
        for var in self._config.unenv_vars:
            if var == "ALL":
                args.append("--clearenv")
            else:
                args.extend(["--unsetenv", var])

        # Chdir
        if self._config.chdir:
            args.extend(["--chdir", self._config.chdir])

        # Add the actual command
        args.extend(command)

        return args

    def validate(self) -> None:
        """Validate the configuration.

        Note: This method is kept for API compatibility but does nothing.
        The SandboxConfig dataclass performs all validation in __post_init__.
        The builder is a pure consumer and does no validation.

        Raises:
            ConfigValidationError: If the configuration has invalid values.
        """
        # No validation here — the config is assumed validated
        # This is kept for API compatibility but is a no-op
        pass


__all__ = ["BwrapBuilder", "BwrapError", "SandboxConfig"]
