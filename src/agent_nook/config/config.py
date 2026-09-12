"""Configuration dataclasses for the sandbox runner.

This module defines the core data structures used to represent sandbox
configurations. The configuration is loaded via ConfigLoader which validates
against a strict canonical schema.
"""

from __future__ import annotations

import logging
from typing import Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Mount:
    """A mount point for the sandbox.

    A single mount is specified as one of:

    - bind:     {"source": "/host/path", "target": "/sandbox/path"}
    - ro-bind:  {"source": "/host/path", "target": "/sandbox/path"}
    - dev-bind: {"source": "/dev/sda", "target": "/sandbox/dev"}
    - tmpfs:    {"target": "/tmp", "size": "100M"}
    - proc:     {"target": "/proc"}
    - dev:      {"target": "/dev"}
    - dir:      {"target": "/workspace"}

    Attributes:
        source: The source path (required for bind, ro-bind, dev-bind types).
        target: The target path inside the sandbox (required for all types).
        type: The mount type. Must be one of: bind, ro-bind, dev-bind,
              tmpfs, proc, dev, dir.
        device: True for dev-bind mounts.
        size: Human-readable size string for tmpfs mounts (e.g., "100M").
    """

    source: str | None = field(default=None, repr=False)
    target: str | None = field(default=None, repr=False)
    type: str = "bind"
    device: bool = False
    size: str = ""

    def build(self) -> list[str]:
        """Build the bwrap arguments for this mount.

        Returns:
            A list of bwrap CLI arguments for this mount.

        Raises:
            ValueError: If the mount is malformed (missing required fields).
        """
        args: list[str] = []

        if self.type.lower().strip() not in (
            "bind",
            "ro-bind",
            "dev-bind",
            "tmpfs",
            "proc",
            "dev",
            "dir",
        ):
            raise ValueError(
                f"Unknown mount type: {self.type}. "
                f"Valid types: bind, ro-bind, dev-bind, tmpfs, proc, dev, dir"
            )

        if self.type.lower().strip() in ("bind", "ro-bind", "dev-bind"):
            if self.source is None:
                raise ValueError(f"Mount type '{self.type}' requires a non-empty source")
            if self.target is None:
                raise ValueError(
                    f"Mount type '{self.type}' requires a non-empty target"
                )

            if self.type.lower().strip() == "ro-bind":
                args.extend(["--ro-bind", self.source, self.target])
            elif self.type.lower().strip() == "dev-bind":
                if self.device:
                    args.extend(["--dev-bind", self.source, self.target])
                else:
                    raise ValueError(
                        f"Mount type 'dev-bind' requires device=True: "
                        f"mount({self.source!r}, target={self.target!r}, "
                        f"type='dev-bind', device=True)"
                    )
            else:
                args.extend(["--bind", self.source, self.target])

        elif self.type.lower().strip() == "tmpfs":
            if self.target is None:
                raise ValueError("tmpfs mount requires a target")
            size_bytes = self._parse_size(self.size)
            if size_bytes is not None and size_bytes > 0:
                args.extend(["--tmpfs", self.target, str(size_bytes)])
            else:
                args.extend(["--tmpfs", self.target])

        elif self.type.lower().strip() == "proc":
            if self.target is None:
                args.extend(["--proc", "/proc"])
            else:
                args.extend(["--proc", self.target])

        elif self.type.lower().strip() == "dev":
            if self.target is None:
                args.extend(["--dev", "/dev"])
            else:
                args.extend(["--dev", self.target])

        elif self.type.lower().strip() == "dir":
            if self.target is None:
                raise ValueError("dir mount requires a target")
            args.extend(["--dir", self.target])

        return args

    @staticmethod
    def _parse_size(size_str: str) -> int | None:
        """Parse a human-readable size string into bytes.

        Args:
            size_str: Size string like "100M", "500G", "1024K", or "100".

        Returns:
            Size in bytes, or None if the input is empty or invalid.

        Raises:
            ValueError: If the format is invalid.
        """
        if not size_str:
            return None

        size_str = str(size_str).strip()
        if not size_str:
            return None

        units = {
            "B": 1,
            "K": 1024,
            "KB": 1024,
            "M": 1_048_576,
            "GB": 1_073_741_824,
            "G": 1_073_741_824,
            "T": 1_099_511_627_776,
            "TB": 1_099_511_627_776,
        }

        # Pattern: number (with optional decimal) followed by optional unit
        # Unit can be any alphabetic characters (we validate separately)
        match = __import__("re").compile(
            r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$"
        ).match(size_str)
        if not match:
            raise ValueError(
                f"Invalid size format: '{size_str}'. "
                f"Expected format: <number>U where U is B, K, KB, M, G, GB, T, or TB. "
                f"Examples: '1024', '100M', '500G', '1024K'"
            )

        num = float(match.group(1))
        unit = match.group(2)

        if not unit:
            return int(num)

        if unit not in units:
            raise ValueError(
                f"Invalid size suffix: '{unit}'. "
                f"Valid suffixes: B, K, KB, M, G, GB, T, TB."
            )

        return int(num * units[unit])


