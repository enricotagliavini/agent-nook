"""Sandbox module — unified executor and builder.

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

from __future__ import annotations

from agent_nook.sandbox.bwrap_sandbox import (
    BwrapError,
    BwrapSandbox,
    SandboxExecutionError,
    SandboxResult,
)
from agent_nook.sandbox.builder import BwrapBuilder
from agent_nook.config.config import SandboxConfig

__all__ = [
    "BwrapError",
    "BwrapSandbox",
    "BwrapBuilder",
    "SandboxConfig",
    "SandboxResult",
    "SandboxExecutionError",
]
