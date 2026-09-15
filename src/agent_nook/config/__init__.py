"""Configuration module for Agent Nook.

Provides XDG-compliant path resolution and configuration management.

XDG directories:
    ~/.config/agent-nook/        (configuration)
    ~/.local/state/agent-nook/   (state, logs)
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agent_nook.config.loader import ConfigLoader
from agent_nook.config.loader import ConfigValidationError
from agent_nook.config.loader import get_config_path


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


__all__ = [
    "ConfigLoader",
    "ConfigValidationError",
    "copy_bundled_config",
    "get_config_path",
]
