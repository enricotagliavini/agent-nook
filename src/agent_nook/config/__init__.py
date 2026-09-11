"""Configuration module for Agent Nook sandbox.

Exports the core dataclasses and utilities for defining sandbox
configuration via YAML.
"""

from .config import (
    SandboxConfig,
    Mount,
    CapabilitySet,
    NamespaceSet,
    ConfigValidationError,
    get_config,
    set_config,
    reset_config,
    nook_config,
)
from .loader import ConfigLoader, ConfigValidationError as ConfigValidationError2

__all__ = [
    "SandboxConfig",
    "Mount",
    "CapabilitySet",
    "NamespaceSet",
    "ConfigValidationError",
    "get_config",
    "set_config",
    "reset_config",
    "nook_config",
    "ConfigLoader",
    "ConfigValidationError2",
]
