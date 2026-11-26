"""Tests for secrets wiring in the bwrap builder.

The builder returns a command line (it never runs bwrap), so these tests
inspect the returned argument list rather than executing anything.

The secrets block is appended after the config mounts, so ``--dir /run/secrets``
always precedes the per-secret ``--ro-bind`` targets — the ordering bwrap
requires (a mount point must exist before anything under it is bind-mounted).
"""

from agent_nook.config.config import Mount
from agent_nook.config.config import SandboxConfig
from agent_nook.sandbox.builder import BwrapBuilder
from agent_nook.sandbox.secret import SecretMount
from agent_nook.sandbox.secret import SecretsBundle

HOST_SECRET = "/tmp/agent-nook-secrets-abc/gh-token"
MANIFEST = "/tmp/agent-nook-secrets-abc/secrets.env"
WRAPPER = "/host/agent_nook/secrets_env.py"


def _has(cmd: list[str], block: list[str]) -> bool:
    """Whether ``block`` appears as a contiguous run inside ``cmd``."""
    return any(cmd[i : i + len(block)] == block for i in range(len(cmd) - len(block) + 1))


def _file_only_bundle() -> SecretsBundle:
    """A single file-only secret (no env exposure → no wrapper)."""
    return SecretsBundle(
        temp_dir="/tmp/agent-nook-secrets-abc",
        mounts=[SecretMount(name="gh-token", host_path=HOST_SECRET, target="/run/secrets/gh-token", env_var=None)],
        manifest_host_path=None,
        wrapper_host_path=None,
    )


def _env_bundle() -> SecretsBundle:
    """A single env-exposed secret (needs the wrapper to export it)."""
    return SecretsBundle(
        temp_dir="/tmp/agent-nook-secrets-abc",
        mounts=[SecretMount(name="gh-token", host_path=HOST_SECRET, target="/run/secrets/gh-token", env_var="GH_TOKEN")],
        manifest_host_path=MANIFEST,
        wrapper_host_path=WRAPPER,
    )


def _config(secrets_root: bool = False) -> SandboxConfig:
    """Minimal config; mounts ``/run/secrets`` itself when ``secrets_root``."""
    target = "/run/secrets" if secrets_root else "/run"
    return SandboxConfig(name="test", mounts=[Mount(target=target, type="tmpfs")])


def test_builder_no_secrets_by_default():
    """Without a secrets bundle, no /run/secrets wiring is added."""
    cmd = BwrapBuilder(_config()).build(["echo", "hi"])
    assert "/run/secrets" not in cmd
    assert "/run/secrets_env.py" not in cmd


def test_builder_file_only_secret_adds_ro_bind_and_dir():
    """A file-only secret is read-only bind-mounted into a created /run/secrets."""
    cmd = BwrapBuilder(_config()).build(["echo", "hi"], secrets=_file_only_bundle())

    assert _has(cmd, ["--dir", "/run/secrets"])
    assert _has(cmd, ["--ro-bind", HOST_SECRET, "/run/secrets/gh-token"])
    # No wrapper bits for a file-only secret.
    assert not _has(cmd, ["--ro-bind", MANIFEST, "/run/agent_nook_secrets.env"])
    assert not _has(cmd, ["--bind", WRAPPER, "/run/secrets_env.py"])


def test_builder_dir_before_ro_bind_targets():
    """The created /run/secrets dir precedes its per-secret read-only mounts."""
    cmd = BwrapBuilder(_config()).build(["echo", "hi"], secrets=_file_only_bundle())
    dir_idx = cmd.index("--dir")
    ro_bind_idx = cmd.index("--ro-bind")
    assert dir_idx < ro_bind_idx


def test_builder_env_secret_adds_wrapper_bits_and_prepends_wrapper():
    """An env-exposed secret also binds the manifest + wrapper and prepends it."""
    cmd = BwrapBuilder(_config()).build(["python3", "agent.py"], secrets=_env_bundle())

    assert _has(cmd, ["--dir", "/run/secrets"])
    assert _has(cmd, ["--ro-bind", HOST_SECRET, "/run/secrets/gh-token"])
    assert _has(cmd, ["--ro-bind", MANIFEST, "/run/agent_nook_secrets.env"])
    assert _has(cmd, ["--bind", WRAPPER, "/run/secrets_env.py"])
    # The wrapper is prepended to the command (last args, appended after chdir).
    assert cmd[-4:] == ["python3", "/run/secrets_env.py", "python3", "agent.py"]


def test_builder_dir_skipped_when_secrets_root_already_mounted():
    """--dir /run/secrets is omitted when the config already mounts that path."""
    cmd = BwrapBuilder(_config(secrets_root=True)).build(["echo", "hi"], secrets=_file_only_bundle())

    assert "--tmpfs" in cmd and "/run/secrets" in cmd  # config mount
    assert not _has(cmd, ["--dir", "/run/secrets"])
    assert _has(cmd, ["--ro-bind", HOST_SECRET, "/run/secrets/gh-token"])


def test_builder_multiple_secrets_all_ro_bind():
    """Every secret is read-only bind-mounted; the wrapper is prepended once."""
    bundle = SecretsBundle(
        temp_dir="/tmp/agent-nook-secrets-abc",
        mounts=[
            SecretMount(name="a", host_path="/tmp/agent-nook-secrets-abc/a", target="/run/secrets/a", env_var=None),
            SecretMount(name="b", host_path="/tmp/agent-nook-secrets-abc/b", target="/run/secrets/b", env_var="B"),
        ],
        manifest_host_path=MANIFEST,
        wrapper_host_path=WRAPPER,
    )
    cmd = BwrapBuilder(_config()).build(["echo", "hi"], secrets=bundle)

    assert _has(cmd, ["--ro-bind", "/tmp/agent-nook-secrets-abc/a", "/run/secrets/a"])
    assert _has(cmd, ["--ro-bind", "/tmp/agent-nook-secrets-abc/b", "/run/secrets/b"])
    assert _has(cmd, ["--ro-bind", MANIFEST, "/run/agent_nook_secrets.env"])
    assert cmd[-4:] == ["python3", "/run/secrets_env.py", "echo", "hi"]
