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
        return os.path.join(xdg_config, "agent-nook")

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
            if "env" in sandbox_cfg and not isinstance(sandbox_cfg["env"], list):
                sandbox_cfg["env_vars"] = sandbox_cfg["env"]
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

    def get_config(self, override: dict[str, Any] | None = None) -> dict[str, Any]:
        """Load and return the sandbox configuration.

        First checks if default config should be installed,
        then loads the user's config (if present).
        """
        self.ensure_config_directory()
        self.ensure_state_directory()
        self.ensure_log_directory()

        installed_path = self.install_default_config()

        return self.load(installed_path, override=override)
