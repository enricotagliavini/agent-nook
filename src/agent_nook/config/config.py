"""Sandbox configuration dataclasses and validation.

This module defines the dataclass schemas and validation logic.
It is the single source of truth for the SandboxConfig structure.
Both the config package and sandbox builder import from here.
"""

import re
from dataclasses import dataclass, field
from typing import Any


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Mount:
    """A unified mount specification.

    The type determines how the mount is converted to bwrap CLI arguments.

    Mount Types:
      - "bind":       --bind SRC DEST
      - "ro-bind":    --ro-bind SRC DEST
      - "dev-bind":   --dev-bind SRC DEST
      - "tmpfs":      --tmpfs TARGET [SIZE]
      - "proc":       --proc TARGET
      - "dev":        --dev TARGET
      - "dir":        --dir TARGET

    For "bind", "ro-bind", and "dev-bind", both source and target are required.
    For "tmpfs", only target is required; size is optional (empty = no size limit).
    For "proc", "dev", and "dir", only target is required.

    Attributes:
        source: Source path for bind/ro-bind/dev-bind mounts. Required for
                bind/ro-bind/dev-bind types.
        target: Target path in the sandbox. Required for bind/ro-bind/dev-bind,
                tmpfs, and dir. Defaults to /proc or /dev for proc/dev.
        type: The mount type. One of: bind, ro-bind, dev-bind, tmpfs, proc, dev, dir.
              Defaults to "bind".
        size: Size limit for tmpfs mounts. Can be a string with a size suffix
              (e.g., "100M", "500G") or an integer for bytes. Empty string means
              no size limit (uses bwrap default). Defaults to "".
        readonly: If True and type is "bind", produces --ro-bind instead of --bind.
                  (Deprecated; prefer type="ro-bind" directly.)
        device: If True and type is "bind", produces --dev-bind instead of --bind.
                (Deprecated; prefer type="dev-bind" directly.)
    """

    source: str = ""
    target: str = ""
    type: str = "bind"
    size: str = ""
    readonly: bool = False
    device: bool = False

    def _parse_size(self, size_str: str) -> int | None:
        """Parse a size string into bytes.

        Supports standard suffixes:
          - No suffix: plain bytes
          - B, KB, KiB: 1024-based
          - MB, MiB: 1024^2
          - GB, GiB: 1024^3
          - TB, TiB: 1024^4

        Args:
            size_str: Size string like "100M", "500G", or "1048576".

        Returns:
            Size in bytes, or None if empty string.

        Raises:
            ValueError: If the format is invalid (no number, invalid suffix).
        """
        if isinstance(size_str, (int, float)):
            return int(size_str)

        if isinstance(size_str, str):
            size_str = size_str.strip()
            if not size_str:
                return None  # No size limit

        match = re.match(r'^(-?\d+(?:\.\d+)?)\s*([A-Za-z]*)$', size_str)
        if not match:
            raise ValueError(f'Invalid size format: "{size_str}"')

        num_str, suffix = match.groups()
        try:
            num = float(num_str)
        except ValueError:
            raise ValueError(f'Invalid size format: "{size_str}"')

        multipliers = {
            '': 1, 'B': 1,
            'K': 1024, 'KB': 1024, 'KiB': 1024,
            'M': 1024**2, 'MB': 1024**2, 'MiB': 1024**2,
            'G': 1024**3, 'GB': 1024**3, 'GiB': 1024**3,
            'T': 1024**4, 'TB': 1024**4, 'TiB': 1024**4,
        }

        suffix = suffix.upper()
        if suffix not in multipliers:
            valid = ', '.join(sorted(multipliers.keys(), key=len))
            raise ValueError(
                f'Invalid size suffix: "{suffix}"'
                f' (valid: {valid})'
            )

        return int(num * multipliers[suffix])

    def build(self) -> list[str]:
        """Convert to bwrap CLI arguments based on mount type.

        Returns:
            A list of 1-4 strings representing bwrap CLI arguments.

        Raises:
            ValueError: If required fields are missing for the mount type.
        """
        mount_type = self.type.lower().strip()

        # dev-bind: requires device=True, source, target
        if mount_type == "dev-bind":
            if not self.source:
                raise ValueError(
                    f"Mount with type 'dev-bind' requires a non-empty source"
                )
            if not self.target:
                raise ValueError(
                    f"Mount with type 'dev-bind' requires a non-empty target"
                )
            if not self.device:
                raise ValueError(
                    f"Mount with type 'dev-bind' requires device=True"
                )
            return ["--dev-bind", self.source, self.target]

        # bind / ro-bind: need source and target
        if mount_type in ("bind", "ro-bind"):
            if not self.source:
                raise ValueError(
                    f"Mount with type '{mount_type}' requires a non-empty source"
                )
            if not self.target:
                raise ValueError(
                    f"Mount with type '{mount_type}' requires a non-empty target"
                )
            # If device=True, treat as dev-bind
            if self.device:
                return ["--dev-bind", self.source, self.target]
            # If readonly=True on a bind mount, produce --ro-bind
            if self.readonly and mount_type == "bind":
                return ["--ro-bind", self.source, self.target]
            if mount_type == "ro-bind":
                return ["--ro-bind", self.source, self.target]
            return ["--bind", self.source, self.target]

        # tmpfs: target + optional size
        if mount_type == "tmpfs":
            if not self.target:
                raise ValueError(
                    f"Mount with type 'tmpfs' requires a non-empty target"
                )
            if self.size:
                size_bytes = self._parse_size(self.size)
                if size_bytes is None:
                    return ["--tmpfs", self.target]
                return ["--tmpfs", self.target, str(size_bytes)]
            return ["--tmpfs", self.target]

        # proc: target defaults to /proc
        if mount_type == "proc":
            return ["--proc", self.target if self.target else "/proc"]

        # dev: target defaults to /dev
        if mount_type == "dev":
            return ["--dev", self.target if self.target else "/dev"]

        # dir: maps to --dir (bind mount as dir)
        if mount_type == "dir":
            if not self.target:
                raise ValueError(
                    f"Mount with type 'dir' requires a non-empty target"
                )
            return ["--dir", self.target]

        # Unknown type
        raise ValueError(
            f"Unknown mount type: '{mount_type}'. "
            f"Valid types: 'bind', 'ro-bind', 'dev-bind', 'tmpfs', 'proc', 'dev', 'dir'"
        )


