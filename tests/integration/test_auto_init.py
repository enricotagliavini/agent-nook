"""Integration tests for automatic initialization on first run.

Tests that the auto-init feature correctly creates:
1. Config directory and file
2. Log directory
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def temp_xdg_dirs(tmp_path: Path) -> tuple[str, str, Path, Path]:
    """Create temporary XDG directories for testing."""
    temp_config_dir = tmp_path / "config"
    temp_state_dir = tmp_path / "state"
    temp_config_dir.mkdir()
    temp_state_dir.mkdir()
    # Create the agent-nook subdirectory for config (XDG-compliant)
    (temp_config_dir / "agent-nook").mkdir()

    # Set XDG environment variables
    old_xdg_config = os.environ.get("XDG_CONFIG_HOME")
    old_xdg_state = os.environ.get("XDG_STATE_HOME")

    os.environ["XDG_CONFIG_HOME"] = str(temp_config_dir)
    os.environ["XDG_STATE_HOME"] = str(temp_state_dir)

    yield str(temp_config_dir), str(temp_state_dir), temp_config_dir, temp_state_dir

    # Restore environment variables
    if old_xdg_config is None:
        os.environ.pop("XDG_CONFIG_HOME", None)
    else:
        os.environ["XDG_CONFIG_HOME"] = old_xdg_config

    if old_xdg_state is None:
        os.environ.pop("XDG_STATE_HOME", None)
    else:
        os.environ["XDG_STATE_HOME"] = old_xdg_state


def test_auto_init_creates_config_file(temp_xdg_dirs: tuple[str, str, Path, Path]) -> None:
    """Test that auto-init creates the config file on first run."""
    config_dir, state_dir, config_path, state_path = temp_xdg_dirs

    # Run agent-nook with a simple command (triggers auto-init)
    # The command follows 'run' as positional arguments
    result = subprocess.run(
        [sys.executable, "-m", "agent_nook", "run", "python3", "-c", "print('hello')"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Command should succeed (0 = success)
    assert result.returncode == 0, f"Command failed: {result.stderr}"

    # Config file should exist (with agent-nook subdirectory per XDG spec)
    config_file = config_path / "agent-nook" / "sandbox.yaml"
    assert config_file.exists(), f"Config file was not created at {config_file}"

    # Config file should be a valid YAML
    import yaml

    with open(config_file) as f:
        config = yaml.safe_load(f)

    assert isinstance(config, dict), "Config should be a dictionary"


def test_auto_init_creates_log_directory(temp_xdg_dirs: tuple[str, str, Path, Path]) -> None:
    """Test that auto-init creates the log directory."""
    config_dir, state_dir, config_path, state_path = temp_xdg_dirs

    # Run agent-nook with a simple command
    result = subprocess.run(
        [sys.executable, "-m", "agent_nook", "run", "python3", "-c", "print('hello')"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Log directory should exist (created by auto-init)
    logs_dir = state_path / "logs"
    assert logs_dir.exists(), "Log directory was not created"
    assert logs_dir.is_dir(), "Log path should be a directory"


def test_auto_init_does_not_overwrite_existing_config(temp_xdg_dirs: tuple[str, str, Path, Path]) -> None:
    """Test that auto-init doesn't overwrite existing config files."""
    config_dir, state_dir, config_path, state_path = temp_xdg_dirs

    # Copy the bundled config as the template
    bundled_config = Path(__file__).parent.parent.parent / "src/agent_nook/config/sandbox.yaml"
    config_file = config_path / "agent-nook" / "sandbox.yaml"
    config_file.write_text(bundled_config.read_text())

    # Modify the name to a custom value (but keep all mounts intact)
    custom_content = config_file.read_text().replace('name: "agent-sandbox"', 'name: "my-custom-sandbox"')
    config_file.write_text(custom_content)

    # Verify the custom name is in place
    assert 'name: "my-custom-sandbox"' in config_file.read_text()

    # Run agent-nook - the command should succeed
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_nook",
            "run",
            "python3",
            "-c",
            "print('hello')",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Command should succeed
    assert result.returncode == 0, f"Command failed: {result.stderr}"

    # Custom config should still be there (name should not be changed)
    assert 'name: "my-custom-sandbox"' in config_file.read_text(), "Custom config was overwritten"
