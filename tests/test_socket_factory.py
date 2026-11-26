"""Tests for socket_factory.generate (path derivation, no real socket created).

The factory only reserves a path — it never binds a socket — so tests assert
on the returned path and the directory it lives in, not on any socket object.
"""

import os
import stat
from pathlib import Path

from agent_nook.socket_factory import generate


def test_generates_name_with_pid_and_counter(tmp_path):
    """The path is {base}/{name}-{pid}-{counter}.sock with a fixed pid."""
    path = generate("mybox", 0, pid=4321, base_dir=str(tmp_path))
    assert path == str(tmp_path / "mybox-4321-0.sock")


def test_counter_increments_per_entry(tmp_path):
    """The counter disambiguates multiple tracked entries in the same run."""
    a = generate("mybox", 0, pid=100, base_dir=str(tmp_path))
    b = generate("mybox", 1, pid=100, base_dir=str(tmp_path))
    assert a.endswith("-100-0.sock")
    assert b.endswith("-100-1.sock")
    assert a != b


def test_strips_directory_components_from_name(tmp_path):
    """A name with directory components is collapsed to its basename only."""
    path = generate("../../etc/passwd", 2, pid=7, base_dir=str(tmp_path))
    assert Path(path).name == "passwd-7-2.sock"


def test_creates_base_dir_with_mode_0700(tmp_path):
    """The base directory is created 0700 on first creation (umask-independent).

    ``os.makedirs(mode=0o700)`` yields ``0700`` regardless of umask (no
    group/other bits to strip), so the dir is born ``0700`` — no ``chmod`` is
    applied, and a pre-existing loose base dir is left untouched.
    """
    base = tmp_path / "agent-nook"
    generate("mybox", 0, pid=9, base_dir=str(base))
    mode = stat.S_IMODE(Path(base).stat().st_mode)
    assert mode == 0o700


def test_does_not_create_socket_file(tmp_path):
    """Only the path is reserved; the socket file itself is never created."""
    path = generate("mybox", 0, pid=9, base_dir=str(tmp_path))
    assert Path(path).exists() is False


def test_uses_xdg_runtime_dir_base(tmp_path, monkeypatch):
    """With XDG_RUNTIME_DIR set, sockets live under $XDG_RUNTIME_DIR/agent-nook."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    path = generate("mybox", 3, pid=55)
    assert path == str(tmp_path / "agent-nook" / "mybox-55-3.sock")


def test_falls_back_to_tmp_without_xdg(tmp_path, monkeypatch):
    """Without XDG_RUNTIME_DIR, the base dir is /tmp."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    path = generate("mybox", 0, pid=55)
    assert path == "/tmp/mybox-55-0.sock"


def test_default_pid_is_current_process():
    """Without an explicit pid, the caller's pid is baked into the name."""
    path = generate("mybox", 0, base_dir="/tmp")
    assert f"-{os.getpid()}-0.sock" in path
