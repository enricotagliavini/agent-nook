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
# Config exports
from agent_nook.config.loader import ConfigLoader
from agent_nook.config.loader import ConfigValidationError
from agent_nook.sandbox import BwrapBuilder
from agent_nook.sandbox import BwrapSandbox
from agent_nook.sandbox import SandboxConfig
from agent_nook.sandbox import SandboxResult


__all__ = [
    "BwrapBuilder",
    "BwrapSandbox",
    "ConfigLoader",
    "ConfigValidationError",
    "SandboxConfig",
    "SandboxResult",
]