class CapabilitySet:
    """Capability drop/add configuration.

    Drop capabilities explicitly using the "drop" field.
    List capabilities to keep using the "keep" field.

    Example:
        CapabilitySet(dropped=["ALL"], kept=["CAP_CHOWN", "CAP_DAC_OVERRIDE"])

    Notes:
        - Capability names are passed through unchanged to bwrap.
          Short names (e.g., "CHOWN") are NOT normalized. Use fully
          qualified names (e.g., "CAP_DAC_READ_SEARCH").
        - bwrap will reject unknown capability names with a native error.
    """

    def __init__(self, dropped: list[str] | None = None, kept: list[str] | None = None):
        self._dropped: list[str] = dropped if dropped is not None else []
        self._kept: list[str] = kept if kept is not None else []

    @property
    def dropped(self) -> list[str]:
        return self._dropped

    @property
    def kept(self) -> list[str]:
        return self._kept

    def build(self) -> list[str]:
        """Convert to bwrap --cap-drop and --cap-add arguments.

        Capability names are expected to be fully qualified (e.g.,
        "CAP_DAC_READ_SEARCH"). Short names are NOT normalized —
        users must use the full name.

        Returns:
            A list of bwrap CLI arguments.
        """
        args: list[str] = []
        if self._dropped:
            for cap in self._dropped:
                args.extend(["--cap-drop", cap])
        for cap in self._kept:
            args.extend(["--cap-add", cap])
        return args

    def __repr__(self) -> str:
        return (
            f"CapabilitySet(dropped={self._dropped!r}, kept={self._kept!r})"
        )


class NamespaceSet:
    """Namespaces to unshare.

    Only namespaces explicitly set to True will be unshared.
    Unspecified namespaces remain shared.

    Attributes:
        pid: Unshare PID namespace (isolates the sandbox process).
        uts: Unshare UTS namespace (isolates hostname).
        ipc: Unshare IPC namespace.
        cgroup: Unshare cgroup namespace.
        user: Unshare user namespace.
        network: Unshare network namespace.
    """

    def __init__(self, pid: bool = False, uts: bool = False, ipc: bool = False, cgroup: bool = False, user: bool = False, network: bool = False):
        self.pid = pid
        self.uts = uts
        self.ipc = ipc
        self.cgroup = cgroup
        self.user = user
        self.network = network

    @property
    def namespaces(self) -> list[str]:
        """Return list of namespace names to unshare, sorted alphabetically."""
        ns_list: list[str] = []
        if self.cgroup:
            ns_list.append("cgroup")
        if self.ipc:
            ns_list.append("ipc")
        if self.network:
            ns_list.append("network")
        if self.pid:
            ns_list.append("pid")
        if self.uts:
            ns_list.append("uts")
        if self.user:
            ns_list.append("user")
        return ns_list

    def __repr__(self) -> str:
        return (
            f"NamespaceSet(pid={self.pid}, uts={self.uts}, ipc={self.ipc}, "
            f"cgroup={self.cgroup}, user={self.user}, network={self.network})"
        )


