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
    from agent_nook.sandbox import builder
except ImportError:
    from ..sandbox import builder


class ConfigLoader:
    """Loads and validates sandbox configuration from YAML files.

    Follows XDG Base Directory Specification for all paths.
    """

    def __init__(self, config_dir: str | None = None) -> None:
        self._config_dir = config_dir
        self._logger = logging.getLogger(__name__)

    @property
    def config_dir(self) -> str:
        """Get the config directory from XDG_CONFIG_HOME.

        Returns ~/.config/agent-nook (or ~/.config/agent-nook).
        """
        if self._config_dir is not None:
            return self._config_dir

        xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        self._config_dir = os.path.join(xdg_config, "agent-nook")
        return self._config_dir

    @property
    def data_dir(self) -> str:
        """Get the data directory from XDG_DATA_HOME."""
        xdg_data = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        return os.path.join(xdg_data, "agent-nook")

    @property
    def state_dir(self) -> str:
        """Get the state directory from XDG_STATE_HOME."""
        xdg_state = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
        return os.path.join(xdg_state, "agent-nook")

    @property
    def sandbox_config_path(self) -> str:
        """Path to the sandbox configuration file."""
        return os.path.join(self.config_dir, "sandbox.yaml")

    @property
    def logging_config_path(self) -> str:
        """Path to the logging configuration file."""
        return os.path.join(self.config_dir, "logging.yaml")

    def find_default_config(self) -> str:
        """Find the bundled default configuration file.

        Looks in the following locations in order of preference:
        1. Installed package location: src/agent_nook/config/sandbox.yaml
           (works for both pip install and development)
        2. Repo root: config/sandbox.yaml (fallback for development)

        Returns:
            Path to the default config file.

        Raises:
            FileNotFoundError: If no default config is found.
        """
        import tempfile

        # Priority 1: Installed package location
        # src/agent_nook/config/sandbox.yaml works for both pip install and dev
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

        # As last resort, try a temp location (for testing)
        temp_config = Path(tempfile.gettempdir()) / "agent-nook" / "config" / "sandbox.yaml"
        if temp_config.exists():
            return str(temp_config)

        # Give up
        raise FileNotFoundError(
            "Default sandbox.yaml not found. "
            "Make sure agent-nook is properly installed or "
            "the config directory exists with a sandbox.yaml file."
        )

    def ensure_config_directory(self) -> None:
        """Create the config directory if it doesn't exist."""
        os.makedirs(self.config_dir, exist_ok=True)

    def ensure_state_directory(self) -> None:
        """Create the state directory if it doesn't exist."""
        os.makedirs(self.state_dir, exist_ok=True)

    def ensure_data_directory(self) -> None:
        """Create the data directory if it doesn't exist."""
        os.makedirs(self.data_dir, exist_ok=True)

    def ensure_log_directory(self) -> None:
        """Create the logs subdirectory if it doesn't exist."""
        logs_dir = os.path.join(self.state_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)

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
            return str(target_path)

        self._logger.info("Using existing config: %s", target_path)
        return str(target_path)

    def load(self, path: str | None = None, override: dict[str, Any] | None = None) -> dict[str, Any]:
        """Load configuration from a YAML file.

        Args:
            path: Optional path override. If None, uses the default config path.
            override: Optional dictionary to override/merge with loaded config.

        Returns:
            Parsed configuration dictionary compatible with SandboxConfig.

        Note: The loader supports both nested and flat config formats.
              The nested format ({"sandbox": {...}}) is preferred and returns
              the inner dict directly, with 'env' renamed to 'env_vars' for
              compatibility with the dataclass.
        """
        if path is None:
            path = self.sandbox_config_path

        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if override:
            data = self._merge_data(data, override)

        # Support both nested and flat config formats
        # Prefer nested format for future extensibility
        if "sandbox" in data and isinstance(data["sandbox"], dict):
            sandbox_cfg = data["sandbox"]
            # Normalize 'env' key to 'env_vars' for dataclass compatibility
            # Keep it even if empty, so downstream code doesn't have to check for existence
            if "env" in sandbox_cfg and not isinstance(sandbox_cfg["env"], list):
                sandbox_cfg["env_vars"] = sandbox_cfg["env"]
            else:
                sandbox_cfg["env_vars"] = {}
            return sandbox_cfg
        return data

    @staticmethod
    def _merge_data(base: dict, override: dict) -> dict:
        """Deep merge override into base dictionary."""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigLoader._merge_data(result[key], value)
            else:
                result[key] = value
        return result

    def _to_tmpfs_mounts(self, mounts_list: list[dict] | None) -> list[dict]:
        """Convert a list of tmpfs mount dicts to builder-compatible format.

        Each dict in the list should have:
          - 'target' (required): the target path inside the sandbox
          - 'size' (optional, default ""): the maximum size
          - 'device' (optional, default False): if True, use --tmpfs-override

        Args:
            mounts_list: List of dicts from the config file, or None for defaults.

        Returns:
            List of dicts compatible with BwrapBuilder.
        """
        if mounts_list is None:
            return []
        return list(mounts_list)

    def to_sandbox_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Convert a loaded config dict to a format compatible with BwrapBuilder.

        Args:
            config: The raw config dict from yaml.safe_load().

        Returns:
            A config dict ready to be passed to BwrapBuilder.
        """
        result = config.copy()

        # Convert mounts list of dicts to proper format
        if "mounts" in result:
            mounts = result["mounts"]
            if isinstance(mounts, list):
                result["mounts"] = [
                    self._mount_to_dict(m) for m in mounts
                ]

        # Convert tmpfs_mounts list of dicts to proper format
        if "tmpfs_mounts" in result:
            tmpfs = result["tmpfs_mounts"]
            if isinstance(tmpfs, list):
                result["tmpfs_mounts"] = [
                    self._mount_to_dict(m) for m in tmpfs
                ]

        # Normalize capabilities: {drop: [...], keep: [...]} → {dropped: [...], kept: [...]}
        if "capabilities" in result:
            caps = result["capabilities"]
            if isinstance(caps, dict):
                # Convert flat format {"drop": [...], "keep": [...]} to builder format
                if "drop" in caps and not isinstance(caps["drop"], list):
                    result["capabilities"]["dropped"] = caps["drop"]
                else:
                    result["capabilities"]["dropped"] = caps.get("drop", [])

                if "keep" in caps and not isinstance(caps["keep"], list):
                    result["capabilities"]["kept"] = caps["keep"]
                else:
                    result["capabilities"]["kept"] = caps.get("keep", [])

        # Handle env_vars (may be None if not present in config)
        if "env_vars" in result and result["env_vars"] is None:
            result["env_vars"] = {}

        return result

    @staticmethod
    def _mount_to_dict(mount: dict | Any) -> dict[str, Any]:
        """Normalize a mount specification to a standard dict format.

        Handles both dict and nested-key formats.
        For tmpfs_mounts: expects keys "target" (required), "size", "device".
        For bind mounts: expects keys "source", "target", "readonly", "device".
        """
        if isinstance(mount, dict):
            d = mount.copy()
            original = mount

            # Handle tmpfs_mounts format
            if "target" in mount:
                # tmpfs_mounts: {"target": "...", "size": "...", "device": bool}
                d["readonly"] = d.get("readonly", False)
                d["device"] = d.get("device", False)
                if "size" not in d:
                    d["size"] = ""
                return d
            elif "source" in mount:
                # bind mounts: {"source": "...", "target": "...", "readonly": bool, "device": bool}
                d["readonly"] = d.get("readonly", False)
                d["device"] = d.get("device", False)
                return d
            else:
                # Unknown mount format — return original to avoid data loss
                return original
        return original if isinstance(mount, dict) else mount

    def get_config(self, override: dict[str, Any] | None = None) -> dict[str, Any]:
        """Load and return the sandbox configuration.

        First checks if default config should be installed,
        then loads the user's config (if present) and converts it
        to a format compatible with BwrapBuilder.

        Returns:
            A config dict ready to be passed to BwrapBuilder.
        """
        self.ensure_config_directory()
        self.ensure_state_directory()
        self.ensure_log_directory()

        installed_path = self.install_default_config()
        config = self.load(installed_path, override=override)

        # Convert to builder-compatible format
        return self.to_sandbox_config(config)
