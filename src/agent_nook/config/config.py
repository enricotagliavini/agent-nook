"""Configuration dataclasses for the sandbox runner.

This module defines the core data structures used to represent sandbox
configurations. The configuration is loaded via ConfigLoader which validates
against a strict canonical schema.

## Validation Pipeline

The validation is layered across modules:

  **Layer 1 (loader.py, ConfigLoader._validate_structure()):**
    → Validates raw dicts against the canonical schema.
    → Ensures required fields are present and types are correct.
    → Rejects unknown keys, missing fields, wrong types.

  **Layer 2 (loader.py, ConfigLoader._parse_mounts()):**
    → Validates mount type enum values.
    → Validates required fields per mount type (e.g., tmpfs requires target).
    → Transforms dicts into Mount dataclass instances.

  **Layer 3 (loader.py, ConfigLoader._parse_capabilities()):**
    → Ensures "drop" and "keep" are lists of strings (not dicts, ints, etc.).
    → Transforms into CapabilitySet dataclass.

  **Layer 4 (loader.py, ConfigLoader._parse_unshare()):**
    → Ensures namespace keys are valid and values are booleans.
    → Transforms into NamespaceSet dataclass.

  **Layer 5 (config.py, SandboxConfig.__post_init__()):**
    → Enforces the dataclass invariant: at least one mount point is required.
      A config with zero mounts would create a completely empty sandbox.
    → This is the **last line of defense** before the config is used:
      every construction path (file, dict, programmatic) passes through it.

  **Layer 6 (sandbox/builder.py, BwrapBuilder.build()):**
    → Pure consumer: assumes config is already valid.
    → Does NOT repeat validation — it uses the validated config directly.
    → Errors here indicate a programming error (e.g., wrong type passed).

  **Layer 7 (sandbox/bwrap_sandbox.py, BwrapSandbox.run):**
    → Executes the command; handles runtime bwrap errors.
    → Does NOT repeat validation.

## Why multiple layers?

Each layer has a distinct purpose:
- Loader layers: structural validation of user input (YAML/JSON).
- Mount.build() / CapabilitySet / SandboxConfig.__post_init__:
  semantic validation that the dataclass invariants hold.
- No layer is truly redundant; each catches a different class of errors.
  Without Layer 1, raw dicts could slip through. Without Layer 5,
  semantically incorrect dataclass values could cause runtime failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

from agent_nook.utils.logger import main_logger


_logger = main_logger(__name__)


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

    ## Validation

    The build() method enforces the semantic invariants of a single mount:

      - Mount type must be one of: bind, ro-bind, dev-bind, tmpfs, proc, dev, dir.
      - Bind/ro-bind/dev-bind require both `source` and `target` to be non-None.
      - Dir/tmpfs require a non-None `target`.
      - Dev-bind requires `device=True`.

    build() is invoked by the builder (Layer 6, build_mounts_order()) when the
    bwrap command line is constructed. It is a defensive boundary that catches
    malformed Mount objects created or mutated programmatically. The
    config-level invariant (at least one mount point) is enforced earlier, at
    Layer 5, by SandboxConfig.__post_init__().

    Args:
        source: The source path (required for bind, ro-bind, dev-bind types).
        target: The target path inside the sandbox (required for all types).
        type: The mount type. Must be one of: bind, ro-bind, dev-bind, tmpfs,
              proc, dev, dir.
        device: True for dev-bind mounts (required when type="dev-bind").
        size: Human-readable size string for tmpfs mounts (e.g., "100M").
    """

    source: str | None = field(default=None, repr=False)
    target: str | None = field(default=None, repr=False)
    type: str = "bind"
    device: bool = False
    size: str = ""

    def build(self) -> list[str]:
        """Build the bwrap arguments for this mount.

        build() is invoked by the builder (Layer 6, build_mounts_order())
        when the bwrap command line is constructed. It validates that the
        Mount's type and fields are semantically consistent.

        Validation performed:
          - Mount type must be one of: bind, ro-bind, dev-bind, tmpfs, proc, dev, dir.
          - Bind/ro-bind/dev-bind require both `source` and `target` to be non-None.
          - Dir/tmpfs require a non-None `target`.
          - Dev-bind requires `device=True`.
          - Size is parsed and converted to bytes for tmpfs.

        Without this check, a Mount created programmatically with invalid
        fields could slip through to the bwrap builder, causing cryptic
        runtime errors or incorrect behavior. The config-level invariant
        (at least one mount point) is enforced by SandboxConfig.__post_init__().

        Returns:
            A list of bwrap CLI arguments for this mount.

        Raises:
            ValueError: If the mount is malformed (missing required fields,
                invalid type, or dev-bind without device=True).
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
            raise ValueError(f"Unknown mount type: {self.type}. Valid types: bind, ro-bind, dev-bind, tmpfs, proc, dev, dir")

        if self.type.lower().strip() in ("bind", "ro-bind", "dev-bind"):
            if self.source is None:
                raise ValueError(f"Mount type '{self.type}' requires a non-empty source")
            if self.target is None:
                raise ValueError(f"Mount type '{self.type}' requires a non-empty target")

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
        match = __import__("re").compile(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$").match(size_str)
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
            raise ValueError(f"Invalid size suffix: '{unit}'. Valid suffixes: B, K, KB, M, G, GB, T, TB.")

        return int(num * units[unit])


class CapabilitySet:
    """Capability drop/add configuration.

    This is **Layer 5** of the validation pipeline. The ConfigLoader
    (Layer 3) validates that "drop" and "keep" are lists of strings,
    then stores the validated data here. This class does not re-validate;
    it is a pure container for the already-validated capability sets.

    Drop capabilities explicitly using the "drop" field.
    List capabilities to keep using the "keep" field.

    Example:
        CapabilitySet(dropped=["ALL"], kept=["CAP_CHOWN", "CAP_NET_BIND_SERVICE"])

    Notes:
        - Capability names are passed through unchanged to bwrap.
          Short names (e.g., "CHOWN") are NOT normalized. Use fully
          qualified names (e.g., "CAP_DAC_READ_SEARCH").
        - bwrap will reject unknown capability names with a native error.

    Attributes:
        dropped: List of capability names to drop (e.g., ["ALL", "CAP_SYS_ADMIN"]).
        kept: List of capability names to explicitly keep (e.g., ["CAP_CHOWN"]).
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
        return f"CapabilitySet(dropped={self._dropped!r}, kept={self._kept!r})"


