"""Directory management utilities for Agent Nook.

All directory creation logic is centralized here. This follows the
principle that the ConfigLoader should only handle configuration
loading/validation, not filesystem operations.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from agent_nook.utils.logger import main_logger


_logger = main_logger(__name__)


def ensure_directories(paths: Sequence[str]) -> None:
    """Create all given directories if they don't exist.

    Args:
        paths: List of directory paths to create.

    Example:
        >>> ensure_directories(["/tmp/foo", "/tmp/bar"])
    """
    for path in paths:
        _logger.debug("Creating directory: %s", path)
        os.makedirs(path, exist_ok=True)


__all__ = ["ensure_directories"]
