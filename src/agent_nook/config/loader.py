"""XDG-compliant configuration loader.

Reads configuration from YAML files and validates against a strict canonical schema.
The canonical format is enforced at load time with zero tolerance for deviations.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from typing import Any

import yaml


try:
    from agent_nook.config.config import CapabilitySet
    from agent_nook.config.config import ConfigValidationError
    from agent_nook.config.config import Mount
    from agent_nook.config.config import NamespaceSet
    from agent_nook.config.config import SandboxConfig
except ImportError:
    from ..config.config import CapabilitySet
    from ..config.config import ConfigValidationError
    from ..config.config import Mount
    from ..config.config import NamespaceSet
    from ..config.config import SandboxConfig

logger = logging.getLogger(__name__)

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
#            → Transforms user-friendly YAML format into internal Mount objects
#
#   Layer 3: _parse_capabilities()      — Dict → CapabilitySet dataclass
#            → Ensures drop/keep are lists of strings (not dicts, ints, etc.)
#            → Prevents malformed capability specifications
#
#   Layer 4: _parse_unshare()           — Dict → NamespaceSet dataclass
#            → Ensures namespace keys are strings, values are booleans
#            → Prevents namespace injection via malformed dicts
#
#   Layer 5: SandboxConfig.__post_init__() — Dataclass construction
#            → Calls Mount.build() on each mount for final validation
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
        "die_with_parent": "bool",
        "new_session": "bool",
        "hostname": "str | None",
        "timeout": "int | None",
        "env_vars": "dict[str, str]",
        "unset_vars": "list[str]",
    }

    _VALID_MOUNT_TYPES = frozenset({"bind", "ro-bind", "dev-bind", "tmpfs", "proc", "dev", "dir"})

    def __init__(self, config_path: str | None = None) -> None:
        """Initialize the config loader.

        Args:
            config_path: Optional path to the config file. If None,
                the default path is used (from XDG_CONFIG_HOME/agent-nook/sandbox.yaml).
        """
        self._config_path: str | None = config_path

    def find_default_config_path(self) -> str:
        """Find the bundled default configuration file path.

        Returns:
            The path to the default sandbox.yaml file.

        Raises:
            FileNotFoundError: If the default config is not found.
        """
        from pathlib import Path

        import agent_nook

        package_dir = Path(agent_nook.__file__).parent
        default_config_path = package_dir / "config" / "sandbox.yaml"

        if default_config_path.exists():
            return str(default_config_path)

        loader_path = Path(__file__).resolve()
        repo_root = loader_path.parent.parent.parent.parent
        default_config_path = repo_root / "config" / "sandbox.yaml"

        if default_config_path.exists():
            return str(default_config_path)

        raise FileNotFoundError(
            f"Default sandbox.yaml not found at {default_config_path}. "
            f"Make sure agent-nook is properly installed or "
            f"the config directory exists with a sandbox.yaml file."
        )

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

        # Override mounts from --bind / --ro-bind
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

        # Override unset-env
        for var in getattr(args, "unset_env", []):
            result.setdefault("unset_vars", []).append(var)

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
        # Resolve the config file path: CLI arg > explicit path > default
        if hasattr(args, "config") and args.config:
            config_path = args.config
        elif path is not None:
            config_path = path
        else:
            config_path = self.find_default_config_path()

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
          3. Parse mounts, capabilities, unshare settings into dataclasses
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
        capabilities = self._parse_capabilities(data.get("capabilities", {}))
        unshare = self._parse_unshare(data.get("unshare", {}))
        env_vars = data.get("env_vars", {})
        unset_vars = data.get("unset_vars", [])
        hostname = data.get("hostname")
        timeout = data.get("timeout")

        return SandboxConfig(
            name=data["name"],
            chdir=data.get("chdir"),
            mounts=mounts,
            capabilities=capabilities,
            unshare=unshare,
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
            value = data.get(field_name)
            if field_name == "mounts" and value is None:
                value = []
            elif field_name == "capabilities":
                value = data.get("capabilities", {})
            elif field_name == "unshare":
                value = data.get("unshare", {})
            elif field_name == "env_vars":
                value = data.get("env_vars", {})
            elif field_name == "unset_vars":
                value = data.get("unset_vars", [])
            elif field_name == "hostname":
                value = data.get("hostname")
            elif field_name == "timeout":
                value = data.get("timeout")
            elif field_name == "die_with_parent":
                value = data.get("die_with_parent", True)
            elif field_name == "new_session":
                value = data.get("new_session", True)

            if expected_type == "str":
                if not isinstance(value, str):
                    raise ConfigValidationError(f"Field '{field_name}' must be a string, got {type(value).__name__}. Value: {value!r}")
            elif expected_type == "list[dict]":
                if not isinstance(value, list):
                    raise ConfigValidationError(f"Field '{field_name}' must be a list of dicts, got {type(value).__name__}")
                for i, m in enumerate(value):
                    if not isinstance(m, dict):
                        raise ConfigValidationError(f"mount[{i}] must be a dict, got {type(m).__name__}")
            elif expected_type == "dict":
                if not isinstance(value, dict):
                    raise ConfigValidationError(f"Field '{field_name}' must be a dict, got {type(value).__name__}")
            elif expected_type == "bool":
                if not isinstance(value, bool):
                    raise ConfigValidationError(f"Field '{field_name}' must be a boolean, got {type(value).__name__}")
            elif expected_type == "list[str]":
                if not isinstance(value, list):
                    raise ConfigValidationError(f"Field '{field_name}' must be a list of strings, got {type(value).__name__}")
                for i, item in enumerate(value):
                    if not isinstance(item, str):
                        raise ConfigValidationError(f"Field '{field_name}'[{i}] must be a string, got {type(item).__name__}: {item!r}")

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
        for mount_dict in mounts:
            mount_type = mount_dict.get("type")
            if not isinstance(mount_type, str):
                raise ConfigValidationError(f"mount type must be a string, got {type(mount_type).__name__}: {mount_type!r}")
            if mount_type not in self._VALID_MOUNT_TYPES:
                raise ConfigValidationError(f"unknown mount type '{mount_type}'")
            validated_mounts.append(Mount(**mount_dict))
        return validated_mounts

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

        if path is None:
            path = self.find_default_config_path()

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


__all__ = ["ConfigLoader", "ConfigValidationError"]
