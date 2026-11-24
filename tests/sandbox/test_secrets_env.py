"""Tests for the secrets_env wrapper (export secrets as env, then exec).

The wrapper replaces itself with the target command via ``os.execvpe``, so the
real-run tests invoke it as a subprocess and assert on the exec'd command's
output rather than on the wrapper process itself. The pure manifest parser
(:func:`load_secret_env`) is unit-tested directly, without a subprocess.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_nook.secrets_env import load_secret_env

WRAPPER = Path(__file__).resolve().parent.parent.parent / "src" / "agent_nook" / "secrets_env.py"


@pytest.fixture
def printenv_available() -> str | None:
    """Return the ``printenv`` path, or skip the calling test if it is absent."""
    path = shutil.which("printenv")
    if not path:
        pytest.skip("printenv not available on PATH")
    return path


@pytest.fixture
def not_root() -> None:
    """Skip on root, where mode 000 files are still readable (no permission error)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("cannot simulate an unreadable secret as root")


@pytest.fixture
def bwrap_available() -> str | None:
    """Return the ``bwrap`` path, or skip the calling test if it is absent."""
    path = shutil.which("bwrap")
    if not path:
        pytest.skip("bwrap not available on PATH")
    return path


def _write_manifest(tmp_path: Path, mapping: dict[str, str]) -> Path:
    """Write a secrets manifest mapping ``VAR`` -> ``<path>`` and return its path."""
    manifest = tmp_path / "secrets.env"
    manifest.write_text("".join(f"{var}={path}\n" for var, path in mapping.items()))
    return manifest


# --- Pure parser (no subprocess) ---


def test_load_secret_env_parses_manifest(tmp_path):
    """load_secret_env maps each env var to its secret file's contents."""
    secret = tmp_path / "gh-token"
    secret.write_bytes(b"tok\n")
    manifest = _write_manifest(tmp_path, {"GH_TOKEN": str(secret)})
    assert load_secret_env(str(manifest)) == {"GH_TOKEN": "tok\n"}


def test_load_secret_env_skips_blank_lines(tmp_path):
    """Blank lines in the manifest are ignored."""
    secret = tmp_path / "a"
    secret.write_bytes(b"1")
    manifest = tmp_path / "secrets.env"
    manifest.write_text("AA=" + str(secret) + "\n\n\n")  # trailing blank lines
    assert load_secret_env(str(manifest)) == {"AA": "1"}


def test_load_secret_env_bad_line_raises(tmp_path):
    """A manifest line without '=' raises ValueError."""
    manifest = tmp_path / "secrets.env"
    manifest.write_text("NO_EQUALS_SIGN\n")
    with pytest.raises(ValueError):
        load_secret_env(str(manifest))


def test_load_secret_env_missing_manifest_raises(tmp_path):
    """A missing manifest raises OSError."""
    with pytest.raises(OSError):
        load_secret_env(str(tmp_path / "does-not-exist.env"))


# --- Real exec (subprocess) ---


def test_exports_secret_and_execs_command(tmp_path, monkeypatch, printenv_available):
    """The wrapper exports the secret and the exec'd command sees it."""
    secret = tmp_path / "gh-token"
    secret.write_bytes(b"tok")
    manifest = _write_manifest(tmp_path, {"GH_TOKEN": str(secret)})
    monkeypatch.setenv("AGENT_NOOK_SECRETS_MANIFEST", str(manifest))

    result = subprocess.run(
        [sys.executable, str(WRAPPER), printenv_available, "GH_TOKEN"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "tok\n"


def test_execed_command_inherits_full_environment(tmp_path, monkeypatch, printenv_available):
    """The exec'd command runs with the parent environment intact (PATH etc.)."""
    secret = tmp_path / "gh-token"
    secret.write_bytes(b"tok")
    manifest = _write_manifest(tmp_path, {"GH_TOKEN": str(secret)})
    monkeypatch.setenv("AGENT_NOOK_SECRETS_MANIFEST", str(manifest))

    # printenv with no args prints every variable; PATH must still be present.
    result = subprocess.run(
        [sys.executable, str(WRAPPER), printenv_available],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "GH_TOKEN=tok" in result.stdout
    assert any(line.startswith("PATH=") for line in result.stdout.splitlines())


def test_unreadable_secret_fails_cleanly(tmp_path, monkeypatch, printenv_available, not_root):
    """An unreadable secret file makes the wrapper exit 1 (not crash)."""
    secret = tmp_path / "gh-token"
    secret.write_bytes(b"tok")
    os.chmod(secret, 0o000)
    manifest = _write_manifest(tmp_path, {"GH_TOKEN": str(secret)})
    monkeypatch.setenv("AGENT_NOOK_SECRETS_MANIFEST", str(manifest))

    result = subprocess.run(
        [sys.executable, str(WRAPPER), printenv_available, "GH_TOKEN"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "cannot read manifest" in result.stderr


def test_no_command_fails_cleanly(tmp_path, monkeypatch):
    """Invoking the wrapper with no command exits 1."""
    manifest = _write_manifest(tmp_path, {"GH_TOKEN": "/nonexistent"})
    monkeypatch.setenv("AGENT_NOOK_SECRETS_MANIFEST", str(manifest))
    result = subprocess.run(
        [sys.executable, str(WRAPPER)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "no command to exec" in result.stderr


def test_unknown_command_returns_127(tmp_path, monkeypatch):
    """A command that cannot be found makes the wrapper exit 127."""
    # The manifest points at a real secret so manifest loading succeeds and we
    # actually reach the exec step, which fails on the missing command.
    secret = tmp_path / "gh-token"
    secret.write_bytes(b"tok")
    manifest = _write_manifest(tmp_path, {"GH_TOKEN": str(secret)})
    monkeypatch.setenv("AGENT_NOOK_SECRETS_MANIFEST", str(manifest))
    result = subprocess.run(
        [sys.executable, str(WRAPPER), "this-command-does-not-exist-xyz"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 127
    assert "cannot exec" in result.stderr


def test_manifest_default_path_used_when_no_env(tmp_path, monkeypatch, bwrap_available):
    """With no AGENT_NOOK_SECRETS_MANIFEST the wrapper uses the fixed default.

    The wrapper runs inside a bwrap sandbox whose ``/run`` is a fresh tmpfs, so
    the fixed default ``/run/agent_nook_secrets.env`` is absent there even when
    the host's real ``/run`` already has it. The wrapper therefore fails to read
    the default and exits 1 — confirming the default was used, without touching
    the host's ``/run`` (or any real system path) at all.
    """
    monkeypatch.delenv("AGENT_NOOK_SECRETS_MANIFEST", raising=False)
    cmd = [
        bwrap_available,
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        "/run",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--",
        sys.executable,
        str(WRAPPER),
        "somecommand",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert "cannot read manifest" in result.stderr
    assert "/run/agent_nook_secrets.env" in result.stderr