@dataclass
class SandboxConfig:
    """Complete sandbox configuration.

    All fields are validated on construction. Use build() or
    ConfigLoader to construct instances.

    Mounts can use the unified type system:
        - type: "bind"     → --bind SRC DEST
        - type: "ro-bind"   → --ro-bind SRC DEST
        - type: "dev-bind"  → --dev-bind SRC DEST
        - type: "tmpfs"     → --tmpfs TARGET [SIZE]
        - type: "proc"      → --proc TARGET
        - type: "dev"       → --dev TARGET
        - type: "dir"       → --dir TARGET

    Timeout:
        - timeout: int or None (default) → No timeout
        - timeout: 60                    → Command fails after 60s
    """

    name: str
    chdir: str | None = None
    mounts: list[Mount] = field(default_factory=list)
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    unshare: NamespaceSet = field(default_factory=NamespaceSet)
    die_with_parent: bool = True
    new_session: bool = True
    hostname: str | None = None
    timeout: int | None = None
    env_vars: dict[str, str] = field(default_factory=dict)
    unenv_vars: list[str] = field(default_factory=list)
    _raw_config: dict[str, Any] = field(default_factory=dict, repr=False)

    def validate(self) -> None:
        """Run full validation.

        Note: Structural validation (field types, required fields) is performed
        by ConfigLoader._validate_structure() during load. This method only
        performs runtime checks that cannot be expressed in the canonical schema:
          - unshare.network implies unshare.uts and unshare.ipc
        """
        # Validate unshare namespaces
        if self.unshare.network:
            # --unshare-net implies --unshare-uts and --unshare-ipc
            if not self.unshare.uts:
                self.unshare.uts = True
            if not self.unshare.ipc:
                self.unshare.ipc = True

    def build(self) -> list[str]:
        """Build the complete bwrap command line.

        Args:
            command: Optional command and arguments to append.

        Returns:
            Full bwrap command as a list of strings.
        """
        cmd: list[str] = ["bwrap"]

        # Sandbox lifecycle
        if self.die_with_parent:
            cmd.append("--die-with-parent")
        if self.new_session:
            cmd.append("--new-session")

        # Mounts
        for mount in self.mounts:
            cmd.extend(mount.build())

        # Capabilities
        if self.capabilities._dropped or self.capabilities._kept:
            args = self.capabilities.build()
            cmd.extend(args)

        # Namespaces — order matters: pid, ipc, uts
        ns_list = []
        if self.unshare.network:
            ns_list.append("net")
        if self.unshare.ipc:
            ns_list.append("ipc")
        if self.unshare.pid:
            ns_list.append("pid")
        if self.unshare.uts:
            ns_list.append("uts")
        if self.unshare.user:
            ns_list.append("user")
        if self.unshare.cgroup:
            ns_list.append("cgroup")

        for ns in ns_list:
            ns_map = {
                "pid": "pid",
                "uts": "uts",
                "ipc": "ipc",
                "cgroup": "cgroup",
                "user": "user",
                "network": "net",
            }
            cmd.append(f"--unshare-{ns_map[ns]}")

        # Hostname — must come AFTER namespaces (order: namespaces, then hostname)
        if self.hostname:
            # Setting hostname implies uts namespace
            if not self.unshare.uts:
                self.unshare.uts = True
            # Rebuild ns_list only if uts was NOT already in the original ns_list
            # We check if "uts" was in the original ns_list (before hostname section)
            original_ns_list = ns_list.copy()
            if "uts" not in original_ns_list:
                # uts was not already added, so rebuild with uts
                ns_list = []
                if self.unshare.network:
                    ns_list.append("net")
                if self.unshare.ipc:
                    ns_list.append("ipc")
                if self.unshare.pid:
                    ns_list.append("pid")
                if self.unshare.uts:
                    ns_list.append("uts")
                if self.unshare.user:
                    ns_list.append("user")
                if self.unshare.cgroup:
                    ns_list.append("cgroup")
                for ns in ns_list:
                    ns_map = {
                        "pid": "pid",
                        "uts": "uts",
                        "ipc": "ipc",
                        "cgroup": "cgroup",
                        "user": "user",
                        "network": "net",
                    }
                    cmd.append(f"--unshare-{ns_map[ns]}")
            cmd.append("--hostname")
            cmd.append(self.hostname)

        # Timeout
        if self.timeout is not None:
            cmd.extend(["--timeout", str(self.timeout)])

        # Environment
        if self.env_vars:
            for key, value in self.env_vars.items():
                cmd.extend(["--setenv", key, value])
        if self.unenv_vars:
            if self.unenv_vars == ["ALL"]:
                cmd.append("--clearenv")
            else:
                cmd.extend(["--unsetenv"] + self.unenv_vars)

        # Command
        if (
            len(self.mounts) == 0
            and not self.capabilities._dropped
            and not self.capabilities._kept
        ):
            # Default mounts
            cmd.extend(["--proc", "/proc", "--dev", "/dev"])
            cmd.extend(["--tmpfs", "/home", "--tmpfs", "/tmp", "--tmpfs", "/var/tmp"])

        return cmd


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""

    pass


def set_config(config: SandboxConfig) -> None:
    """Set the current sandbox configuration.

    Args:
        config: The SandboxConfig to set.
    """
    global _CONFIG
    _CONFIG = config


def nook_config() -> SandboxConfig:
    """Get the current sandbox configuration.

    Returns:
        The current SandboxConfig instance.
    """
    global _CONFIG
    if _CONFIG is None:
        raise RuntimeError(
            "No sandbox configuration has been set. "
            "Use set_config() to configure the sandbox."
        )
    return _CONFIG


def reset_config() -> None:
    """Reset the current sandbox configuration to None."""
    global _CONFIG
    _CONFIG = None


# Module-level config
_CONFIG: SandboxConfig | None = None


__all__ = [
    "SandboxConfig",
    "Mount",
    "CapabilitySet",
    "NamespaceSet",
    "ConfigValidationError",
    "set_config",
    "nook_config",
    "reset_config",
]
