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

from dataclasses import dataclass

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

        # Timeout
        if self._config.timeout is not None:
            args.extend(["--timeout", str(self._config.timeout)])

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


__all__ = ["BwrapBuilder", "BwrapError", "SandboxConfig"]
