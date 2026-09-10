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
        TmpfsMount,
        CapabilitySet,
        NamespaceSet,
    )
except ImportError:
    from ..config.config import (
        SandboxConfig,
        Mount,
        TmpfsMount,
        CapabilitySet,
        NamespaceSet,
    )


logger = logging.getLogger(__name__)


# Re-export from config module
from .config import ConfigValidationError


class ConfigLoader:
    """Loads and validates sandbox configuration from YAML files.

    The config file should be a flat structure at root level:
        name: "my-sandbox"
        root: "/tmp"
        mounts:
          - source: /host/path
            target: /sandbox/path
        tmpfs_mounts:
          - target: /tmp
            size: 100M
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
    """

    def __init__(self, config_dir: str | None = None) -> None:
        self._config_dir = config_dir
        self._logger = logging.getLogger(__name__)
        self._raw_config: dict[str, Any] | None = None
        self._config: SandboxConfig | None = None

    @property
    def config_dir(self) -> str:
        """Get the config directory from XDG_CONFIG_HOME."""
        if self._config_dir is not None:
            return self._config_dir

        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        self._config_dir = os.path.join(xdg_config, "agent-nook")
        return self._config_dir

    @property
    def state_dir(self) -> str:
        """Get the state directory from XDG_STATE_HOME."""
        xdg_state = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
        return os.path.join(xdg_state, "agent-nook")

    @property
    def sandbox_config_path(self) -> str:
        """Path to the sandbox configuration file."""
        return os.path.join(self.config_dir, "sandbox.yaml")

    def ensure_config_directory(self) -> None:
        """Create the config directory if it doesn't exist."""
        os.makedirs(self.config_dir, exist_ok=True)

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
        # Priority 1: Installed package location
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

        # Step 2: Convert raw YAML dict to dataclasses
        converted = self._yaml_to_dataclasses(raw)

        # Step 3: Apply overrides
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
        """
        self._raw_config = data
        converted = self._yaml_to_dataclasses(data)
        if override := data.get("override"):
            converted = self._merge(converted, override)
        self._config = converted
        return converted

    def _yaml_to_dataclasses(self, data: dict[str, Any]) -> SandboxConfig:
        """Convert raw YAML dict to SandboxConfig dataclass.

        Handles:
        - mounts: flat dict format or list of dicts
        - tmpfs_mounts: flat dict format or list of dicts
        - capabilities: flat dict or lists
        - unshare: flat list of namespace names
        """
        # Extract fields
        name: str = data.get("name", "default-sandbox")
        root: str = data.get("root", "./sandbox")
        hostname: str | None = data.get("hostname")

        # mounts
        mounts: list[Mount] = self._convert_mounts(data.get("mounts", []))

        # tmpfs_mounts
        tmpfs_mounts: list[TmpfsMount] = self._convert_tmpfs_mounts(
            data.get("tmpfs_mounts", [])
        )

        # capabilities
        capabilities: CapabilitySet = self._convert_capabilities(
            data.get("capabilities", {})
        )

        # unshare
        unshare: NamespaceSet = self._convert_unshare(data.get("unshare", []))

        # Boolean flags
        die_with_parent = data.get("die_with_parent", True)
        new_session = data.get("new_session", True)
        proc_enabled = data.get("proc_enabled", True)
        dev_enabled = data.get("dev_enabled", True)

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
            tmpfs_mounts=tmpfs_mounts,
            capabilities=capabilities,
            unshare=unshare,
            die_with_parent=die_with_parent,
            new_session=new_session,
            hostname=hostname,
            env_vars=env_vars,
            unenv_vars=unenv_vars,
            proc_enabled=proc_enabled,
            dev_enabled=dev_enabled,
        )

    def _convert_mounts(self, mounts: list | dict) -> list[Mount]:
        """Convert mounts to list[Mount].

        Handles:
        - List of dicts: [{"source": "...", "target": "..."}]
        - Dict: {"/host/path": "/sandbox/path"}
        """
        if isinstance(mounts, list):
            result: list[Mount] = []
            for i, m in enumerate(mounts):
                if isinstance(m, dict):
                    result.append(
                        Mount(
                            source=m.get("source", ""),
                            target=m.get("target", ""),
                            readonly=m.get("readonly", False),
                            device=m.get("device", False),
                        )
                    )
                elif isinstance(m, str):
                    # Flat format: source:target
                    if ":" in m:
                        parts = m.split(":", 1)
                        result.append(Mount(source=parts[0], target=parts[1]))
                    else:
                        result.append(Mount(source=m, target=m))
            return result

        if isinstance(mounts, dict):
            result: list[Mount] = []
            for source, target in mounts.items():
                if isinstance(target, str):
                    result.append(Mount(source=source, target=target))
            return result

        return []

    def _convert_tmpfs_mounts(self, tmpfs: list | dict) -> list[TmpfsMount]:
        """Convert tmpfs_mounts to list[TmpfsMount].

        Handles:
        - List of dicts: [{"target": "...", "size": "100M"}]
        - Dict: {"/tmp": "100M"}
        """
        if isinstance(tmpfs, list):
            result: list[TmpfsMount] = []
            for i, t in enumerate(tmpfs):
                if isinstance(t, dict):
                    result.append(
                        TmpfsMount(
                            target=t.get("target", ""),
                            size=t.get("size", ""),
                            device=t.get("device", False),
                        )
                    )
                elif isinstance(t, str):
                    result.append(TmpfsMount(target=t, size=""))
            return result

        if isinstance(tmpfs, dict):
            result: list[TmpfsMount] = []
            for target, size in tmpfs.items():
                result.append(
                    TmpfsMount(target=target, size=str(size) if size else "")
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

        return CapabilitySet(dropped=dropped, kept=kept)

    def _convert_unshare(self, unshare: list) -> NamespaceSet:
        """Convert unshare to NamespaceSet.

        Handles:
        - List: ["pid", "uts", "network"]
        - Dict: {"pid": True, "uts": False}
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
            pid=ns_dict.get("pid", True),
            uts=ns_dict.get("uts", True),
            ipc=ns_dict.get("ipc", True),
            cgroup=ns_dict.get("cgroup", True),
            user=ns_dict.get("user", True),
            network=ns_dict.get("network", False),
        )

    def _merge(self, base: SandboxConfig, override: dict[str, Any]) -> SandboxConfig:
        """Merge override into base config.

        Only handles top-level fields that are dicts or lists.
        """
        new_mounts = base.mounts
        if "mounts" in override:
            new_mounts = base.mounts + self._convert_mounts(override["mounts"])

        new_tmpfs = base.tmpfs_mounts
        if "tmpfs_mounts" in override:
            new_tmpfs = base.tmpfs_mounts + self._convert_tmpfs_mounts(
                override["tmpfs_mounts"]
            )

        return SandboxConfig(
            name=base.name,
            root=base.root,
            mounts=new_mounts,
            tmpfs_mounts=new_tmpfs,
            capabilities=base.capabilities,
            unshare=base.unshare,
            die_with_parent=base.die_with_parent,
            new_session=base.new_session,
            hostname=base.hostname,
            env_vars=base.env_vars | override.get("env_vars", {}),
            unenv_vars=base.unenv_vars + override.get("unenv_vars", []),
            proc_enabled=base.proc_enabled,
            dev_enabled=base.dev_enabled,
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
        self.ensure_config_directory()

        default_path = self.find_default_config()
        target_path = self.sandbox_config_path

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
