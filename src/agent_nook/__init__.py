# Agent Nook
"""A lightweight bwrap sandbox for AI agents.

Usage:
    from agent_nook.sandbox import BwrapSandbox, BwrapBuilder, SandboxConfig

    # Preferred: use BwrapSandbox.run() which handles execution
    result = BwrapSandbox.run(
        command=["python3", "agent.py"],
        config=config,
        timeout=60,
    )

    # Manual builder
    cmd = BwrapBuilder(config).build(["python3", "agent.py"])
"""

__version__ = "0.1.0"

# Unified sandbox exports
from agent_nook.sandbox import (
    BwrapSandbox,
    BwrapBuilder,
    BwrapError,
    SandboxConfig,
    SandboxResult,
    SandboxExecutionError,
)

# Config exports
from agent_nook.config.loader import ConfigLoader, ConfigValidationError

__all__ = [
    "BwrapSandbox",
    "BwrapBuilder",
    "BwrapError",
    "SandboxConfig",
    "SandboxResult",
    "SandboxExecutionError",
    "ConfigLoader",
    "ConfigValidationError",
]
