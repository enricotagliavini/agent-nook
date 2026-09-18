"""Sandbox builder for bubblewrap commands.

Provides a dataclass-driven API for building bwrap command lines.
Accepts ONLY SandboxConfig dataclass — no dict normalization.

## Validation Pipeline

The BwrapBuilder sits at **Layer 6** of the validation pipeline. By design,
it performs **NO** validation — it assumes the config has already passed
through all previous layers:

```
Layer 1: ConfigLoader._validate_structure()      → schema check
Layer 2: ConfigLoader._parse_mounts()            → mount type/fields check
Layer 3: ConfigLoader._parse_capabilities()      → cap list-of-strings check
Layer 4: ConfigLoader._parse_unshare()           → ns key/type check
Layer 5: SandboxConfig.__post_init__() → Mount.build() validation gate
Layer 6: BwrapBuilder.build()                    → uses validated config
```

The builder is a **pure consumer** — it transforms validated data into
a bwrap command line. It does not repeat validation because:
  - The config is guaranteed valid by Layer 5 (__post_init__).
  - Re-validating here would be redundant and error-prone.
  - If an error occurs, the config has already failed validation upstream.

## When errors occur in the builder

If BwrapBuilder encounters invalid data, it is a bug in the code path
that produced the config (e.g., using a raw dict instead of a
ConfigLoader-loaded SandboxConfig). The builder will raise an error
with a clear message pointing to the root cause.
"""

from __future__ import annotations

from collections import defaultdict
from collections import deque
from dataclasses import dataclass

from agent_nook.config.config import SandboxConfig


# Re-export BwrapError from runner for CLI compatibility
try:
    from agent_nook.runner import BwrapError
except ImportError:
    BwrapError = RuntimeError


def build_mounts_order(config: SandboxConfig) -> list[list[str]]:
    """Build a topologically-sorted list of mount arguments for the sandbox.

    All mount types are considered together. The result guarantees that
    for any two mounts where one is a parent directory of the other,
    the parent's mount arguments appear before the child's mount arguments.

    When multiple mounts target the same path (conflicting mount types),
    their arguments are combined in the order mounts appear in the config.

    This is critical because bwrap applies mount arguments in order of
    appearance on the command line. Without proper ordering, bwrap fails
    with "no such file or directory" errors when a child mount point is
    created before its parent.

    Example:
        Config with:
          Mount(type="tmpfs", target="/home/user/git")
          Mount(type="ro-bind", source="/host/home", target="/home")
        Produces:
          ["--ro-bind", "/host/home", "/home", "--tmpfs", "/home/user/git"]

    Args:
        config: The SandboxConfig containing mounts.

    Returns:
        A list of lists, where each inner list contains the bwrap arguments
        for a single mount point. The outer list is ordered so that parents
        appear before children.

    Raises:
        RuntimeError: If the topological sort detects a cycle or
            unreachable node (should not happen with valid configs).
    """
    # First pass: collect all mount points and their arguments.
    # When multiple mounts target the same path, combine their arguments.
    mount_args: dict[str, list[str]] = {}

    for mount in config.mounts:
        target = mount.target or ""
        if target in mount_args:
            # Multiple mounts target the same path — combine their args
            mount_args[target].extend(mount.build())
        else:
            mount_args[target] = mount.build()

    # Second pass: build dependency graph using pre-computed mount_points
    mount_points: set[str] = set(mount_args.keys())

    children_of: dict[str, list[str]] = defaultdict(list)
    for mount in config.mounts:
        target = mount.target or ""
        # Find all ancestor directories of this mount point.
        # For target "/home/user/git", ancestors are "/home/user" and "/home".
        # Only those ancestors that are ALSO mount points are included.
        path_parts = target.replace("\\", "/").split("/")
        for i in range(1, len(path_parts)):
            parent = "/".join(path_parts[:i])
            if parent and not parent.startswith("."):
                if parent in mount_points:
                    children_of[parent].append(target)

    # Topological sort: parents before children (Kahn's algorithm)
    in_degree: dict[str, int] = dict.fromkeys(mount_points, 0)
    for _parent, children in children_of.items():
        for child in children:
            in_degree[child] += 1

    queue: deque[str] = deque()
    for tp in sorted(mount_points):
        if in_degree[tp] == 0:
            queue.append(tp)

    sorted_mounts: list[str] = []
    while queue:
        current = queue.popleft()
        sorted_mounts.append(current)
        for child in sorted(children_of[current]):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if len(sorted_mounts) != len(mount_points):
        unreachable = sorted(mount_points - set(sorted_mounts))
        raise RuntimeError(
            f"Topological sort failed: {len(unreachable)} unreachable mount point(s): "
            f"{unreachable}. This indicates a cycle or conflicting mount "
            f"configuration. Each mount point must be a root of the dependency "
            f"DAG or have a reachable parent chain from a root."
        )

    # Build the final ordered argument list
    result: list[list[str]] = []
    for target in sorted_mounts:
        result.append(mount_args[target])

    return result


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

    Mount ordering is handled automatically via `build_mounts_order()`,
    which ensures parent directories are always mounted before their
    children, regardless of mount type.
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
                f"Expected SandboxConfig dataclass, got {type(config).__name__}. Use ConfigLoader.load() to obtain a SandboxConfig."
            )
        self._config = config

    @property
    def config(self) -> SandboxConfig:
        """Return the config as-is (no validation, no transformation)."""
        return self._config

    def build(self, command: list[str]) -> list[str]:
        """Build the complete bwrap command line.

        Mounts are ordered via `build_mounts_order()` to ensure that
        parent directories are always mounted before their children,
        regardless of mount type. This prevents bwrap failures due to
        attempting to mount a path before its parent exists.

        Args:
            command: The command and arguments to run inside the sandbox.

        Returns:
            A list that can be passed directly to subprocess.run().
        """
        args: list[str] = ["bwrap", "--die-with-parent", "--new-session"]

        # Get mounts in topologically sorted order (parents before children)
        mount_args_list = build_mounts_order(self._config)

        for mount_args in mount_args_list:
            args.extend(mount_args)

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

        # Unset environment variables
        # unset_vars is a whitespace-separated string: "VAR1 VAR2" or "ALL"
        unset_vars_str = self._config.unset_vars
        if unset_vars_str:
            # Split on any whitespace (handles newlines, spaces, tabs)
            var_names = [v for v in unset_vars_str.split() if v]
            for var_name in var_names:
                if var_name.upper() == "ALL":
                    args.append("--clearenv")
                else:
                    args.extend(["--unsetenv", var_name])

        # Set environment variables
        for key, value in self._config.env_vars.items():
            args.extend(["--setenv", key, str(value)])

        # Chdir
        if self._config.chdir:
            args.extend(["--chdir", self._config.chdir])

        # Add the actual command
        args.extend(command)

        return args


__all__ = ["BwrapBuilder", "BwrapError", "SandboxConfig"]
