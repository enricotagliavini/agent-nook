"""End-to-end test for the orchestration: feature through a real bwrap sandbox.

The orchestration entry starts the host-side stdio bridge (stdio_bridge.py)
on the host; the sandboxed command runs the sandbox-side stub (stdio_stub.py)
and feeds a request through it. Data flows both ways: test process ->
sandboxed stub -> socket -> bridge -> echo "MCP" child -> back.

Follows the integration-test conventions: agent-nook is installed from the
freshly built wheel via pipx (shared session fixtures in conftest.py), the
config file is passed via --config, and tests skip cleanly when bwrap is
not available.
"""

import shutil
import subprocess
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration

BWRAP = shutil.which("bwrap") is not None

REPO = Path(__file__).resolve().parent.parent.parent
BRIDGE = REPO / "src" / "agent_nook" / "stdio_bridge.py"
STUB = REPO / "src" / "agent_nook" / "stdio_stub.py"

# In-sandbox driver: feeds a request to stdio_stub, keeps the session open
# until the echo response arrives (like a real MCP client, whose stdin stays
# open for the whole session), then closes stdin and checks the exit code.
DRIVER = """\
import os
import select
import subprocess
import sys
import time

p = subprocess.Popen(
    [sys.executable, sys.argv[1], "--socket", sys.argv[2]],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
)
p.stdin.write(b"hello-mcp")
p.stdin.flush()
buf = b""
deadline = time.monotonic() + 10
while b"ECHO:hello-mcp" not in buf and time.monotonic() < deadline:
    r, _, _ = select.select([p.stdout], [], [], 0.1)
    if r:
        d = os.read(p.stdout.fileno(), 4096)
        if not d:
            break
        buf += d
p.stdin.close()
rc = p.wait(timeout=10)
sys.exit(0 if rc == 0 and b"ECHO:hello-mcp" in buf else 1)
"""


@pytest.fixture()
def sock_dir(tmp_path) -> Path:
    d = tmp_path / "socks"
    d.mkdir()
    return d


@pytest.fixture()
def e2e_config(xdg_config_dir, sock_dir) -> Path:
    (sock_dir / "driver.py").write_text(DRIVER)
    sock = sock_dir / "mcp.sock"
    config_path = xdg_config_dir / "orchestration_e2e.yaml"
    config_path.write_text(
        f"""name: orchestration-e2e
mounts:
  - type: ro-bind
    source: /
    target: /
  - type: proc
    target: /proc
  - type: bind
    source: {sock_dir}
    target: {sock_dir}
orchestration:
  - command:
      - python3
      - {BRIDGE}
      - --listen
      - {sock}
      - --
      - python3
      - -c
      - |
        import os
        while True:
            d = os.read(0, 4096)
            if not d:
                break
            os.write(1, b'ECHO:' + d)
    socket: {sock}
    wait-timeout: 30
"""
    )
    return config_path


@pytest.mark.skipif(not BWRAP, reason="bwrap not available")
def test_orchestration_roundtrip_through_bwrap(test_sandbox_run, e2e_config, sock_dir):
    """Host bridge + in-sandbox stub: data flows both ways through bwrap."""
    sock = sock_dir / "mcp.sock"
    result = test_sandbox_run(
        "run",
        "--config",
        str(e2e_config),
        "python3",
        str(sock_dir / "driver.py"),
        str(STUB),
        str(sock),
    )
    assert result.returncode == 0, f"agent-nook failed (rc={result.returncode}):\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    # The bridge ran on the host and served exactly one session.
    assert "stdio_bridge: listening on" in result.stdout
    assert "started child pid=" in result.stdout
    # The bridge was stopped cleanly by the orchestrator (SIGTERM): the unix
    # socket file is only unlinked on a clean bridge shutdown, so its absence
    # proves the orchestrator's stop() ran (and did not have to SIGKILL).
    assert not sock.exists(), "bridge socket file left behind: the bridge was not stopped cleanly"


@pytest.mark.skipif(not BWRAP, reason="bwrap not available")
def test_orchestration_never_ready_blocks_sandbox(test_sandbox_run, xdg_config_dir, tmp_path):
    """Socket never appears: agent-nook fails before starting the sandbox,
    and the orchestration command is cleaned up."""
    marker = tmp_path / "sandbox_ran"
    sleeper = "import time; time.sleep(30)  # e2e-fail-sleeper"
    config_path = xdg_config_dir / "orchestration_e2e_fail.yaml"
    config_path.write_text(
        f"""name: orchestration-e2e-fail
mounts:
  - type: ro-bind
    source: /
    target: /
  - type: proc
    target: /proc
  - type: tmpfs
    target: /tmp
  - type: bind
    source: {tmp_path}
    target: /sandbox-tmp
orchestration:
  - command:
      - python3
      - -c
      - "{sleeper}"
    socket: {tmp_path / "never.sock"}
    wait-timeout: 1
"""
    )
    result = test_sandbox_run(
        "run",
        "--config",
        str(config_path),
        "/bin/sh",
        "-c",
        f"touch {marker}",
    )
    assert result.returncode != 0, f"agent-nook should fail when the socket never becomes ready:\n{result.stdout}\n{result.stderr}"
    assert "not ready after" in result.stdout + result.stderr
    assert not marker.exists(), "the sandbox must not start when an orchestration socket never becomes ready"
    # The orchestration command was stopped (SIGTERM -> reaped).
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        r = subprocess.run(["pgrep", "-f", "e2e-fail-sleeper"], check=False, capture_output=True)
        if r.returncode != 0:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the never-ready orchestration command was not stopped")
