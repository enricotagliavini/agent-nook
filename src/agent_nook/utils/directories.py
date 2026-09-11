"""Directory management utilities for Agent Nook.

All directory creation logic is centralized here. This follows the
principle that the ConfigLoader should only handle configuration
loading/validation, not filesystem operations.
"""

from __future__ import annotations

import os
import logging
from typing import Sequence

logger = logging.getLogger(__name__)


def ensure_directories(paths: Sequence[str]) -> None:
    """Create all given directories if they don't exist.

    Args:
        paths: List of directory paths to create.

    Example:
        >>> ensure_directories(["/tmp/foo", "/tmp/bar"])
    """
    for path in paths:
        os.makedirs(path, exist_ok=True)


__all__ = ["ensure_directories"]
