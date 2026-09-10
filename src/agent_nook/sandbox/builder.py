"""Sandbox builder for bubblewrap commands.

Provides a dataclass-driven API for building bwrap command lines.
Accepts ONLY SandboxConfig dataclass — no dict normalization.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

from agent_nook.config.config import SandboxConfig, Mount, TmpfsMount

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
    """

    # Default mounts that are always applied
    DEFAULT_MOUNTS: list[Mount] = field(
        default_factory=lambda: [
            Mount(source="/proc", target="/proc", readonly=True),
            Mount(source="/dev", target="/dev", device=True),
            Mount(source="/sys", target="/sys", readonly=True),
            Mount(source="/run", target="/run", readonly=False),
        ]
    )

    # Default tmpfs mounts (writable, private to sandbox)
    DEFAULT_TMPFS_MOUNTS: list[TmpfsMount] = field(
        default_factory=lambda: [
            TmpfsMount(target="/home"),
            TmpfsMount(target="/tmp"),
        ]
    )

    def __init__(
        self,
        config: SandboxConfig,
        default_mounts: list[Mount] | None = None,
        default_tmpfs_mounts: list[TmpfsMount] | None = None,
    ) -> None:
        """Initialize the builder.

        Args:
            config: The sandbox configuration (must be a SandboxConfig dataclass).
            default_mounts: Optional override for default mounts.
            default_tmpfs_mounts: Optional override for default tmpfs mounts.

        Raises:
            ValueError: If config is not a SandboxConfig dataclass.
        """
        # Default mounts for the sandbox
        self.DEFAULT_MOUNTS: list[Mount] = [
            Mount(source="/proc", target="/proc"),
            Mount(source="/dev", target="/dev"),
            Mount(source="/sys", target="/sys"),
        ]
        # Default tmpfs mounts
        self.DEFAULT_TMPFS_MOUNTS: list[TmpfsMount] = [
            TmpfsMount(target="/tmp"),
            TmpfsMount(target="/run"),
            TmpfsMount(target="/var/tmp"),
        ]

        if not isinstance(config, SandboxConfig):
            raise ValueError(
                f"Expected SandboxConfig dataclass, got {type(config).__name__}. "
                "Use ConfigLoader.load() to obtain a SandboxConfig."
            )
        self._config = config
        self._default_mounts = (
            default_mounts if default_mounts is not None else self.DEFAULT_MOUNTS
        )
        self._default_tmpfs_mounts = (
            default_tmpfs_mounts
            if default_tmpfs_mounts is not None
            else self.DEFAULT_TMPFS_MOUNTS
        )

    @property
    def config(self) -> SandboxConfig:
        return self._config

    def build(self, command: list[str]) -> list[str]:
        """Build the complete bwrap command line.

        Args:
            command: The command and arguments to run inside the sandbox.

        Returns:
            A list that can be passed directly to subprocess.run().
        """
        # Collect mounts: config overrides defaults
        all_mounts: list[Mount] = []
        # Start with defaults
        all_mounts.extend(self._default_mounts)
        # Config mounts override defaults at the same source+target
        for mount in self._config.mounts:
            # Remove any default with the same source+target
            all_mounts = [
                m for m in all_mounts
                if not (m.source == mount.source and m.target == mount.target)
            ]
            all_mounts.append(mount)

        # Collect tmpfs mounts: config overrides defaults
        all_tmpfs: list[TmpfsMount] = []
        # Start with defaults
        all_tmpfs.extend(self._default_tmpfs_mounts)
        # Config tmpfs mounts override defaults at the same target
        for tmpfs in self._config.tmpfs_mounts:
            # Remove any default with the same target
            all_tmpfs = [t for t in all_tmpfs if t.target != tmpfs.target]
            all_tmpfs.append(tmpfs)

        # Build args
        args: list[str] = [
            "bwrap",
            "--die-with-parent",
            "--new-session",
        ]

        # Add --proc /proc only if proc_enabled is True
        if self._config.proc_enabled:
            args.append("--proc")
            args.append("/proc")

        # Add --dev /dev only if dev_enabled is True
        if self._config.dev_enabled:
            args.append("--dev")
            args.append("/dev")

        # Add tmpfs mounts
        for tmpfs in all_tmpfs:
            args.extend(tmpfs.build())

        # Add custom mounts
        for mount in all_mounts:
            args.extend(mount.build())

        # Capabilities
        args.extend(self._config.capabilities.build())

        # Namespaces
        for ns in self._config.unshare.namespaces:
            args.append(f"--unshare={ns}")

        # Hostname
        if self._config.hostname:
            args.extend(["--unshare-uts", f"--hostname={self._config.hostname}"])

        # Environment variables
        for key, value in self._config.env_vars.items():
            args.extend(["--setenv", key, str(value)])
        for var in self._config.unenv_vars:
            args.extend(["--unsetenv", var])

        # Add the actual command
        args.extend(command)

        return args

    def validate(self) -> None:
        """Validate the configuration.

        Raises:
            ConfigValidationError: If the configuration has invalid values.
        """
        self._config.validate()

        # Check mount sources exist (for read-write mounts)
        for mount in self._config.mounts:
            if mount.source and not os.path.exists(mount.source):
                if not mount.readonly:
                    # Can't verify existence at build time, just warn
                    pass


__all__ = ["BwrapBuilder", "BwrapError", "SandboxConfig"]
