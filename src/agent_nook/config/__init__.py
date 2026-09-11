"""Configuration module for Agent Nook.

Provides XDG-compliant path resolution and configuration management.

XDG directories:
    ~/.config/agent-nook/        (configuration)
    ~/.local/state/agent-nook/   (state, logs, cache)
"""

from __future__ import annotations

import os

from agent_nook.config.config import SandboxConfig, set_config, nook_config, reset_config
from agent_nook.config.loader import ConfigLoader, ConfigValidationError

# Lazy import to avoid circular dependency
_get_state_dir: Callable[[], str]


def _get_state_dir() -> str:
    """Get the state directory path from XDG_STATE_HOME."""
    xdg_state = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    return os.path.join(xdg_state, "agent-nook")


def get_config_dir() -> str:
    """Resolve the config directory path using XDG_CONFIG_HOME.

    Reads XDG_CONFIG_HOME from the environment, falls back to
    ~/.config if not set.

    Returns:
        The full path to the config directory.

    Example:
        >>> get_config_dir()
        '/home/username/.config/agent-nook'
    """
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg_config, "agent-nook")


def get_state_dir() -> str:
    """Resolve the state directory path using XDG_STATE_HOME.

    Reads XDG_STATE_HOME from the environment, falls back to
    ~/.local/state if not set.

    Returns:
        The full path to the state directory.

    Example:
        >>> get_state_dir()
        '/home/username/.local/state/agent-nook'
    """
    xdg_state = os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state"))
    return os.path.join(xdg_state, "agent-nook")


__all__ = [
    "get_config_dir",
    "get_state_dir",
    "ConfigLoader",
    "ConfigValidationError",
    "nook_config",
    "set_config",
    "reset_config",
]
