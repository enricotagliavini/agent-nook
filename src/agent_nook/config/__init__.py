"""Configuration module for Agent Nook.

Provides XDG-compliant path resolution and configuration management.

XDG directories:
    ~/.config/agent-nook/        (configuration)
    ~/.local/state/agent-nook/   (state, logs, cache)
"""

from __future__ import annotations

import os

from agent_nook.config.loader import ConfigLoader, ConfigValidationError
from agent_nook.utils.logger import get_log_directory, get_log_file_path


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
    "get_config_path",
    "get_log_directory",
    "get_log_file_path",
]
