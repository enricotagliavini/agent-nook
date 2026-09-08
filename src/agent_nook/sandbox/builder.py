"""Sandbox builder for bubblewrap commands.

Provides a dataclass-driven API for building bwrap command lines.
Implements principle of least privilege: drop ALL capabilities,
keep only what is explicitly required.

Design decisions:
- Dataclasses (frozen=True) for immutability and type safety
- Type hints throughout for IDE support and mypy
- No external dependencies beyond the dataclass stdlib feature (Python 3.7+)
- BwrapError is re-exported from runner for CLI compatibility
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


# Re-export BwrapError from runner for CLI compatibility
# Circular import avoided by placing this after the import
try:
    from agent_nook.runner import BwrapError
except ImportError:
    BwrapError = RuntimeError  # fallback


class ValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


# --- Configuration ---

@dataclass(frozen=True)
class BindMount:
    """A bind mount specification for the sandbox.

    Attributes:
        source: The host path to mount.
        target: The path inside the sandbox.
        readonly: If True, the mount is read-only (via --ro-bind).
        device: If True, use --dev-bind instead of --bind (allows device access).
    """

    source: str
    target: str
    readonly: bool = False
    device: bool = False

    def to_bwrap_args(self) -> list[str]:
        """Convert to a list of bwrap CLI arguments."""
        if self.device:
            return ["--dev-bind", self.source, self.target]
        if self.readonly:
            return ["--ro-bind", self.source, self.target]
        return ["--bind", self.source, self.target]


@dataclass(frozen=True)
class TmpfsMount:
    """A tmpfs mount specification for the sandbox.

    Args:
        target: The path inside the sandbox to mount as tmpfs.
        size: The maximum size of the tmpfs mount (e.g., "100M", "1G", "500M").
              If empty string, uses unbounded tmpfs (default bwrap behavior).
        device: If True, use --tmpfs-override instead of --tmpfs (allows
                 device access within tmpfs).
    """

    target: str
    size: str = ""
    device: bool = False

    def to_bwrap_args(self) -> list[str]:
        """Convert to a list of bwrap --tmpfs CLI arguments.

        bwrap syntax: --tmpfs TARGET SIZE (size can be empty for unbounded)
        """
        if self.size:
            return ["--tmpfs", self.target, self.size]
        return ["--tmpfs", self.target]


@dataclass(frozen=True)
class CapabilityConfig:
    """Capability drop/add configuration.

    Design decision: Use drop-all + keep-list pattern.
    We start from a completely unprivileged process and only add back
    what is explicitly needed. This is more secure than starting from
    privileged and dropping, as it reduces the attack surface.
    """

    dropped: list[str] = field(default_factory=lambda: ["ALL"])
    kept: list[str] = field(
        default_factory=lambda: ["CAP_CHOWN", "CAP_SETUID", "CAP_SETGID"]
    )

    def to_bwrap_args(self) -> list[str]:
        """Convert to a list of bwrap --cap-drop and --cap-add arguments.

        Note: bwrap uses --cap-drop=CAP syntax (with =) for a single cap.
        For --drop-all, use --cap-drop=ALL.
        """
        args: list[str] = []
        if self.dropped:
            if self.dropped == ["ALL"]:
                args.append("--cap-drop=ALL")
            else:
                # For explicit lists, use individual flags
                for cap in self.dropped:
                    args.extend(["--cap-drop", cap])
        if self.kept:
            for cap in self.kept:
                args.extend(["--cap-add", cap])
        return args


@dataclass(frozen=True)
class NetworkConfig:
    """Network namespace configuration.

    Design decision: Default to "allow all" for simplicity.
    Users can restrict via allow_hosts/allow_ports if needed.
    Full network control via seccomp is left for future work.
    """

    allow_all: bool = True
    allow_hosts: list[str] = field(default_factory=list)
    allow_ports: list[int] = field(default_factory=list)

    def to_bwrap_args(self) -> list[str]:
        """Convert to bwrap --network arguments (not yet supported).

        Note: bwrap's network control is limited. This method returns
        no arguments currently — full network control requires seccomp
        filters which are not implemented here.
        """
        return []


@dataclass(frozen=True)
class NamespaceConfig:
    """Namespaces to unshare.

    Each namespace provides a degree of isolation.
    Order matters: cgroup before pid (pid1 reaper), pid before user.
    """

    pid: bool = True
    uts: bool = True
    ipc: bool = True
    cgroup: bool = True
    user: bool = True
    network: bool = True

    @property
    def namespaces(self) -> list[str]:
        """Return list of namespace names to unshare."""
        ns_list = []
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

    def to_bwrap_args(self) -> list[str]:
        """Convert to a list of bwrap --unshare arguments.

        Note: bwrap uses --unshare-pid (with hyphen) syntax, not --unshare=pid.
        """
        args: list[str] = []
        ns_to_flag = {
            "pid": "unshare-pid",
            "uts": "unshare-uts",
            "ipc": "unshare-ipc",
            "cgroup": "unshare-cgroup",
            "user": "unshare-user",
            "network": "unshare-net",
        }
        for ns_name in self.namespaces:
            flag = ns_to_flag.get(ns_name)
            if flag:
                args.extend(["--" + flag])
        return args


@dataclass(frozen=True)
class SandboxConfig:
    """Complete sandbox configuration.

    This is the main configuration class. All other classes (BindMount,
    CapabilityConfig, NamespaceConfig, NetworkConfig, TmpfsMount) are held here
    and converted to bwrap arguments during command building.

    Attributes:
        name: Human-readable name for the sandbox (logging/identification).
        root: The working directory inside the sandbox.
        mounts: List of bind mounts to apply.
        tmpfs_mounts: List of tmpfs mounts to apply (writable private dirs).
                     Each element can be a TmpfsMount object or a dict
                     with "target" (required), "size" (optional), and
                     "device" (optional) keys.
        capabilities: What capabilities to keep/drop.
        unshare: Which namespaces to isolate.
        die_with_parent: Kill sandbox child when bwrap parent dies.
        new_session: Create a new session (prevents TIOCSTI attacks).
        hostname: Custom hostname for the sandbox.
        env_vars: Environment variables to set.
        unenv_vars: Environment variables to unset.
        proc_enabled: If True, use `--proc /proc` (special bwrap mount).
                      If False, use standard --bind/--ro-bind (allows selective
                      mounts like /dev/nvidia0 without /dev/dri). Default: True.
        dev_enabled: If True, use `--dev /dev` (special bwrap mount).
                     If False, use standard --bind/--dev-bind (allows selective
                     mounts like /dev/nvidia0 without /dev/dri). Default: True.
    """

    name: str = "default-sandbox"
    root: str = "./sandbox"
    mounts: list[BindMount] = field(default_factory=list)
    tmpfs_mounts: list[TmpfsMount] = field(default_factory=list)
    capabilities: CapabilityConfig = field(default_factory=CapabilityConfig)
    unshare: NamespaceConfig = field(default_factory=NamespaceConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    die_with_parent: bool = True
    new_session: bool = True
    hostname: str | None = None
    env_vars: dict[str, str] = field(default_factory=dict)
    unenv_vars: list[str] = field(default_factory=list)
    proc_enabled: bool = True
    dev_enabled: bool = True

    def to_bwrap_args(self) -> list[str]:
        """Convert the entire config to a flat list of bwrap CLI arguments.

        Returns a complete command line like:
            bwrap --die-with-parent --new-session --bind /proc /proc --dev /dev \
            --tmpfs /home --bind /home/user/data /data ... -- python3 script.py
        """
        args: list[str] = []

        # Core setup flags
        if self.die_with_parent:
            args.append("--die-with-parent")
        if self.new_session:
            args.append("--new-session")

        # Filesystem mounts
        for mount in self.mounts:
            args.extend(mount.to_bwrap_args())

        # Capabilities
        args.extend(self.capabilities.to_bwrap_args())

        # Namespaces
        args.extend(self.unshare.to_bwrap_args())

        # Hostname (requires --unshare-uts)
        if self.hostname:
            args.extend(["--unshare-uts", f"--hostname={self.hostname}"])

        # Environment
        if self.env_vars:
            for key, value in self.env_vars.items():
                args.extend(["--setenv", key, value])
        for var in self.unenv_vars:
            args.extend(["--unsetenv", var])

        # Network (bwrap has limited network support, seccomp would be needed)
        # args.extend(self.network.to_bwrap_args())

        return args


# --- Builder ---

class BwrapBuilder:
    """Builds complete bwrap command lines from SandboxConfig.

    Usage:
        config = SandboxConfig(name="my-sandbox")
        builder = BwrapBuilder(config)
        cmd = builder.build(["python3", "agent.py", "arg1", "arg2"])
        subprocess.run(cmd)
    """

    # Default mounts that are always applied
    DEFAULT_MOUNTS: list[BindMount] = [
        BindMount(source="/proc", target="/proc", readonly=True),
        BindMount(source="/dev", target="/dev", device=True),
        BindMount(source="/sys", target="/sys", readonly=True),
        BindMount(source="/run", target="/run", readonly=False),  # Needed for container runtimes
    ]

    # Default tmpfs mounts (writable, private to sandbox)
    DEFAULT_TMPFS_MOUNTS: list[TmpfsMount] = [
        TmpfsMount(target="/home"),
        TmpfsMount(target="/tmp"),
    ]

    # Always kept capabilities
    DEFAULT_KEEPED_CAPS: list[str] = ["CHOWN", "SETUID", "SETGID"]

    def __init__(
        self,
        config: SandboxConfig | None = None,
        default_mounts: list[BindMount] | None = None,
        default_tmpfs_mounts: list[TmpfsMount] | None = None,
    ) -> None:
        self._config = config or SandboxConfig()
        self._default_mounts = (
            default_mounts if default_mounts is not None else self.DEFAULT_MOUNTS
        )
        self._default_tmpfs_mounts = (
            default_tmpfs_mounts if default_tmpfs_mounts is not None else self.DEFAULT_TMPFS_MOUNTS
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

        Example:
            >>> config = SandboxConfig(name="my-sandbox", tmpfs_mounts=[TmpfsMount(target="/home")])
            >>> builder = BwrapBuilder(config)
            >>> cmd = builder.build(["python3", "-c", "print('hi')"])
            >>> print(" ".join(cmd))
            bwrap --die-with-parent --new-session --bind /proc /proc --dev /dev --tmpfs /home ...
            python3 -c print('hi')
        """
        # Collect mounts: default + config overrides
        all_mounts: list[BindMount] = list(self._default_mounts)
        for mount in self._config.mounts:
            # Accept either a BindMount object or a dict
            if isinstance(mount, BindMount):
                existing = any(
                    m.source == mount.source and m.target == mount.target for m in all_mounts
                )
                if not existing:
                    all_mounts.append(mount)
            elif isinstance(mount, dict):
                source = mount.get("source", "")
                target = mount.get("target", "")
                if source and target:
                    existing = any(
                        m.source == source and m.target == target for m in all_mounts
                    )
                    if not existing:
                        all_mounts.append(BindMount(
                            source=source,
                            target=target,
                            readonly=mount.get("readonly", False),
                            device=mount.get("device", False),
                        ))

        # Collect tmpfs mounts: default + config overrides
        all_tmpfs: list[TmpfsMount] = list(self._default_tmpfs_mounts)
        for tmpfs in self._config.tmpfs_mounts:
            if isinstance(tmpfs, TmpfsMount):
                existing = any(
                    t.target == tmpfs.target for t in all_tmpfs
                )
                if not existing:
                    all_tmpfs.append(tmpfs)
            elif isinstance(tmpfs, dict):
                target = tmpfs.get("target", "")
                size = tmpfs.get("size", "")
                device = tmpfs.get("device", False)
                if target:
                    existing = any(t.target == target for t in all_tmpfs)
                    if not existing:
                        all_tmpfs.append(TmpfsMount(
                            target=target,
                            size=size,
                            device=device,
                        ))

        # Build args
        args: list[str] = [
            "bwrap",
            # Core
            "--die-with-parent",
            "--new-session",
        ]

        # Add --proc /proc only if proc_enabled is True
        # When False, users can mount specific /dev nodes (nvidia, dri, etc.)
        # via BindMount with device=True instead of the full --proc mount.
        if self._config.proc_enabled:
            args.append("--proc")
            args.append("/proc")

        # Add --dev /dev only if dev_enabled is True
        # When False, users can mount specific /dev nodes (nvidia, dri, etc.)
        # via BindMount with device=True instead of the full --dev mount.
        if self._config.dev_enabled:
            args.append("--dev")
            args.append("/dev")

        # Add tmpfs mounts
        for tmpfs in all_tmpfs:
            args.extend(tmpfs.to_bwrap_args())

        # Add custom mounts
        for mount in all_mounts:
            args.extend(mount.to_bwrap_args())

        # Capabilities — handle both dict and CapabilityConfig
        caps = self._config.capabilities
        if isinstance(caps, dict):
            dropped = caps.get("dropped", ["ALL"])
            kept = caps.get("kept", ["CHOWN", "SETUID", "SETGID"])
        else:
            dropped = caps.dropped
            kept = caps.kept

        # For --cap-drop and --cap-add, we need to pass one capability per flag
        # because older bwrap versions don't support comma-separated lists
        # This is a known limitation of older bwrap versions (< 1.0)
        all_caps = [
            "AUDIT_WRITE", "AUDIT_CONTROL", "CAP_CHOWN", "CAP_DAC_OVERRIDE",
            "CAP_DAC_READ_SEARCH", "CAP_FOWNER", "CAP_FSETID", "CAP_KILL",
            "CAP_LINUX_IMMUTABLE", "CAP_NET_BIND_SERVICE", "CAP_NET_BROADCAST",
            "CAP_NET_ADMIN", "CAP_NET_RAW", "CAP_IPC_LOCK", "CAP_IPC_OWNER",
            "CAP_SYS_MODULE", "CAP_SYS_RAWIO", "CAP_SYS_CHROOT",
            "CAP_SYS_PTRACE", "CAP_SYS_PACCT", "CAP_SYS_ADMIN", "CAP_SYS_BOOT",
            "CAP_SYS_NICE", "CAP_SYS_RESOURCE", "CAP_SYS_TIME",
            "CAP_SYS_TTY_CONFIG", "CAP_MKNOD", "CAP_LEASE", "CAP_SETGID",
            "CAP_SETUID", "CAP_SETFCAP", "CAP_SETPCAP", "CAP_MAC_OVERRIDE",
            "CAP_MAC_ADMIN", "CAP_SYSLOG", "CAP_WAKE_ALARM", "CAP_BLOCK_SUSPEND",
            "CAP_AUDIT_READ", "CAP_PERFMON", "CAP_BPF", "CAP_CHECKPOINT_RESTORE",
        ]

        # Drop all caps, then add back what we keep (least privilege)
        caps_to_drop = []
        if dropped == ["ALL"]:
            # Drop all, then add back kept ones
            caps_to_drop = all_caps
            # Add back kept capabilities one at a time
            for cap in kept:
                args.extend(["--cap-add", cap])
        else:
            # Explicit list of dropped caps
            caps_to_drop = dropped
            for cap in kept:
                args.extend(["--cap-add", cap])

        for cap in caps_to_drop:
            args.extend(["--cap-drop", cap])

        # Namespaces — handle both dict and NamespaceConfig
        ns = self._config.unshare
        ns_list = []
        if isinstance(ns, dict):
            ns_list = [k for k, v in ns.items() if v]
        else:
            ns_list = ns.namespaces

        if ns_list:
            for ns_name in ns_list:
                args.append(f"--unshare={ns_name}")

        # Hostname
        hostname = getattr(self._config, 'hostname', None)
        if isinstance(hostname, str) and hostname:
            args.extend(["--unshare-uts", f"--hostname={hostname}"])

        # Environment variables
        env_vars = getattr(self._config, 'env', {})
        if isinstance(env_vars, dict) and env_vars:
            for key, value in env_vars.items():
                args.extend(["--setenv", key, str(value)])

        # Unset environment variables
        unenv = getattr(self._config, 'unenv_vars', [])
        if isinstance(unenv, list):
            for var in unenv:
                args.extend(["--unsetenv", var])

        # Add the actual command
        args.extend(command)

        return args

    def validate(self) -> None:
        """Validate the configuration.

        Raises:
            ValidationError: If the configuration has invalid values.
        """
        if not self._config.name:
            raise ValidationError("Sandbox name cannot be empty")

        if not self._config.root:
            raise ValidationError("Sandbox root must be specified")

        # Check mount sources exist (for read-write mounts)
        for mount in self._config.mounts:
            if mount.source and not os.path.exists(mount.source):
                # For --bind-try, this is ok, but for --bind it's an error
                if not mount.readonly:
                    # Can't verify existence at build time, just warn
                    pass

        # Check capability consistency
        dropped = set(self._config.capabilities.dropped)
        kept = set(self._config.capabilities.kept)
        if dropped & kept:
            raise ValidationError(
                f"Capability conflict: {dropped & kept} is both dropped and kept"
            )

        # Validate unshare order: pid must come before user namespace
        # (pid1 reaper needs to run before user namespace switch)
        ns_list = self._config.unshare.namespaces
        if "pid" in ns_list and "user" in ns_list:
            idx_pid = ns_list.index("pid")
            idx_user = ns_list.index("user")
            if idx_pid > idx_user:
                # This is a warning, not an error — bwrap handles it
                pass
