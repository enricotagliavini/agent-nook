"""Tests for sandbox/secret.py: prepare_secrets (materialize + bundle).

These exercise the host-side secret preparation only — the wrapper
(secrets_env.py) and the builder plumbing are covered elsewhere.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from agent_nook.config.config import Secret
from agent_nook.sandbox.secret import prepare_secrets


def _host_file(tmp_path: Path, name: str, content: bytes) -> str:
    """Write a host file (the secret's *source*, outside the sandbox)."""
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


def _read_secret(bundle, name: str) -> bytes:
    """Read a materialized secret file back from the bundle's temp dir."""
    return Path(bundle.temp_dir, name).read_bytes()


# --- Sources ---


def test_file_source_materialized(tmp_path):
    """A file-source secret's host contents are copied to the temp file."""
    host = _host_file(tmp_path, "creds", b"secret-value")
    bundle = prepare_secrets([Secret(name="gh-token", source_file=host)])
    try:
        assert _read_secret(bundle, "gh-token") == b"secret-value"
    finally:
        bundle.cleanup()


def test_command_source_captures_stdout(tmp_path):
    """A command-source secret's stdout (no shell) becomes the value.

    ``echo`` appends its own trailing newline; it is stripped here so the secret
    does not carry an unexpected byte (see ``_read_content``).
    """
    bundle = prepare_secrets([Secret(name="gh-token", source_command=["echo", "tok"])])
    try:
        assert _read_secret(bundle, "gh-token") == b"tok"
    finally:
        bundle.cleanup()


def test_file_trailing_newline_fidelity(tmp_path):
    """Trailing newlines in a file source are preserved (written as bytes)."""
    host = _host_file(tmp_path, "creds", b"tok\n\n")
    bundle = prepare_secrets([Secret(name="s", source_file=host)])
    try:
        assert _read_secret(bundle, "s") == b"tok\n\n"
    finally:
        bundle.cleanup()


def test_command_strips_exactly_one_trailing_newline(tmp_path):
    """Only one trailing newline is removed from a command source.

    Internal newlines survive and only a single trailing one is dropped, so a
    multi-line secret keeps every line but loses the final terminator.
    """
    bundle = prepare_secrets([Secret(name="s", source_command=["printf", "%s", "x\ny\n"])])
    try:
        assert _read_secret(bundle, "s") == b"x\ny"
    finally:
        bundle.cleanup()


def test_command_without_trailing_newline_is_untouched(tmp_path):
    """A command emitting no trailing newline leaves the value unchanged."""
    bundle = prepare_secrets([Secret(name="s", source_command=["printf", "tok"])])
    try:
        assert _read_secret(bundle, "s") == b"tok"
    finally:
        bundle.cleanup()


# --- Permissions ---


def test_secret_files_are_mode_0600(tmp_path):
    """Each materialized secret is mode 0600 (even under a loose umask)."""
    old_umask = os.umask(0o022)  # without the explicit chmod, write_bytes would yield 0644
    try:
        host = _host_file(tmp_path, "creds", b"v")
        bundle = prepare_secrets([Secret(name="s", source_file=host)])
        mode = stat.S_IMODE(Path(bundle.temp_dir, "s").stat().st_mode)
        assert mode == 0o600
    finally:
        os.umask(old_umask)
        bundle.cleanup()


def test_temp_dir_is_mode_0700(tmp_path, monkeypatch):
    """The per-run temp dir is mode 0700."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    host = _host_file(tmp_path, "creds", b"v")
    bundle = prepare_secrets([Secret(name="s", source_file=host)])
    try:
        mode = stat.S_IMODE(Path(bundle.temp_dir).stat().st_mode)
        assert mode == 0o700
    finally:
        bundle.cleanup()


def test_manifest_is_mode_0600(tmp_path):
    """The manifest file is mode 0600."""
    bundle = prepare_secrets([Secret(name="s", source_command=["echo", "v"], env_var="FOO")])
    try:
        mode = stat.S_IMODE(Path(bundle.manifest_host_path).stat().st_mode)
        assert mode == 0o600
    finally:
        bundle.cleanup()


# --- Manifest + env exposure ---


def test_manifest_maps_env_var_to_target(tmp_path):
    """The manifest maps ENV_VAR to its in-sandbox /run/secrets/<name> path."""
    bundle = prepare_secrets([Secret(name="gh-token", source_command=["echo", "v"], env_var="GH_TOKEN")])
    try:
        manifest = Path(bundle.temp_dir, bundle.manifest_host_path).read_text()
        assert manifest == "GH_TOKEN=/run/secrets/gh-token\n"
    finally:
        bundle.cleanup()


def test_manifest_lists_all_env_secrets(tmp_path):
    """The manifest has one line per env-exposed secret (file-only ones omitted)."""
    secrets = [
        Secret(name="a", source_command=["echo", "1"], env_var="AA"),
        Secret(name="b", source_file=_host_file(tmp_path, "b", b"2")),  # file-only
        Secret(name="c", source_command=["echo", "3"], env_var="CC"),
    ]
    bundle = prepare_secrets(secrets)
    try:
        manifest = Path(bundle.temp_dir, bundle.manifest_host_path).read_text()
        assert manifest == "AA=/run/secrets/a\nCC=/run/secrets/c\n"
    finally:
        bundle.cleanup()


def test_no_manifest_for_file_only_secret(tmp_path):
    """A file-only secret produces no manifest and no wrapper path."""
    host = _host_file(tmp_path, "creds", b"v")
    bundle = prepare_secrets([Secret(name="s", source_file=host)])
    try:
        assert bundle.manifest_host_path is None
        assert bundle.wrapper_host_path is None
    finally:
        bundle.cleanup()


def test_needs_env_true_when_env_secret(tmp_path):
    """needs_env is True when any secret opts in to env exposure."""
    bundle = prepare_secrets([Secret(name="s", source_command=["echo", "v"], env_var="FOO")])
    try:
        assert bundle.needs_env is True
    finally:
        bundle.cleanup()


def test_needs_env_false_when_file_only(tmp_path):
    """needs_env is False when every secret is file-only."""
    host = _host_file(tmp_path, "creds", b"v")
    bundle = prepare_secrets([Secret(name="s", source_file=host)])
    try:
        assert bundle.needs_env is False
    finally:
        bundle.cleanup()


# --- Empty ---


def test_no_secrets_creates_no_temp_dir():
    """No secrets → no temp dir, empty mounts, no manifest/wrapper."""
    bundle = prepare_secrets([])
    assert bundle.temp_dir is None
    assert bundle.mounts == []
    assert bundle.manifest_host_path is None
    assert bundle.wrapper_host_path is None
    assert bundle.needs_env is False
    # cleanup is a no-op and must not raise.
    bundle.cleanup()


# --- Cleanup ---


def test_cleanup_removes_dir_and_files(tmp_path):
    """cleanup removes the temp dir and everything inside it."""
    bundle = prepare_secrets([Secret(name="s", source_command=["echo", "v"], env_var="FOO")])
    assert Path(bundle.temp_dir).exists()
    bundle.cleanup()
    assert not Path(bundle.temp_dir).exists()


def test_cleanup_is_noop_when_no_temp_dir():
    """cleanup on an empty bundle does nothing and does not raise."""
    bundle = prepare_secrets([])
    bundle.cleanup()  # must not raise


def test_failed_materialize_cleans_up_temp_dir(tmp_path, monkeypatch):
    """A materialize failure (missing file) removes the partial temp dir."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        prepare_secrets([Secret(name="s", source_file="/nonexistent/path/creds")])
    # The partial temp dir must have been cleaned up by prepare_secrets.
    leftovers = list(Path(tmp_path, "agent-nook").glob("agent-nook-secrets-*"))
    assert leftovers == []


# --- Perms base dir + XDG ---


def test_materializes_under_xdg_base(tmp_path, monkeypatch):
    """With XDG_RUNTIME_DIR set, the temp dir lives under $XDG_RUNTIME_DIR/agent-nook."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    host = _host_file(tmp_path, "creds", b"v")
    bundle = prepare_secrets([Secret(name="s", source_file=host)])
    try:
        assert bundle.temp_dir.startswith(str(tmp_path / "agent-nook"))
    finally:
        bundle.cleanup()


def test_falls_back_to_tmp_without_xdg(tmp_path, monkeypatch):
    """Without XDG_RUNTIME_DIR, the temp dir is under /tmp."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    host = _host_file(tmp_path, "creds", b"v")
    bundle = prepare_secrets([Secret(name="s", source_file=host)])
    try:
        assert bundle.temp_dir.startswith("/tmp")
    finally:
        bundle.cleanup()


# --- Errors ---


def test_command_nonzero_exit_raises(tmp_path):
    """A command-source secret whose command exits non-zero raises."""
    with pytest.raises(subprocess.CalledProcessError):
        prepare_secrets([Secret(name="s", source_command=["false"])])


def test_missing_file_source_raises(tmp_path):
    """A file-source secret whose host file is missing raises."""
    with pytest.raises(FileNotFoundError):
        prepare_secrets([Secret(name="s", source_file="/nonexistent/path/creds")])


# --- Targets ---


def test_target_is_run_secrets_name(tmp_path):
    """Each secret is bind-mounted at /run/secrets/<name>."""
    host = _host_file(tmp_path, "creds", b"v")
    bundle = prepare_secrets([Secret(name="gh-token", source_file=host)])
    try:
        assert bundle.mounts[0].target == "/run/secrets/gh-token"
        assert bundle.mounts[0].name == "gh-token"
    finally:
        bundle.cleanup()


def test_multiple_secrets_are_independent(tmp_path):
    """Several secrets materialize and bind independently."""
    secrets = [
        Secret(name="a", source_file=_host_file(tmp_path, "a", b"1")),
        Secret(name="b", source_command=["echo", "2"], env_var="BB"),
    ]
    bundle = prepare_secrets(secrets)
    try:
        names = sorted(m.name for m in bundle.mounts)
        assert names == ["a", "b"]
        assert _read_secret(bundle, "a") == b"1"
        assert _read_secret(bundle, "b") == b"2"  # echo's trailing newline is stripped
    finally:
        bundle.cleanup()
