"""Configuration module for Agent Nook.

Provides XDG-compliant path resolution and configuration management.

XDG directories:
    ~/.config/agent-nook/        (configuration)
    ~/.local/state/agent-nook/   (state, logs)
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from agent_nook.config.loader import ConfigLoader
from agent_nook.config.loader import ConfigValidationError


def copy_bundled_config(source_path: str | Path) -> None:
    """Copy a bundled configuration file to the user's config directory.

    Args:
        source_path: Path to the bundled configuration file.

    Example:
        >>> copy_bundled_config("/path/to/bundled/sandbox.yaml")
    """
    target_path = Path(source_path)
    dest_path = Path(get_config_path())
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target_path, dest_path)


def get_config_path() -> str:
    """Resolve the config path using XDG_CONFIG_HOME.

    Reads XDG_CONFIG_HOME from the environment, falls back to
    ~/.config if not set.

    Returns:
        The full path to the config file (agent-nook/sandbox.yaml).

    Example:
        >>> get_config_path()
        '/home/username/.config/agent-nook/sandbox.yaml'
    """
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg_config, "agent-nook", "sandbox.yaml")


__all__ = [
    "ConfigLoader",
    "ConfigValidationError",
    "copy_bundled_config",
    "get_config_path",
]