@dataclass(frozen=True)
class CapabilitySet:
    """Capability drop/add configuration.

    Drop capabilities explicitly. If you want to drop all capabilities,
    set dropped=["ALL"]. If you want to keep specific capabilities,
    list them in kept. The two lists are mutually exclusive:
    if dropped is ["ALL"], kept must be empty.

    Defaults:
        - dropped: [] (drop nothing by default)
        - kept: [] (nothing kept by default)

    To drop all caps, use: CapabilitySet(dropped=["ALL"])
    To keep specific caps: CapabilitySet(kept=["CHOWN", "SETUID"])
    To drop specific caps: CapabilitySet(dropped=["NET_ADMIN", "NET_RAW"])
    """

    dropped: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate after construction."""
        if self.dropped == ["ALL"] and self.kept:
            raise ConfigValidationError(
                "Cannot keep capabilities when dropping ALL"
            )

    def validate(self) -> None:
        if self.dropped == ["ALL"] and self.kept:
            raise ConfigValidationError(
                "Cannot keep capabilities when dropping ALL"
            )

    def build(self) -> list[str]:
        """Convert to bwrap --cap-drop and --cap-add arguments.

        Returns:
            A list of bwrap CLI arguments.
        """
        args: list[str] = []
        if self.dropped:
            for cap in self.dropped:
                args.extend(["--cap-drop", cap])
        for cap in self.kept:
            args.extend(["--cap-add", cap])
        return args


@dataclass(frozen=True)
class NamespaceSet:
    """Namespaces to unshare.

    Defaults (all False, opt-in):
        - pid: False
        - uts: False
        - ipc: False
        - cgroup: False
        - user: False
        - network: False

    Only namespaces explicitly set to True will be unshared.
    """

    pid: bool = False
    uts: bool = False
    ipc: bool = False
    cgroup: bool = False
    user: bool = False
    network: bool = False

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


@dataclass(frozen=True)
class SandboxConfig:
    """Complete sandbox configuration.

    All fields are validated in __post_init__. Use build() or
    load() to construct instances — do not construct directly.

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
    root: str
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

    def __post_init__(self) -> None:
        """Validate configuration after construction."""
        if not self.name:
            raise ConfigValidationError("name cannot be empty")
        if not self.root:
            raise ConfigValidationError("root cannot be empty")
        if not self.mounts:
            raise ConfigValidationError("mounts cannot be empty")

        # Check capability conflict: cannot keep caps when dropping ALL
        if self.capabilities.dropped == ["ALL"] and self.capabilities.kept:
            raise ConfigValidationError(
                "Cannot keep capabilities when dropping ALL"
            )

        # Validate mount types
        valid_types = {"bind", "ro-bind", "dev-bind", "tmpfs", "proc", "dev", "dir"}
        for mount in self.mounts:
            if mount.type.lower().strip() not in valid_types:
                raise ConfigValidationError(
                    f"Mount type '{mount.type}' is not valid. "
                    f"Valid types: {', '.join(sorted(valid_types))}"
                )

    def validate(self) -> None:
        """Run full validation."""
        self.__post_init__()

        # Validate mounts
        for i, mount in enumerate(self.mounts):
            if not mount.source and mount.type in ("bind", "ro-bind", "dev-bind"):
                raise ConfigValidationError(
                    f"mount[{i}] with type '{mount.type}' requires a non-empty source"
                )
            if not mount.target and mount.type in (
                "bind", "ro-bind", "dev-bind", "dir", "tmpfs"
            ):
                raise ConfigValidationError(
                    f"mount[{i}] with type '{mount.type}' requires a non-empty target"
                )

        # Validate capabilities
        self.capabilities.validate()

    def build(self) -> list[str]:
        """Return complete bwrap CLI arguments.

        This builds the core bwrap arguments from the config.
        For full command construction including the actual command to run,
        use BwrapBuilder.build() instead.

        Returns:
            A list of bwrap CLI arguments. Does NOT include the command
            to run or its arguments.
        """
        args: list[str] = ["bwrap"]

        # Only add --die-with-parent if enabled (default True)
        if self.die_with_parent:
            args.append("--die-with-parent")
        if self.new_session:
            args.append("--new-session")

        # Timeout (if set and not None)
        if self.timeout is not None:
            args.extend(["--timeout", str(self.timeout)])

        # Proc mount
        for mount in self.mounts:
            if mount.type == "proc":
                args.extend(mount.build())

        # Dev mount
        for mount in self.mounts:
            if mount.type == "dev":
                args.extend(mount.build())

        # Tmpfs mounts
        for mount in self.mounts:
            if mount.type == "tmpfs":
                args.extend(mount.build())

        # Other mounts (bind, ro-bind, dev-bind, dir)
        for mount in self.mounts:
            if mount.type not in ("proc", "dev", "tmpfs"):
                args.extend(mount.build())

        # Capabilities
        args.extend(self.capabilities.build())

        # Namespaces
        for ns in self.unshare.namespaces:
            args.append(f"--unshare={ns}")

        # Hostname (requires --unshare-uts)
        if self.hostname is not None:
            args.extend(["--unshare-uts", "--hostname", self.hostname])

        # Environment
        for key, value in self.env_vars.items():
            args.extend(["--setenv", key, str(value)])
        for var in self.unenv_vars:
            args.extend(["--unsetenv", var])

        # Root
        args.append(self.root)

        return args


# Global config — lazily initialized
nook_config: SandboxConfig | None = None
_config_path: str | None = None


def get_config() -> SandboxConfig:
    """Get the current config. Raises RuntimeError if not set."""
    global nook_config
    if nook_config is None:
        raise RuntimeError("No config loaded. Call ConfigLoader.load() first.")
    return nook_config


def set_config(config: SandboxConfig) -> None:
    """Set the global config."""
    global nook_config
    config.validate()
    nook_config = config


def reset_config() -> None:
    """Reset the global config."""
    global nook_config, _config_path
    nook_config = None
    _config_path = None


__all__ = [
    "SandboxConfig",
    "Mount",
    "CapabilitySet",
    "NamespaceSet",
    "ConfigValidationError",
    "get_config",
    "set_config",
    "reset_config",
    "nook_config",
]
