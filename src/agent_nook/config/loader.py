"""XDG-compliant configuration loader.

Reads configuration from user's XDG directories:
- ~/.config/agent-nook/sandbox.yaml   (main config)
- ~/.config/agent-nook/logging.yaml   (logging config)
- ~/.local/state/agent-nook/logs/     (log output)
- ~/.local/state/agent-nook/cache/    (temporary data)

On first run, copies the bundled default config to ~/.config/agent-nook/
if it does not already exist.
"""

from __future__ import annotations

import os
import shutil
import logging
from pathlib import Path
from typing import Any

import yaml

try:
    from agent_nook.config.config import (
        SandboxConfig,
        Mount,
        CapabilitySet,
        NamespaceSet,
    )
except ImportError:
    from ..config.config import (
        SandboxConfig,
        Mount,
        CapabilitySet,
        NamespaceSet,
    )

from .config import ConfigValidationError

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Loads and validates sandbox configuration from YAML files.

    The config file should be a flat structure at root level:
        name: "my-sandbox"
        root: "/tmp"
        mounts:
          - target: /proc
            type: proc
          - target: "/home"
            type: tmpfs
            size: "100M"
        capabilities:
          drop: ALL
          keep: CHOWN
        unshare:
          - pid
          - uts
          - ipc
          - cgroup
          - user
        die_with_parent: true
        new_session: true

    All mount types are unified under the `mounts` list. There is no
    separate `tmpfs_mounts` section — use `type: tmpfs` instead.
    """

    def __init__(self, config_dir: str | None = None) -> None:
        self._config_dir = config_dir
        self._logger = logging.getLogger(__name__)
        self._raw_config: dict[str, Any] | None = None
        self._config: SandboxConfig | None = None

    

    

    

    

    def find_default_config(self) -> str:
        """Find the bundled default configuration file.

        Looks in the following locations in order of preference:
        1. Installed package location: src/agent_nook/config/sandbox.yaml
        2. Repo root: config/sandbox.yaml (fallback for development)

        Returns:
            Path to the default config file.

        Raises:
            FileNotFoundError: If no default config is found.
        """
        import agent_nook
        package_dir = Path(agent_nook.__file__).parent
        default_config_path = package_dir / "config" / "sandbox.yaml"

        if default_config_path.exists():
            return str(default_config_path)

        # Priority 2: Repo root (fallback for development)
        loader_path = Path(__file__).resolve()
        repo_root = loader_path.parent.parent.parent.parent
        default_config_path = repo_root / "config" / "sandbox.yaml"

        if default_config_path.exists():
            return str(default_config_path)

        raise FileNotFoundError(
            f"Default sandbox.yaml not found at {default_config_path}. "
            f"Make sure agent-nook is properly installed or "
            f"the config directory exists with a sandbox.yaml file."
        )

    def load(
        self,
        path: str | None = None,
        override: dict[str, Any] | None = None,
    ) -> SandboxConfig:
        """Load and validate configuration from a YAML file.

        Args:
            path: Optional path override. If None, uses the default config path.
            override: Optional dictionary to override/merge with loaded config.

        Returns:
            A validated SandboxConfig ready for use.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ValueError: If the config structure is invalid.
        """
        if path is None:
            path = self.sandbox_config_path

        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")

        # Step 1: Parse YAML
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
            self._raw_config = raw

        # Step 2: Validate known keys
        known_keys = {"name", "root", "mounts", "capabilities", "unshare",
                      "die_with_parent", "new_session", "hostname", "timeout",
                      "env_vars", "unenv_vars"}
        for key in raw.keys():
            if key not in known_keys:
                raise ConfigValidationError(f"unknown key '{key}'")

        # Step 3: Convert raw YAML dict to dataclasses
        converted = self._yaml_to_dataclasses(raw)

        # Step 4: Apply overrides
        if override:
            converted = self._merge(converted, override)

        self._config = converted
        return converted

    def set(self, data: dict[str, Any]) -> SandboxConfig:
        """Set configuration from a raw dictionary.

        Args:
            data: Raw configuration dictionary.

        Returns:
            A validated SandboxConfig.

        Raises:
            ConfigValidationError: If required keys are missing or invalid.
        """
        self._raw_config = data

        known_keys = {"name", "root", "mounts", "capabilities", "unshare",
                      "die_with_parent", "new_session", "hostname", "timeout",
                      "env_vars", "unenv_vars"}
        for key in data.keys():
            if key not in known_keys:
                raise ConfigValidationError(f"unknown key '{key}'")

        # Validate required fields
        if "name" not in data or not data["name"].strip():
            raise ConfigValidationError("name cannot be empty")
        if "root" not in data or not data["root"].strip():
            raise ConfigValidationError("root cannot be empty")
        if "mounts" not in data:
            raise ConfigValidationError("mounts cannot be empty")

        converted = self._yaml_to_dataclasses(data)
        if override := data.get("override"):
            converted = self._merge(converted, override)
        self._config = converted
        return converted

    def _yaml_to_dataclasses(self, data: dict[str, Any]) -> SandboxConfig:
        """Convert raw YAML dict to SandboxConfig dataclass.

        Handles:
        - mounts: unified list with type field
          - List of dicts: [{"target": "...", "type": "tmpfs", "size": "100M"}]
          - Dict: {"/proc": {"type": "proc"}, "/host": {"type": "ro-bind", "target": "/sandbox"}}
        """
        # Extract fields
        name: str = data.get("name", "default-sandbox")
        root: str = data.get("root", "./sandbox")
        hostname: str | None = data.get("hostname")

        # Mounts (unified format)
        mounts: list[Mount] = self._convert_mounts(data.get("mounts", []))

        # capabilities
        capabilities: CapabilitySet = self._convert_capabilities(
            data.get("capabilities", {})
        )
        capabilities.validate()

        # unshare
        unshare: NamespaceSet = self._convert_unshare(data.get("unshare", []))

        # Boolean flags
        die_with_parent = data.get("die_with_parent", True)
        new_session = data.get("new_session", True)
        timeout = data.get("timeout", None)

        # Environment variables
        env_vars = data.get("env_vars", {})
        if isinstance(env_vars, dict):
            # Convert flat dict to list format if needed
            pass
        elif isinstance(env_vars, list):
            env_vars = dict(item.split("=", 1) for item in env_vars if "=" in item)

        unenv_vars: list[str] = data.get("unenv_vars", [])

        return SandboxConfig(
            name=name,
            root=root,
            mounts=mounts,
            capabilities=capabilities,
            unshare=unshare,
            die_with_parent=die_with_parent,
            new_session=new_session,
            hostname=hostname,
            timeout=timeout,
            env_vars=env_vars,
            unenv_vars=unenv_vars,
            _raw_config=data,
        )

    def _convert_mounts(self, mounts: list | dict) -> list[Mount]:
        """Convert mounts to list[Mount].

        Handles:
        - List of dicts: [{"target": "...", "type": "tmpfs", "size": "100M"}]
        - Dict: {"/host/path": {"target": "/sandbox/path", "type": "ro-bind"}}
        - Dict (flat format, no nested target): {"/host/path": "/sandbox/path"}
        """
        if isinstance(mounts, list):
            result: list[Mount] = []
            for i, m in enumerate(mounts):
                if isinstance(m, dict):
                    mount_type = m.get("type", "bind")
                    # Validate mount type
                    valid_types = {"bind", "ro-bind", "dev-bind", "tmpfs", "proc", "dev", "dir"}
                    if mount_type not in valid_types:
                        raise ValueError(
                            f"Mount type '{mount_type}' is not valid. "
                            f"Valid types: {', '.join(sorted(valid_types))}"
                        )
                    source = m.get("source", "")
                    target = m.get("target", "")
                    readonly = m.get("readonly", False)
                    device = m.get("device", False)
                    result.append(
                        Mount(
                            source=source,
                            target=target,
                            readonly=readonly,
                            device=device,
                            type=mount_type,
                            size=m.get("size", ""),
                        )
                    )
                elif isinstance(m, str):
                    # Flat format: source:target (deprecated, falls back to bind)
                    if ":" in m:
                        parts = m.split(":", 1)
                        result.append(Mount(source=parts[0], target=parts[1]))
                    else:
                        result.append(Mount(source=m, target=m))
            return result

        if isinstance(mounts, dict):
            result: list[Mount] = []
            for source, target_or_dict in mounts.items():
                if isinstance(target_or_dict, str):
                    result.append(Mount(source=source, target=target_or_dict))
                elif isinstance(target_or_dict, dict):
                    mount_type = target_or_dict.get("type", "bind")
                    valid_types = {"bind", "ro-bind", "dev-bind", "tmpfs", "proc", "dev", "dir"}
                    if mount_type not in valid_types:
                        raise ValueError(
                            f"Mount type '{mount_type}' is not valid. "
                            f"Valid types: {', '.join(sorted(valid_types))}"
                        )
                    result.append(
                        Mount(
                            source=source,
                            target=target_or_dict.get("target", source),
                            readonly=target_or_dict.get("readonly", False),
                            device=target_or_dict.get("device", False),
                            type=mount_type,
                            size=target_or_dict.get("size", ""),
                        )
                    )
            return result

        return []

    def _convert_capabilities(self, caps: dict) -> CapabilitySet:
        """Convert capabilities to CapabilitySet.

        Handles:
        - "drop": "ALL"
        - "keep": ["CHOWN", "SETUID"]
        - "dropped": ["CAP_NET_ADMIN"]
        - "kept": ["CAP_CHOWN"]
        """
        # Valid Linux capabilities (from /usr/include/linux/capability.h)
        # "ALL" is a special value meaning all capabilities
        valid_capabilities = frozenset({
            "ALL", "CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_DAC_READ_SEARCH",
            "CAP_FOWNER", "CAP_FSETID", "CAP_KILL", "CAP_SETGID",
            "CAP_SETUID", "CAP_SETPCAP", "CAP_LINUX_IMMUTABLE",
            "CAP_NET_BIND_SERVICE", "CAP_NET_BROADCAST", "CAP_NET_ADMIN",
            "CAP_NET_RAW", "CAP_IPC_LOCK", "CAP_IPC_OWNER", "CAP_SYS_MODULE",
            "CAP_SYS_RAWIO", "CAP_SYS_CHROOT", "CAP_SYS_PTRACE",
            "CAP_SYS_PACCT", "CAP_SYS_ADMIN", "CAP_SYS_BOOT", "CAP_SYS_NICE",
            "CAP_SYS_RESOURCE", "CAP_SYS_TIME", "CAP_SYS_TTY_CONFIG",
            "CAP_MKNOD", "CAP_LEASE", "CAP_AUDIT_WRITE", "CAP_AUDIT_READ",
            "CAP_AUDIT_CONTROL", "CAP_SETFCAP", "CAP_MAC_OVERRIDE",
            "CAP_MAC_ADMIN", "CAP_SYSLOG", "CAP_WAKE_ALARM",
            "CAP_BLOCK_SUSPEND", "CAP_AUDIT_READ", "CAP_PERFMON",
            "CAP_BPF", "CAP_CHECKPOINT_RESTORE",
        })
        dropped: list[str] = []
        kept: list[str] = []

        if isinstance(caps, dict):
            if "drop" in caps:
                val = caps["drop"]
                if isinstance(val, str):
                    dropped = [val]
                else:
                    dropped = list(val)
            if "keep" in caps:
                val = caps["keep"]
                if isinstance(val, str):
                    kept = [val]
                else:
                    kept = list(val)
            if "dropped" in caps:
                val = caps["dropped"]
                if isinstance(val, str):
                    dropped = [val]
                else:
                    dropped = list(val)
            if "kept" in caps:
                val = caps["kept"]
                if isinstance(val, str):
                    kept = [val]
                else:
                    kept = list(val)

        # Check for conflicting drop ALL + keep
        if "ALL" in dropped and kept:
            raise ConfigValidationError(
                "Cannot keep capabilities when dropping ALL"
            )

        # Normalize capability names (accept both "CHOWN" and "CAP_CHOWN")
        def _normalize_cap(name: str) -> str:
            if name.startswith("CAP_"):
                return name
            return "CAP_" + name

        normalized_dropped = [_normalize_cap(cap) for cap in dropped]
        normalized_kept = [_normalize_cap(cap) for cap in kept]

        # Validate capability names
        all_caps = normalized_dropped + normalized_kept
        invalid = [cap for cap in all_caps if cap not in valid_capabilities]
        if invalid:
            raise ConfigValidationError(
                f"Invalid capability{'' if len(invalid) == 1 else 's'}: "
                f"{', '.join(invalid)}.{'' if len(invalid) == 1 else ''} "
                f"Valid: {', '.join(sorted(valid_capabilities))}"
            )

        return CapabilitySet(dropped=dropped, kept=kept)

    def _convert_unshare(self, unshare: list) -> NamespaceSet:
        """Convert unshare to NamespaceSet.

        Handles:
        - List: ["pid", "uts", "network"]
        - Dict: {"pid": True, "uts": False}

        Uses opt-in semantics: only namespaces explicitly set to True are unshared.
        """
        if isinstance(unshare, list):
            ns_dict: dict[str, bool] = {}
            for ns in unshare:
                if isinstance(ns, str):
                    ns_dict[ns] = True
                else:
                    ns_dict[str(ns)] = True
        elif isinstance(unshare, dict):
            ns_dict = unshare
        else:
            ns_dict = {}

        return NamespaceSet(
            pid=ns_dict.get("pid", False),
            uts=ns_dict.get("uts", False),
            ipc=ns_dict.get("ipc", False),
            cgroup=ns_dict.get("cgroup", False),
            user=ns_dict.get("user", False),
            network=ns_dict.get("network", False),
        )

    def _merge(self, base: SandboxConfig, override: dict[str, Any]) -> SandboxConfig:
        """Merge override into base config.

        Handles:
          - dict/list fields: mounts, env_vars, unenv_vars, capabilities, unshare
          - scalar fields: hostname (uses override value if present)
        """
        new_mounts = base.mounts
        if "mounts" in override:
            new_mounts = base.mounts + self._convert_mounts(override["mounts"])

        new_hostname = base.hostname
        if "hostname" in override and override["hostname"] is not None:
            new_hostname = override["hostname"]

        return SandboxConfig(
            name=base.name,
            root=base.root,
            mounts=new_mounts,
            capabilities=base.capabilities,
            unshare=base.unshare,
            die_with_parent=base.die_with_parent,
            new_session=base.new_session,
            hostname=new_hostname,
            env_vars=base.env_vars | override.get("env_vars", {}),
            unenv_vars=base.unenv_vars + override.get("unenv_vars", []),
            _raw_config=base._raw_config,
        )

    @property
    def raw_config(self) -> dict[str, Any]:
        """Get the raw (unvalidated) config from the YAML file."""
        if self._raw_config is None:
            raise ValueError("No config loaded. Call load() first.")
        return self._raw_config

    @property
    def config(self) -> SandboxConfig:
        """Get the normalized, validated config."""
        if self._config is None:
            raise ValueError("No config loaded. Call load() first.")
        return self._config

    def install_default_config(self) -> str:
        """Copy the default config to the user's config directory if not present.

        Returns:
            The path to the installed config file.
        """
        import agent_nook
        package_dir = Path(agent_nook.__file__).parent
        default_config_path = package_dir / "config" / "sandbox.yaml"

        default_path = self.find_default_config()
        target_path = str(default_config_path)
        if not os.path.exists(default_path):
            raise FileNotFoundError(
                f"Default config not found at {default_path}. "
                f"Install agent-nook with: pip install -e ."
            )

        if not os.path.exists(target_path):
            self._logger.info(
                "No sandbox config found at %s. Copying default config.",
                target_path,
            )
            shutil.copy2(default_path, target_path)
            self._logger.info(
                "Default sandbox config installed to: %s",
                target_path,
            )
            return target_path

        self._logger.info("Using existing config: %s", target_path)
        return target_path


__all__ = ["ConfigLoader", "ConfigValidationError"]
