"""Sandbox configuration dataclasses and validation.

This module defines the dataclass schemas and validation logic.
It is the single source of truth for the SandboxConfig structure.
Both the config package and sandbox builder import from here.
"""

from dataclasses import dataclass, field
from typing import Any


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Mount:
    """A bind mount specification."""

    source: str
    target: str
    readonly: bool = False
    device: bool = False

    def build(self) -> list[str]:
        """Convert to bwrap CLI arguments."""
        if self.device:
            return ["--dev-bind", self.source, self.target]
        if self.readonly:
            return ["--ro-bind", self.source, self.target]
        return ["--bind", self.source, self.target]


@dataclass(frozen=True)
class TmpfsMount:
    """A tmpfs mount specification."""

    target: str
    size: str = ""
    device: bool = False

    def build(self) -> list[str]:
        """Convert to bwrap --tmpfs CLI arguments."""
        if self.size:
            return ["--tmpfs", self.target, self.size]
        return ["--tmpfs", self.target]


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
    """

    dropped: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate after construction.

        Raises ConfigValidationError if both dropped=["ALL"] and kept is non-empty.
        """
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
        """Convert to bwrap --cap-drop and --cap-add arguments."""
        args: list[str] = []
        if self.dropped == ["ALL"]:
            args.append("--cap-drop=ALL")
        for cap in self.kept:
            args.extend(["--cap-add", cap])
        return args


@dataclass(frozen=True)
class NamespaceSet:
    """Namespaces to unshare."""

    pid: bool = True
    uts: bool = True
    ipc: bool = True
    cgroup: bool = True
    user: bool = True
    network: bool = True
    network: bool = True

    @property
    def namespaces(self) -> list[str]:
        """Return list of namespace names to unshare."""
        ns_list: list[str] = []
        if self.pid:
            ns_list.append("pid")
        if self.uts:
            ns_list.append("uts")
        if self.ipc:
            ns_list.append("ipc")
        if self.cgroup:
            ns_list.append("cgroup")
        if self.user:
            ns_list.append("user")
        if self.network:
            ns_list.append("network")
        return ns_list


@dataclass(frozen=True)
class SandboxConfig:
    """Complete sandbox configuration.

    All fields are validated in __post_init__. Use build() or
    load() to construct instances — do not construct directly.
    """

    name: str
    root: str
    mounts: list[Mount] = field(default_factory=list)
    tmpfs_mounts: list[TmpfsMount] = field(default_factory=list)
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    unshare: NamespaceSet = field(default_factory=NamespaceSet)
    die_with_parent: bool = True
    new_session: bool = True
    hostname: str | None = None
    env_vars: dict[str, str] = field(default_factory=dict)
    unenv_vars: list[str] = field(default_factory=list)
    proc_enabled: bool = True
    dev_enabled: bool = True

    def __post_init__(self) -> None:
        """Validate configuration after construction."""
        if not self.name:
            raise ConfigValidationError("name cannot be empty")
        if not self.root:
            raise ConfigValidationError("root cannot be empty")
        if not self.mounts:
            raise ConfigValidationError("mounts cannot be empty")
        if not self.tmpfs_mounts:
            raise ConfigValidationError("tmpfs_mounts cannot be empty")

        # Check capability conflict: cannot keep caps when dropping ALL
        if self.capabilities.dropped == ["ALL"] and self.capabilities.kept:
            raise ConfigValidationError(
                "Cannot keep capabilities when dropping ALL"
            )

    def validate(self) -> None:
        """Run full validation."""
        self.__post_init__()

        # Validate mounts
        for i, mount in enumerate(self.mounts):
            if not mount.source:
                raise ConfigValidationError(
                    f"mount[{i}] has empty source"
                )
            if not mount.target:
                raise ConfigValidationError(
                    f"mount[{i}] has empty target"
                )

        # Validate tmpfs mounts
        for i, tmpfs in enumerate(self.tmpfs_mounts):
            if not tmpfs.target:
                raise ConfigValidationError(
                    f"tmpfs_mounts[{i}] has empty target"
                )

        # Validate capabilities
        self.capabilities.validate()

    def build(self) -> list[str]:
        """Return complete bwrap CLI arguments.

        This builds the core bwrap arguments from the config.
        For full command construction including the actual command to run,
        use BwrapBuilder.build() instead.
        """
        args: list[str] = ["bwrap"]

        # Only add --die-with-parent if enabled (default True)
        if self.die_with_parent:
            args.append("--die-with-parent")
        if self.new_session:
            args.append("--new-session")

        # Proc and dev mounts (conditional on flags)
        if self.proc_enabled:
            args.extend(["--proc", "/proc"])
        if self.dev_enabled:
            args.extend(["--dev", "/dev"])

        # Mounts
        for mount in self.mounts:
            args.extend(mount.build())

        # Tmpfs mounts
        for tmpfs in self.tmpfs_mounts:
            args.extend(tmpfs.build())

        # Capabilities
        args.extend(self.capabilities.build())

        # Namespaces
        for ns in self.unshare.namespaces:
            args.append(f"--unshare={ns}")

        # Hostname (requires --unshare-uts)
        if self.hostname is not None:
            args.extend(["--unshare-uts", f"--hostname={self.hostname}"])

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
    "TmpfsMount",
    "CapabilitySet",
    "NamespaceSet",
    "ConfigValidationError",
    "get_config",
    "set_config",
    "reset_config",
    "nook_config",
]