class NamespaceSet:
    """Namespaces to unshare.

    This is **Layer 5** of the validation pipeline. The ConfigLoader
    (Layer 4) validates that namespace keys are valid and values are
    booleans, then stores the validated data here. This class does not
    re-validate; it is a pure container for the already-validated
    namespace configuration.

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

    def __init__(
        self, pid: bool = False, uts: bool = False, ipc: bool = False, cgroup: bool = False, user: bool = False, network: bool = False
    ):
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

    Mounts can use the unified type system:
        - type: "bind"     → --bind SRC DEST
        - type: "ro-bind"   → --ro-bind SRC DEST
        - type: "dev-bind"  → --dev-bind SRC DEST
        - type: "tmpfs"     → --tmpfs TARGET [SIZE]
        - type: "proc"      → --proc TARGET
        - type: "dev"       → --dev TARGET
        - type: "dir"       → --dir TARGET

    Timeout:
        - timeout: int, float or None (default) → No timeout
        - timeout: 60                    → Command fails after 60s
        - timeout: 0.5                   → Command fails after half a second

    ## Validation Pipeline

    The SandboxConfig sits at **Layer 5** of the validation pipeline.
    ConfigLoader loads and validates config from YAML/JSON files, producing
    a fully-validated SandboxConfig dataclass. The dataclass invariants
    (e.g., mounts must be valid Mount objects) are enforced by ConfigLoader.

    BwrapBuilder (Layer 6) consumes the already-validated config and produces
    the final bwrap command line.

    ## Usage

    ```python
    from agent_nook.sandbox import BwrapSandbox

    # Preferred: use BwrapSandbox.run() which handles everything
    result = BwrapSandbox.run(
        command=["python3", "agent.py"],
        config=config,
        timeout=60,
    )

    # Or build the command line manually
    from agent_nook.sandbox.builder import BwrapBuilder
    cmd = BwrapBuilder(config).build(["python3", "agent.py"])
    ```
    """

    name: str
    chdir: str | None = None
    mounts: list[Mount] = field(default_factory=list)
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    unshare: NamespaceSet = field(default_factory=NamespaceSet)
    die_with_parent: bool = True
    new_session: bool = True
    hostname: str | None = None
    timeout: int | float | None = None
    env_vars: dict[str, str] = field(default_factory=dict)
    unset_vars: str = ""  # Comma-separated list of variable names to unset
    _raw_config: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Enforce dataclass invariants on construction (Layer 5 of the pipeline).

        This is the **last line of defense**: every SandboxConfig — whether
        loaded from a file, parsed from a dict with CLI overrides, or
        constructed programmatically — passes through here before use.

        Raises:
            ConfigValidationError: If the config defines no mount points.
                A sandbox with zero mounts has a completely empty root and
                bwrap fails at runtime with a cryptic execvp error.
        """
        if not self.mounts:
            raise ConfigValidationError(
                "at least one mount point is required: the sandbox root would be empty "
                "(add a mount to the config file or pass --bind SRC:DEST)"
            )

    def validate(self) -> None:
        """Run full validation.

        Note: Structural validation (field types, required fields) is performed
        by ConfigLoader._validate_structure() during load. This method only
        performs runtime checks that cannot be expressed in the canonical schema:
          - unshare.network implies unshare.uts and unshare.ipc
        """
        if self.unshare.network:
            if not self.unshare.uts:
                self.unshare.uts = True
            if not self.unshare.ipc:
                self.unshare.ipc = True


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""


__all__ = [
    "CapabilitySet",
    "ConfigValidationError",
    "Mount",
    "NamespaceSet",
    "SandboxConfig",
]
