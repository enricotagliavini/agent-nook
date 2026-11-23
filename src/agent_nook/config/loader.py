"""XDG-compliant configuration loader.

Reads configuration from YAML files and validates against a strict canonical schema.
The canonical format is enforced at load time with zero tolerance for deviations.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shlex
from pathlib import Path
from typing import Any

import yaml


try:
    from agent_nook.config.config import CapabilitySet
    from agent_nook.config.config import ConfigValidationError
    from agent_nook.config.config import Mount
    from agent_nook.config.config import NamespaceSet
    from agent_nook.config.config import OrchestrationEntry
    from agent_nook.config.config import SandboxConfig
    from agent_nook.config.config import Secret
except ImportError:
    from ..config.config import CapabilitySet
    from ..config.config import ConfigValidationError
    from ..config.config import Mount
    from ..config.config import NamespaceSet
    from ..config.config import OrchestrationEntry
    from ..config.config import SandboxConfig
    from ..config.config import Secret

logger = logging.getLogger(__name__)

# Usage hint appended to "command must be a non-empty string" errors for the
# orchestration section, so a misconfigured command points at the stdio-bridge
# invocation pattern. Kept as a constant so the single source of truth for the
# tokenization + error text (_parse_command) can reuse it verbatim.
_STDIO_BRIDGE_HINT = 'command: "stdio-bridge --listen ${{STDIO_SOCKET_PATH}} -- node index.js"'


def get_config_path() -> str:
    """Resolve the config path using XDG_CONFIG_HOME.

    Reads XDG_CONFIG_HOME from the environment, falls back to
    ~/.config if not set.

    Returns:
        The full path to the config file (agent-nook/sandbox.yaml).

    Example:
        >>> get_config_path()
        '/home/username/.config/agent-nook/sandbox.yaml'
    """
    xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg_config, "agent-nook", "sandbox.yaml")


# ───────────────────────────────────────────────────────────────────────────────
# VALIDATION PIPELINE
# ───────────────────────────────────────────────────────────────────────────────
#
# The ConfigLoader enforces a strict, multi-layered validation strategy:
#
#   Layer 1: _validate_structure()      — Raw dict → canonical schema check
#            → Catches user input errors: missing fields, wrong types, unknown keys
#
#   Layer 2: _parse_mounts()            — Dict list → Mount dataclass list
#            → Validates mount type enum, required fields (source/target) per type
#            → Validates create-source (bool) / create-as (dir|file) options
#            → Validates overlay-src (list) / workdir (str) options for overlay mounts
#            → Transforms user-friendly YAML format into internal Mount objects
#
#   Layer 2b: _ensure_run_mount()       — Post-mount enforcement of a writable /run
#            → Inserts a tmpfs /run (appended after user mounts) when absent,
#               so the topological sort keeps it after `/`; honors an explicit
#               tmpfs /run (size only); rejects any non-tmpfs /run.
#            → /run must be writable: bwrap creates the stdio socket bind-mount
#               target (/run/<name>) and bind-mounts the stdio stub at
#               /run/stdio_stub.py on every run; --ro-bind / / makes /run EROFS.
#
#   Layer 3: _parse_capabilities()      — Dict → CapabilitySet dataclass
#            → Ensures drop/keep are lists of strings (not dicts, ints, etc.)
#            → Prevents malformed capability specifications
#
#   Layer 4: _parse_unshare()           — Dict → NamespaceSet dataclass
#            → Ensures namespace keys are strings, values are booleans
#            → Prevents namespace injection via malformed dicts
#
#   Layer 5: _parse_orchestration()     — Dict list → OrchestrationEntry list
#            → Validates per-entry command (non-empty list of non-empty strings),
#              socket (non-empty str, udp: rejected), wait-timeout (positive
#              number), keep (bool); rejects unknown per-entry keys
#
#   Layer 5b: _parse_secrets()          — Dict list → Secret list
#            → Validates per-entry name (non-empty, filename-safe), exactly one
#              of file/command (file = non-empty host path; command = single
#              string tokenized with shlex, no shell); env (optional non-empty
#              str); rejects entries with both or neither source, unknown keys
#
#   Layer 6: SandboxConfig.__post_init__() — Dataclass construction
#            → Enforces the invariant: at least one mount point is required
#            → The LAST line of defense before the config is used
#
# Key principle: Each layer has a distinct responsibility. No layer is redundant.
# - Loader layers transform and validate STRUCTURAL data
# - Dataclass layers (Mount.build, SandboxConfig.__post_init__) validate SEMANTIC
#   consistency (e.g., that a "tmpfs" mount has a valid target, that capabilities
#   are properly resolved against a base set).
# ───────────────────────────────────────────────────────────────────────────────


class ConfigLoader:
    """Loads and validates sandbox configuration from a YAML file.

    The configuration must match the canonical schema exactly. No type
    coercion or format conversion is performed.

    Args:
        config_path: Optional path to the config file. If None, uses
            the default config path derived from XDG_CONFIG_HOME.
    """

    _FIELDS: dict[str, str] = {
        "name": "str",
        "chdir": "str | None",
        "mounts": "list[dict]",
        "capabilities": "dict",
        "unshare": "dict",
        "orchestration": "list[dict]",
        "secrets": "list[dict]",
        "die_with_parent": "bool",
        "new_session": "bool",
        "hostname": "str | None",
        "timeout": "int | float | None",
        "env_vars": "dict[str, str]",
        "unset_vars": "str | None",
    }

    _OPTIONAL_TYPES = frozenset({"str | None", "int | None", "int | float | None"})
    _VALID_MOUNT_TYPES = frozenset(
        {
            "bind",
            "ro-bind",
            "dev-bind",
            "bind-try",
            "ro-bind-try",
            "dev-bind-try",
            "tmpfs",
            "proc",
            "dev",
            "dir",
            "overlay",
            "ro-overlay",
            "tmp-overlay",
        }
    )
    # Types that carry a host source. Only these accept `create-source`/
    # `create-as`. The `*-try` variants are intentionally absent so the two are
    # mutually exclusive for free: a try mount can never carry a source flag.
    _SOURCE_MOUNT_TYPES = frozenset({"bind", "ro-bind", "dev-bind"})
    _OVERLAY_MOUNT_TYPES = frozenset({"overlay", "ro-overlay", "tmp-overlay"})
    _VALID_CREATE_AS = frozenset({"dir", "file"})

    def __init__(self, config_path: str | None = None) -> None:
        """Initialize the config loader.

        Args:
            config_path: Optional path to the config file. If None,
                uses the default path from  _get_config_path() (XDG-compliant).
        """
        self._config_path: str | None = config_path

    def set(
        self,
        data: dict[str, Any],
        override: dict[str, Any] | None = None,
    ) -> SandboxConfig:
        """Load configuration from a raw dict."""
        if override is not None:
            data = {**data, **override}
        return self._parse_config(data)

    def set_from_yaml(self, path: str) -> SandboxConfig:
        """Load configuration from a YAML file path."""
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return self.set(data)

    @staticmethod
    def _merge_cli_overrides(raw_dict: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
        """Apply command-line argument overrides to a raw configuration dictionary.

        This method performs the same logic as the previous `_apply_cli_overrides`
        function in __main__.py, but operates on a raw dictionary rather than a
        SandboxConfig.__dict__. This allows the merged result to be passed directly
        to ConfigLoader.set() for unified parsing and validation.

        Args:
            raw_dict: The raw configuration dictionary from the YAML file.
            args: Parsed CLI arguments namespace.

        Returns:
            A new dictionary with CLI overrides applied.
        """
        result: dict[str, Any] = raw_dict.copy()

        # Override chdir
        if hasattr(args, "chdir") and args.chdir:
            result["chdir"] = args.chdir

        # Override die_with_parent
        if hasattr(args, "die_with_parent") and args.die_with_parent is not True:
            result["die_with_parent"] = args.die_with_parent

        # Override new_session
        if hasattr(args, "new_session") and args.new_session is not True:
            result["new_session"] = args.new_session

        # Override hostname
        if hasattr(args, "hostname") and args.hostname:
            result["hostname"] = args.hostname

        # Override timeout
        if getattr(args, "timeout", None) is not None:
            result["timeout"] = args.timeout

        # Override mounts from --bind / --ro-bind / --bind-try / --ro-bind-try
        for bind in getattr(args, "bind", []):
            parts = bind.split(":")
            if len(parts) == 2:
                result.setdefault("mounts", []).append(
                    {
                        "source": parts[0],
                        "target": parts[1],
                        "type": "bind",
                    }
                )

        for bind in getattr(args, "ro_bind", []):
            parts = bind.split(":")
            if len(parts) == 2:
                result.setdefault("mounts", []).append(
                    {
                        "source": parts[0],
                        "target": parts[1],
                        "type": "ro-bind",
                    }
                )

        # --bind-try / --ro-bind-try: same as --bind / --ro-bind but emit the
        # bwrap *-try variants, which skip the mount when the host source is
        # missing.
        for bind in getattr(args, "bind_try", []):
            parts = bind.split(":")
            if len(parts) == 2:
                result.setdefault("mounts", []).append(
                    {
                        "source": parts[0],
                        "target": parts[1],
                        "type": "bind-try",
                    }
                )

        for bind in getattr(args, "ro_bind_try", []):
            parts = bind.split(":")
            if len(parts) == 2:
                result.setdefault("mounts", []).append(
                    {
                        "source": parts[0],
                        "target": parts[1],
                        "type": "ro-bind-try",
                    }
                )

        # Override overlays with --overlay-src / --overlay / --ro-overlay /
        # --tmp-overlay. Like bwrap, --overlay-src flags apply to the next
        # overlay flag that follows them on the command line. The ordered
        # list of (op, value) pairs is collected by the CLI's
        # _OverlayOpAction, because argparse per-flag lists lose the
        # interleaving order between flags.
        overlay_ops = getattr(args, "_overlay_ops", None) or []
        if overlay_ops:
            pending_srcs: list[str] = []
            for op_name, value in overlay_ops:
                if op_name == "overlay-src":
                    pending_srcs.append(value)
                    continue
                parts = value.split(":")
                if op_name == "overlay":
                    if len(parts) != 3:
                        raise ConfigValidationError(f"--overlay expects SRC:WORKDIR:DEST, got '{value}'")
                    mount_dict = {
                        "type": "overlay",
                        "source": parts[0],
                        "workdir": parts[1],
                        "target": parts[2],
                        "overlay_src": list(pending_srcs),
                    }
                elif op_name in ("ro-overlay", "tmp-overlay"):
                    if len(parts) != 1:
                        raise ConfigValidationError(f"--{op_name} expects DEST, got '{value}'")
                    mount_dict = {
                        "type": op_name,
                        "target": parts[0],
                        "overlay_src": list(pending_srcs),
                    }
                else:
                    raise ConfigValidationError(f"unknown overlay operation '{op_name}'")
                result.setdefault("mounts", []).append(mount_dict)
                pending_srcs = []
            if pending_srcs:
                raise ConfigValidationError(
                    f"{len(pending_srcs)} --overlay-src value(s) have no following --overlay/--ro-overlay/--tmp-overlay to attach to"
                )

        # Override capabilities with --cap-add / --cap-drop
        for cap in getattr(args, "cap_add", []):
            if "kept" not in result.get("capabilities", {}):
                result.setdefault("capabilities", {})["kept"] = []
            result["capabilities"]["kept"].append(cap)

        for cap in getattr(args, "cap_drop", []):
            if "dropped" not in result.get("capabilities", {}):
                result.setdefault("capabilities", {})["dropped"] = []
            result["capabilities"]["dropped"].append(cap)

        # Override unshare with --unshare (comma-separated list)
        for ns_str in getattr(args, "unshare", []):
            ns_str = ns_str.strip()
            namespaces = [ns.strip().lower() for ns in ns_str.split(",")]
            for ns in namespaces:
                if ns and "unshare" not in result:
                    result["unshare"] = []
                if ns not in result["unshare"]:
                    result["unshare"].append(ns)

        # Override env with --env
        for env in getattr(args, "env", []):
            if "=" in env:
                key, value = env.split("=", 1)
                result.setdefault("env_vars", {})[key] = value

        # Override unset-env with --unset-env (comma-separated list of variable names)
        for var in getattr(args, "unset_env", []):
            result.setdefault("unset_vars", "")
            result["unset_vars"] += f", {var}"

        return result

    def load_with_overrides(self, args: argparse.Namespace, path: str | None = None) -> SandboxConfig:
        """Load configuration from a YAML file with CLI overrides applied.

        This is the unified API that handles the entire configuration loading
        pipeline: file discovery, file validation, CLI override parsing, merging,
        and final validation — all in one step.

        The validation pipeline ensures correctness at multiple layers:

            1. File structure validation — checks that the YAML file is well-formed
               and matches the canonical schema.
            2. CLI override parsing — safely parses CLI arguments into override dict.
            3. Merged config validation — the merged configuration is parsed and
               validated through the same pipeline as a pure config file.

        Args:
            args: Parsed CLI arguments namespace. The method will use
                   `args.config` if provided, otherwise the default bundled config.
            path: Optional explicit config path. If provided, overrides both
                   `args.config` and any stored path.

        Returns:
            A fully validated SandboxConfig ready for use.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ConfigValidationError: If the config structure or merged result is invalid.
        """
        # Resolve the config file path: CLI arg > explicit path > default (XDG-compliant)
        if hasattr(args, "config") and args.config:
            config_path = args.config
        elif path is not None:
            config_path = path
        else:
            config_path = get_config_path()

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")

        # Parse YAML into raw dict
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # --- Validation Layer 1: File Structure ---
        self._validate_structure(raw)

        # --- Override Parsing ---
        overrides = self._merge_cli_overrides(raw, args)

        # --- Validation Layer 2: Merged Config ---
        # The set() call performs _validate_structure + _parse_config + full validation
        return self.set(overrides)

    def _parse_config(self, data: dict[str, Any]) -> SandboxConfig:
        """Parse raw config data into a SandboxConfig.

        The parsing pipeline:
          1. Expand environment variables (${VAR}, ${VAR:-default})
          2. Validate structure against canonical schema
          3. Parse mounts, capabilities, unshare, orchestration into dataclasses
          4. Return fully-validated SandboxConfig

        Raises:
            ConfigValidationError: If the data does not match the canonical schema.
            ValueError: If mount types are invalid.
        """
        # Expand environment variables (supports ${VAR} and ${VAR:-default} syntax)
        data = self._expand_env_vars(data)

        # Validate field presence and types (strict, no coercion)
        self._validate_structure(data)

        # Build dataclasses from validated data
        mounts = self._parse_mounts(data.get("mounts", []))
        mounts = self._ensure_run_mount(mounts)
        capabilities = self._parse_capabilities(data.get("capabilities", {}))
        unshare = self._parse_unshare(data.get("unshare", {}))
        orchestration = self._parse_orchestration(data.get("orchestration", []))
        secrets = self._parse_secrets(data.get("secrets", []))
        env_vars = data.get("env_vars", {})
        unset_vars = self._parse_unset_vars(data.get("unset_vars"))
        hostname = data.get("hostname")
        timeout = data.get("timeout")

        return SandboxConfig(
            name=data["name"],
            chdir=data.get("chdir"),
            mounts=mounts,
            capabilities=capabilities,
            unshare=unshare,
            orchestration=orchestration,
            secrets=secrets,
            die_with_parent=data.get("die_with_parent", True),
            new_session=data.get("new_session", True),
            hostname=hostname,
            timeout=timeout,
            env_vars=env_vars,
            unset_vars=unset_vars,
        )

    def _validate_structure(self, data: dict[str, Any]) -> None:
        """Validate that the config data matches the canonical schema.

        This is the **first line of defense** — it ensures the input is a
        well-formed dictionary with all required fields present and of the
        correct type before any parsing or transformation occurs.

        Without this gate, downstream parsers would receive malformed data
        and produce cryptic errors or silently accept invalid configurations.

        Validates:
          - Required fields are present (e.g., `name`)
          - All keys are from the known schema (rejects unknown fields)
          - Each field has the expected type (str, list[dict], dict, bool, etc.)
          - Mount entries are dictionaries (not scalars)

        Note: This does NOT validate the CONTENT of fields (e.g., mount type
        strings, capability names). That is handled by the parsers below.

        Args:
            data: Raw configuration dictionary (as parsed from YAML/JSON).

        Raises:
            ConfigValidationError: If any field is missing, has wrong type,
                or contains unknown keys.
        """
        for required in ("name",):
            if required not in data:
                raise ConfigValidationError(f"{required} cannot be empty")

        valid_keys = set(self._FIELDS.keys())
        for key in data:
            if key not in valid_keys:
                raise ConfigValidationError(f"unknown key '{key}'")

        for field_name, expected_type in self._FIELDS.items():
            # Get the value for this field
            if field_name in ("env_vars",):
                # env_vars is always a dict (even if empty)
                value = data.get(field_name)
                if value is None:
                    value = {}
            elif field_name in ("capabilities", "unshare"):
                # These fields use nested dicts
                value = data.get(field_name, {})
            elif field_name == "unset_vars":
                # unset_vars is an optional string (can be empty or None)
                value = data.get(field_name)
            elif field_name in ("mounts", "orchestration", "secrets"):
                # These default to an empty list when not present
                value = data.get(field_name, [])
            elif field_name == "hostname":
                value = data.get("hostname")
            elif field_name == "timeout":
                value = data.get("timeout")
            elif field_name == "die_with_parent":
                value = data.get("die_with_parent", True)
            elif field_name == "new_session":
                value = data.get("new_session", True)
            else:
                value = data.get(field_name)

            # Handle optional fields that are None
            if value is None and expected_type in self._OPTIONAL_TYPES:
                # Optional fields are allowed to be None
                continue

            if expected_type == "str":
                if not isinstance(value, str):
                    raise ConfigValidationError(f"Field '{field_name}' must be a string, got {type(value).__name__}. Value: {value!r}")
            elif expected_type == "list[dict]":
                if not isinstance(value, list):
                    raise ConfigValidationError(f"Field '{field_name}' must be a list of dicts, got {type(value).__name__}")
                for i, m in enumerate(value):
                    if not isinstance(m, dict):
                        raise ConfigValidationError(f"{field_name}[{i}] must be a dict, got {type(m).__name__}")
            elif expected_type == "dict":
                if not isinstance(value, dict):
                    raise ConfigValidationError(f"Field '{field_name}' must be a dict, got {type(value).__name__}")
            elif expected_type == "bool":
                if not isinstance(value, bool):
                    raise ConfigValidationError(f"Field '{field_name}' must be a boolean, got {type(value).__name__}")
            elif expected_type == "int":
                if not isinstance(value, int):
                    raise ConfigValidationError(f"Field '{field_name}' must be an integer, got {type(value).__name__}")
            elif expected_type in self._OPTIONAL_TYPES:
                # Optional fields - already handled above with continue
                pass
            elif expected_type == "dict[str, str]":
                # Explicit check for dict[str, str] type (e.g., env_vars, unset_vars)
                if not isinstance(value, dict):
                    raise ConfigValidationError(f"Field '{field_name}' must be a dict, got {type(value).__name__}")
            elif expected_type == "list[str]":
                # For backward compatibility, keep this case
                if not isinstance(value, list):
                    raise ConfigValidationError(f"Field '{field_name}' must be a list of strings, got {type(value).__name__}")
                for i, item in enumerate(value):
                    if not isinstance(item, str):
                        raise ConfigValidationError(f"Field '{field_name}'[{i}] must be a string, got {type(item).__name__}: {item!r}")
            else:
                # Unknown expected_type - this should not happen
                raise ConfigValidationError(f"Unknown expected type '{expected_type}' for field '{field_name}'")

    def _parse_mounts(self, mounts: list[dict]) -> list[Mount]:
        """Parse mounts from a list of dicts into Mount dataclass instances.

        This is **Layer 2** of the validation pipeline. It performs two functions:
        1. **Transformation**: Converts user-friendly YAML dicts into Mount dataclasses.
        2. **Validation**: Ensures mount type strings are valid and required fields
           are present for each mount type.

        Why this is needed even though _validate_structure() checks the top-level
        type:

          - _validate_structure() only verifies that mounts is a *list of dicts*.
          - It does NOT verify that each dict has the correct *shape* for its type.
          - _parse_mounts() enforces the semantic rules:
              * A 'tmpfs' mount MUST have a 'target' field.
              * A 'bind' mount MUST have both 'source' and 'target' fields.
              * Mount type strings must be in the allowed enum.
              * The 'size' field must be a valid size string (or empty).
              * 'create-source' must be a boolean and is only valid for
                bind/ro-bind/dev-bind mounts.
              * 'create-as' must be 'dir' or 'file' and is only valid when
                'create-source' is true.
              * 'overlay-src' must be a list of non-empty strings and is only
                valid for overlay/ro-overlay/tmp-overlay mounts.
              * 'workdir' must be a non-empty string and is only valid for
                overlay/ro-overlay/tmp-overlay mounts.
              * 'source' is not valid for ro-overlay/tmp-overlay mounts.

        Without this layer, a user could specify:
            mounts:
              - target: /tmp
                type: tmpfs
          and the parser would silently create a Mount with source=None,
          leading to subtle runtime errors or incorrect behavior.

        Args:
            mounts: List of mount dictionaries from the config.

        Returns:
            A list of Mount dataclass instances.

        Raises:
            ConfigValidationError: If a mount is missing required fields.
            ValueError: If a mount type string is not recognized.
        """
        validated_mounts: list[Mount] = []
        for i, mount_dict in enumerate(mounts):
            mount_type = mount_dict.get("type")
            if not isinstance(mount_type, str):
                raise ConfigValidationError(f"mount type must be a string, got {type(mount_type).__name__}: {mount_type!r}")
            if mount_type not in self._VALID_MOUNT_TYPES:
                raise ConfigValidationError(f"unknown mount type '{mount_type}'")

            # Translate hyphenated YAML keys to dataclass field names and
            # validate the create-source/create-as options.
            mount = dict(mount_dict)
            if "create-source" in mount or "create-as" in mount:
                if mount_type not in self._SOURCE_MOUNT_TYPES:
                    raise ConfigValidationError(
                        f"mount[{i}]: 'create-source'/'create-as' are only valid for bind, ro-bind and dev-bind mounts, "
                        f"got type '{mount_type}'"
                    )
            if "create-source" in mount:
                create_source = mount.pop("create-source")
                if not isinstance(create_source, bool):
                    raise ConfigValidationError(
                        f"mount[{i}]: 'create-source' must be a boolean (true/false), "
                        f"got {type(create_source).__name__}: {create_source!r}"
                    )
                mount["create_source"] = create_source
            if "create-as" in mount:
                create_as = mount.pop("create-as")
                if not isinstance(create_as, str) or create_as not in self._VALID_CREATE_AS:
                    raise ConfigValidationError(f"mount[{i}]: 'create-as' must be 'dir' or 'file', got {create_as!r}")
                if mount.get("create_source") is not True:
                    raise ConfigValidationError(f"mount[{i}]: 'create-as' is only valid when 'create-source' is true")
                mount["create_as"] = create_as

            # Translate hyphenated overlay keys and validate the overlay
            # options (scope: only overlay-family types may use them).
            if "overlay-src" in mount or "workdir" in mount:
                if mount_type not in self._OVERLAY_MOUNT_TYPES:
                    raise ConfigValidationError(
                        f"mount[{i}]: 'overlay-src'/'workdir' are only valid for overlay, ro-overlay and tmp-overlay mounts, "
                        f"got type '{mount_type}'"
                    )
            if "overlay-src" in mount:
                mount["overlay_src"] = mount.pop("overlay-src")
            if "overlay_src" in mount:
                overlay_src = mount["overlay_src"]
                if not isinstance(overlay_src, list) or not all(isinstance(s, str) and s for s in overlay_src):
                    raise ConfigValidationError(f"mount[{i}]: 'overlay-src' must be a list of non-empty strings")
            if "workdir" in mount:
                if not isinstance(mount["workdir"], str) or not mount["workdir"]:
                    raise ConfigValidationError(f"mount[{i}]: 'workdir' must be a non-empty string")
            if mount_type in ("ro-overlay", "tmp-overlay") and "source" in mount:
                raise ConfigValidationError(f"mount[{i}]: 'source' is not valid for '{mount_type}' mounts; use 'overlay-src' layers")

            validated_mounts.append(Mount(**mount))
        return validated_mounts

    def _ensure_run_mount(self, mounts: list[Mount]) -> list[Mount]:
        """Ensure a writable tmpfs /run exists for the stdio socket/stub wiring.

        This is a **post-Layer-2 enforcement step**. It runs after
        :meth:`_parse_mounts`, before Layer 3, on the parsed mount list.

        Why this is needed:

          bwrap *creates* its bind-mount targets itself. The runtime wiring
          (``BwrapBuilder.build``) bind-mounts every generated track-socket
          into ``/run`` and unconditionally bind-mounts the sandbox-side stub
          at ``/run/stdio_stub.py`` on **every** run. bwrap must be able to
          create those targets, so ``/run`` has to be writable inside the
          sandbox. A ``--ro-bind / /`` (the common "reuse the host image"
          mount) recursively makes the whole ``/`` subtree — including
          ``/run`` — read-only inside the sandbox, so target creation fails
          with EROFS ("Read-only file system"). The socket's *data* is
          created on the host and bind-mounted in: ``/run`` is not where the
          socket data is written, it just has to be a writable mount point to
          receive the bind. A writable sub-path alternative does not help:
          every bind-mount target needs a writable parent, and ``/`` is
          read-only, so a tmpfs ``/run`` is required.

        Design: a writable tmpfs ``/run`` is non-optional infrastructure, not
        a user-facing option. The user's only allowed tweak to a ``/run``
        mount is its ``size``:

          - ``/run`` absent (and the user gave >= 1 mount) -> append a
            tmpfs ``/run`` *after* the user's mounts, so the topological
            sort (parents before children) still places it after ``/``.
          - ``/run`` present as tmpfs -> kept as-is; any user-set ``size`` is
            honored.
          - ``/run`` present as any non-tmpfs type (bind / ro-bind /
            ro-overlay / dir / ...) -> rejected with ``ConfigValidationError``.
          - ``/run`` absent and the user gave zero mounts -> returned
            unchanged, so the Layer-6 gate (at least one mount) still rejects
            the config.

        Args:
            mounts: The parsed mount list (possibly empty; already env- and
                hyphenated-key-normalized by :meth:`_parse_mounts`).

        Returns:
            The same list (mutated copy) with a tmpfs ``/run`` ensured.

        Raises:
            ConfigValidationError: If a user-provided ``/run`` mount is not a
                tmpfs mount.
        """
        if not mounts:
            # No mounts at all: leave the list empty so the Layer-6 gate
            # (at least one mount) rejects the config, not this step.
            return mounts

        run_mount = next((m for m in mounts if m.target == "/run"), None)
        if run_mount is not None:
            if run_mount.type != "tmpfs":
                raise ConfigValidationError(
                    f"mount: /run must be a tmpfs mount (got type '{run_mount.type}'); "
                    "/run must be writable so bwrap can create the stdio socket "
                    "and stdio stub bind-mount targets. Only a tmpfs type is "
                    "accepted — you may still set its 'size'."
                )
            # tmpfs /run is correct; honor any user-set size.
            return mounts

        # /run absent: append a tmpfs /run after the user's mounts so it ends
        # up after / in the topologically-sorted bwrap command line.
        return [*mounts, Mount(type="tmpfs", target="/run")]

    def _parse_capabilities(self, caps: dict[str, Any]) -> CapabilitySet:
        """Parse capabilities from a dict into a CapabilitySet dataclass.

        This is **Layer 3** of the validation pipeline. It validates and
        transforms the `capabilities` field, which can have several valid forms:

          ```yaml
          capabilities:
            drop: ["ALL"]
            keep: ["CAP_CHOWN", "CAP_NET_BIND_SERVICE"]
          ```

          or

          ```yaml
          capabilities:
            drop:
              - ALL
              - CAP_SYS_ADMIN
            keep: ["CAP_NET_BIND_SERVICE"]
          ```

        Why this is needed:

          - The raw config may use lists, comma-separated strings, or dicts.
          - _validate_structure() ensures the field is a dict, but not what's
            inside the `drop` and `keep` keys.
          - This layer ensures `drop` and `keep` are lists of strings (not
            dicts, ints, or nested structures), and converts them into a
            normalized internal representation.

        Args:
            caps: A dict with optional "drop" and "keep" keys, each mapping
                  to a list of capability strings.

        Returns:
            A CapabilitySet dataclass instance with normalized drop/keep lists.

        Raises:
            ConfigValidationError: If drop/keep are not lists of strings.
        """
        dropped: list[str] = []
        kept: list[str] = []

        if "drop" in caps:
            if not isinstance(caps["drop"], list):
                raise ConfigValidationError(
                    f"capabilities.drop must be a list, got {type(caps['drop']).__name__}. Use: drop: ['ALL'] or drop: []"
                )
            for i, item in enumerate(caps["drop"]):
                if not isinstance(item, str):
                    raise ConfigValidationError(f"capabilities.drop[{i}] must be a string, got {type(item).__name__}: {item!r}")
            dropped = list(caps["drop"])

        if "keep" in caps:
            if not isinstance(caps["keep"], list):
                raise ConfigValidationError(
                    f"capabilities.keep must be a list, got {type(caps['keep']).__name__}. Use: keep: ['CAP_CHOWN']"
                )
            for i, item in enumerate(caps["keep"]):
                if not isinstance(item, str):
                    raise ConfigValidationError(f"capabilities.keep[{i}] must be a string, got {type(item).__name__}: {item!r}")
            kept = list(caps["keep"])

        return CapabilitySet(dropped=dropped, kept=kept)

    def _parse_unshare(self, unshare: dict[str, bool]) -> NamespaceSet:
        """Parse unshare settings from a dict into a NamespaceSet dataclass.

        This is **Layer 4** of the validation pipeline. It validates and
        transforms the `unshare` field, which can have several valid forms:

          ```yaml
          unshare:
            pid: true
            uts: true
            ipc: false
            cgroup: false
            user: false
            network: false
          ```

          or

          ```yaml
          unshare:
            pid: true
            network: true
          ```

        Why this is needed:

          - The raw config may mix booleans with strings ("true"/"false"),
            or use comma-separated values like "pid, uts, network".
          - _validate_structure() ensures the field is a dict, but not the
            content of its values.
          - This layer ensures:
              * Keys are valid namespace names (pid, uts, ipc, cgroup, user, network)
              * Values are booleans (not strings, ints, etc.)
              * It normalizes to a clean NamespaceSet dataclass

        Without this layer, a malformed config like:
            unshare:
              pid: "true"
              uts: 1
          could silently produce incorrect namespace isolation.

        Args:
            unshare: A dict mapping namespace names to boolean values.

        Returns:
            A NamespaceSet dataclass instance.

        Raises:
            ConfigValidationError: If a key is invalid or a value is not a boolean.
        """
        namespace_dict: dict[str, bool] = {}

        if not isinstance(unshare, dict):
            raise ConfigValidationError(
                f"unshare must be a dict mapping namespace names to booleans. Got {type(unshare).__name__}: {unshare!r}"
            )

        for key, value in unshare.items():
            if key not in {"pid", "uts", "ipc", "cgroup", "user", "network"}:
                raise ConfigValidationError(f"unshare key '{key}' is not valid. Valid keys: pid, uts, ipc, cgroup, user, network")
            if not isinstance(value, bool):
                raise ConfigValidationError(f"unshare.{key} must be a boolean (true/false), got {type(value).__name__}: {value!r}")
            namespace_dict[key] = bool(value)

        return NamespaceSet(**namespace_dict)

    def _parse_orchestration(self, entries: list[dict]) -> list[OrchestrationEntry]:
        """Parse orchestration entries into OrchestrationEntry dataclasses.

        This is **Layer 5** of the validation pipeline. It validates and
        transforms the `orchestration` field:

          ```yaml
          orchestration:
            - command: "stdio-bridge --listen ${STDIO_SOCKET_PATH} -- node index.js"
              track-socket: true
              wait-timeout: 30
              keep: false
          ```

        Why this is needed:

          - _validate_structure() ensures the field is a list of dicts, but
            not the content of each entry.
          - This layer ensures per entry:
              * `command` is a non-empty string that tokenizes (with
                `shlex.split`, so quoting and arguments can be written
                naturally in the YAML) into a non-empty argv list (no shell
                is involved at execution time).
              * `track-socket` (optional) is a boolean (default false); when
                true, agent-nook generates a unique socket name at run time
                and wires it into the command and the sandbox (see
                OrchestrationEntry).
              * `wait-timeout` (optional) is a positive number (default 30)
              * `keep` (optional) is a boolean (default false)
              * unknown keys are rejected (strict schema, consistent with
                the rest of the loader)

        Args:
            entries: A list of raw orchestration entry dicts (already
                env-expanded by _expand_env_vars). Each entry's `command`
                is a non-empty string that is tokenized into an argv list
                with `shlex.split`.

        Returns:
            A list of OrchestrationEntry dataclass instances.

        Raises:
            ConfigValidationError: If any entry is malformed.
        """
        parsed: list[OrchestrationEntry] = []
        known_keys = ("command", "track-socket", "wait-timeout", "keep")

        for i, entry in enumerate(entries):
            for key in entry:
                if key not in known_keys:
                    raise ConfigValidationError(f"orchestration[{i}] has unknown key '{key}'. Known keys: {', '.join(known_keys)}")

            command = entry.get("command")
            argv = self._parse_command(command, i, hint=_STDIO_BRIDGE_HINT)

            track_socket = entry.get("track-socket", False)
            if not isinstance(track_socket, bool):
                raise ConfigValidationError(
                    f"orchestration[{i}].track-socket must be a boolean, got {type(track_socket).__name__}: {track_socket!r}"
                )

            wait_timeout: int | float = 30
            if "wait-timeout" in entry:
                wait_timeout = entry["wait-timeout"]
                if isinstance(wait_timeout, bool) or not isinstance(wait_timeout, (int, float)) or wait_timeout <= 0:
                    raise ConfigValidationError(f"orchestration[{i}].wait-timeout must be a positive number, got {wait_timeout!r}")

            keep = entry.get("keep", False)
            if not isinstance(keep, bool):
                raise ConfigValidationError(f"orchestration[{i}].keep must be a boolean, got {type(keep).__name__}: {keep!r}")

            parsed.append(OrchestrationEntry(command=argv, track_socket=track_socket, wait_timeout=wait_timeout, keep=keep))

        return parsed

    @staticmethod
    def _parse_command(
        command: Any,
        index: int,
        *,
        label: str = "orchestration",
        hint: str | None = None,
    ) -> list[str]:
        """Tokenize a single-command string into an argv list (no shell involved).

        This is the single source of truth for turning a config's single
        command string into the argument list the caller passes to
        ``subprocess`` (which does not invoke a shell). It validates that the
        command is a non-empty string and that it tokenizes into at least one
        non-empty argument; a malformed shell tokenization (e.g. an
        unterminated quote) surfaces as a ``ConfigValidationError``.

        Args:
            command: The raw command value of the calling section's
                ``[index]`` entry (already env-expanded by _expand_env_vars).
            index: The entry index, for the error message.
            label: The section name in error messages (``orchestration`` or
                ``secrets``).
            hint: Optional usage hint appended to the "non-empty string"
                errors; only the orchestration section carries the stdio-bridge
                example.

        Returns:
            A non-empty list of argument strings.

        Raises:
            ConfigValidationError: If `command` is not a non-empty string,
                tokenizes into no arguments, or raises a shell error.
        """
        if not isinstance(command, str) or not command:
            msg = f"{label}[{index}].command must be a non-empty string (no shell)"
            if hint:
                msg += f" Use: {hint}"
            raise ConfigValidationError(msg)
        try:
            argv = shlex.split(command)
        except ValueError as e:
            raise ConfigValidationError(f"{label}[{index}].command could not be parsed as a shell command: {e}") from e
        if not argv:
            msg = f"{label}[{index}].command must be a non-empty string (no shell)"
            if hint:
                msg += f" Use: {hint}"
            raise ConfigValidationError(msg)
        return argv

    def _parse_secrets(self, entries: list[dict]) -> list[Secret]:
        """Parse secret entries into Secret dataclasses.

        This is **Layer 5b** of the validation pipeline. It validates and
        transforms the `secrets` field:

          ```yaml
          secrets:
            - name: gh-token          # required; -> /run/secrets/gh-token
              command: "gh auth token"  # host command; stdout = secret
              #file: ~/.config/gh/hosts.yml
              env: GH_TOKEN             # optional; export inside sandbox as GH_TOKEN
          ```

        Why this is needed:

          - _validate_structure() ensures the field is a *list of dicts*, but
            not the content of each entry.
          - This layer ensures per entry:
              * `name` is a non-empty string; any path components are stripped
                (``Path(name).name``) so a hostile name cannot escape
                ``/run/secrets/<name>``.
              * exactly one of `file` / `command` is present (both or neither →
                error); `file` is a non-empty host path, `command` is a non-empty
                string tokenized with `shlex.split` (no shell is involved at
                execution time — the sandbox runs the resulting argv via
                ``subprocess``).
              * `env` (optional) is a non-empty string; when absent the secret is
                file-only (no env export).
              * unknown keys are rejected (strict schema, consistent with the
                rest of the loader).

        Args:
            entries: A list of raw secret entry dicts (already env-expanded).

        Returns:
            A list of Secret dataclass instances.

        Raises:
            ConfigValidationError: If any entry is malformed.
        """
        parsed: list[Secret] = []
        known_keys = ("name", "file", "command", "env")

        for i, entry in enumerate(entries):
            for key in entry:
                if key not in known_keys:
                    raise ConfigValidationError(f"secrets[{i}] has unknown key '{key}'. Known keys: {', '.join(known_keys)}")

            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ConfigValidationError(f"secrets[{i}].name must be a non-empty string")
            # Strip any path components -> filename-safe; mirrors socket_factory.
            name = Path(name).name

            has_file = entry.get("file") is not None
            has_command = entry.get("command") is not None
            if has_file and has_command:
                raise ConfigValidationError(f"secrets[{i}] specifies both 'file' and 'command'; only one is allowed")
            if not has_file and not has_command:
                raise ConfigValidationError(f"secrets[{i}] must specify exactly one of 'file' or 'command'")

            source_file: str | None = None
            source_command: list[str] | None = None
            if has_command:
                source_command = self._parse_command(entry["command"], i, label="secrets")
            else:
                source_file = entry["file"]
                if not isinstance(source_file, str) or not source_file:
                    raise ConfigValidationError(f"secrets[{i}].file must be a non-empty string")

            env_var = entry.get("env")
            if env_var is not None:
                if not isinstance(env_var, str) or not env_var:
                    raise ConfigValidationError(f"secrets[{i}].env must be a non-empty string")

            parsed.append(Secret(name=name, source_file=source_file, source_command=source_command, env_var=env_var))

        return parsed

    def _expand_env_vars(self, data: dict[str, Any]) -> dict[str, Any]:
        """Expand environment variables in string values.

        Supports:
          - ${VAR} - Expand to value of VAR
          - ${VAR:-default} - Expand to default if VAR is unset or empty
          - ${VAR:-${OTHER}} - Nested defaults (single level only)

        This is called during config parsing, before validation, so that
        environment variables in mount sources, targets, and other string
        fields are expanded automatically.

        Undefined variables are left as-is (literal ${VAR} remains).

        As a special case, agent-nook's own STDIO_SOCKET_* placeholders are
        always preserved verbatim (the orchestrator substitutes them at
        runtime), regardless of any host environment value for those vars.

        Args:
            data: Raw configuration dictionary.

        Returns:
            New dictionary with environment variables expanded.
        """

        def _expand_value(value: Any) -> Any:
            """Recursively expand environment variables in a value."""
            if isinstance(value, str):
                # Custom expansion that supports both ${VAR} and ${VAR:-default}
                return self._custom_expand_env_vars(value)
            elif isinstance(value, dict):
                return {k: _expand_value(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [_expand_value(item) for item in value]
            return value

        return {k: _expand_value(v) for k, v in data.items()}

    def _custom_expand_env_vars(self, value: str) -> str:
        """Custom environment variable expansion supporting :- default syntax.

        Expands ${VAR} and ${VAR:-default} patterns in a string.
        Returns the expanded value or the original string if no matches.

        Reserved placeholders named STDIO_SOCKET_* (e.g. STDIO_SOCKET_PATH) are
        left literal here: the orchestrator substitutes them at runtime, so host
        environment values must not leak in during config parsing.

        Args:
            value: Input string to expand.

        Returns:
            String with environment variables expanded.

        """
        if not value or not isinstance(value, str):
            return value

        result: list[str] = []
        # Pattern to match ${VAR} or ${VAR:-default}
        # Groups: 1=full match, 2=var name, 3=default value (optional)
        pattern = re.compile(r"\$\{([^:}]+)(?::-([^}]*))?\}")

        # Use finditer with match tracking to handle consecutive variables
        last_end = 0
        for match in pattern.finditer(value):
            # Append text between last match and this match
            if match.start() > last_end:
                result.append(value[last_end : match.start()])

            var_name = match.group(1)
            default_value = match.group(2)

            # agent-nook's own runtime placeholders are filled by the orchestrator
            # (orchestrator.substitute_placeholders), never by the loader. Leave
            # them literal regardless of whether they are set in the host env, so
            # config parsing is unaffected by the sandbox's STDIO_SOCKET_* vars.
            if var_name.startswith("STDIO_SOCKET_"):
                result.append(match.group(0))
                last_end = match.end()
                continue

            # Get environment variable or use default
            env_value = os.environ.get(var_name)

            if env_value is not None and env_value != "":
                # Variable is set and non-empty, use its value
                result.append(env_value)
            # Variable is unset or empty, use default or literal ${var}
            elif default_value is not None:
                result.append(default_value)
            else:
                # No default provided, keep literal
                result.append(match.group(0))

            # Update last_end to current match end
            last_end = match.end()

        # Append any remaining text after the last match
        if last_end < len(value):
            result.append(value[last_end:])

        return "".join(result)

    def _parse_unset_vars(self, unset_vars: str | None) -> str:
        """Parse unset_vars string into a normalized string.

        The unset_vars field can be specified as:
          - A whitespace-separated list: "VAR1 VAR2 VAR3" (newlines, spaces, tabs)
          - The special keyword "ALL" to clear all environment variables

        This method normalizes the input to a clean string with space-separated values.

        Args:
            unset_vars: Optional string of variable names (whitespace-separated) or "ALL".

        Returns:
            Normalized string with space-separated variable names, or empty string.
        """
        if unset_vars is None:
            return ""

        if not isinstance(unset_vars, str):
            raise ConfigValidationError(f"unset_vars must be a string, got {type(unset_vars).__name__}")

        # Split on any whitespace (newlines, spaces, tabs) - handles:
        # "VAR1\nVAR2" or "VAR1 VAR2" or "VAR1\tVAR2"
        vars_list = [v.strip() for v in unset_vars.split()]
        # Remove empty strings (from consecutive whitespace)
        vars_list = [v for v in vars_list if v]

        # Check for "ALL" keyword (case-sensitive, exact match)
        if vars_list == ["ALL"]:
            return "ALL"

        # Join with space (single space, no trailing/leading whitespace)
        return " ".join(vars_list) if vars_list else ""

    def load(self, path: str | None = None) -> SandboxConfig:
        """Load and validate configuration from a YAML file.

        Args:
            path: Optional path override. If None, uses the default config path
                (from XDG_CONFIG_HOME/agent-nook/sandbox.yaml).

        Returns:
            A validated SandboxConfig ready for use.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ConfigValidationError: If the config structure is invalid.
        """
        from os import path as os_path

        if not os_path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")

        # Parse YAML into raw dict
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        # Validate structure (single gate — no legacy normalization)
        self._validate_structure(raw)

        # Build config
        config = self._parse_config(raw)

        # Full validation
        config.validate()

        return config

    def load_from_dict(self, data: dict[str, Any]) -> SandboxConfig:
        """Load and validate configuration from a dict (for testing).

        Args:
            data: Raw configuration dictionary.

        Returns:
            A validated SandboxConfig instance.

        Raises:
            ConfigValidationError: If the config structure is invalid.
        """
        self._validate_structure(data)

        config = self._parse_config(data)
        config.validate()

        return config
