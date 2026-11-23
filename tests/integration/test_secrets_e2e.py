"""End-to-end integration tests for secrets passed into the sandbox.

These run the real ``agent-nook run`` binary (installed once via pipx, shared
across xdist workers) against bubblewrap. They exercise the full path: the
``--secret`` / ``--secret-env`` CLI flags, the loader merge, host-side
materialization into a private temp file, the read-only bind-mount into
``/run/secrets``, and the env-export wrapper.

Secrets are delivered by bind-mount, never on the command line or as host env
vars, so a secret value must never show up in agent-nook's own output or logs —
even with ``-v`` (DEBUG), which logs the full bwrap command (temp-file paths,
but not secret bytes).
"""

import os
from pathlib import Path

import pytest


def _run(test_sandbox_run, *secret_args: str, script: str) -> str:
    """Run ``script`` inside the sandbox with the given secret flags.

    Args:
        test_sandbox_run: The shared installed-binary runner.
        secret_args: The ``--secret`` / ``--secret-env`` arguments (each flag
            and its value passed as a separate positional, e.g.
            ``"--secret", "tok=cmd:printf tok"``).
        script: Python source executed inside the sandbox; its stdout is
            returned.

    Returns:
        The sandboxed command's stdout.

    Raises:
        AssertionError: If the sandboxed command does not exit 0.
    """
    result = test_sandbox_run("run", *secret_args, "python3", "-c", script)
    assert result.returncode == 0, f"Command failed: {result.returncode}\n  stdout: {result.stdout!r}\n  stderr: {result.stderr!r}"
    return result.stdout


@pytest.mark.integration
def test_file_secret_mounted_at_target(test_sandbox_run, tmp_path) -> None:
    """A --secret host file is read-only bind-mounted at /run/secrets/<name>."""
    host_file = tmp_path / "gh-token.txt"
    host_file.write_text("ghs_filecontent123\n", encoding="utf-8")

    out = _run(test_sandbox_run, "--secret", f"gh-token={host_file}", script="print(open('/run/secrets/gh-token').read(), end='')")

    assert "ghs_filecontent123\n" in out, f"Expected the file content at /run/secrets/gh-token, got: {out!r}"


@pytest.mark.integration
def test_command_secret_stdout_is_value(test_sandbox_run) -> None:
    """A --secret cmd: source uses the command's stdout as the secret value."""
    out = _run(
        test_sandbox_run, "--secret", "tok=cmd:printf cmdsecretvalue42", script="print(open('/run/secrets/tok').read(), end='')"
    )

    assert "cmdsecretvalue42" in out, f"Expected the command stdout as the secret value, got: {out!r}"


@pytest.mark.integration
def test_env_secret_exported(test_sandbox_run) -> None:
    """A --secret with --secret-env is exported inside the sandbox as that var."""
    out = _run(
        test_sandbox_run,
        "--secret",
        "tok=cmd:printf envsecretvalue77",
        "--secret-env",
        "tok=GH_TOKEN",
        script="import os; print(os.environ.get('GH_TOKEN', ''), end='')",
    )

    assert "envsecretvalue77" in out, f"Expected the secret exported as GH_TOKEN, got: {out!r}"


@pytest.mark.integration
def test_temp_dir_removed_after_exit(test_sandbox_run) -> None:
    """The per-run secret temp dir is removed once the sandbox exits."""
    # The temp dir is created under $XDG_RUNTIME_DIR/agent-nook (mode 0700) and
    # cleaned up in the executor's finally block, which runs before the
    # agent-nook process exits — so by the time run_command returns, it is gone.
    base = Path(os.environ["XDG_RUNTIME_DIR"]) / "agent-nook"

    _run(test_sandbox_run, "--secret", "tok=cmd:printf tok", script="print('ok', end='')")

    leftovers = list(base.glob("agent-nook-secrets-*")) if base.exists() else []
    assert leftovers == [], f"Secret temp dirs were not cleaned up: {leftovers}"


@pytest.mark.integration
def test_secret_value_not_in_output_or_log(test_sandbox_run) -> None:
    """The secret value never appears in agent-nook's output, even at DEBUG."""
    secret_value = "supersecretvalue-9f8a7b-XYZ"

    # -v enables DEBUG logging, which logs the full bwrap command (including the
    # secret temp-file paths) — but never the secret bytes. The value must be
    # absent from the combined output regardless.
    result = test_sandbox_run(
        "-v",
        "run",
        "--secret",
        f"tok=cmd:printf {secret_value}",
        "python3",
        "-c",
        "print('done', end='')",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert secret_value not in combined, f"Secret value leaked into output:\n{combined!r}"
