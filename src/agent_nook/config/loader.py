"""XDG-compliant configuration loader.

Reads configuration from YAML files and validates against a strict canonical schema.
The canonical format is enforced at load time with zero tolerance for deviations.
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

try:
    from agent_nook.config.config import (
        SandboxConfig,
        Mount,
        CapabilitySet,
        NamespaceSet,
        ConfigValidationError,
    )
except ImportError:
    from ..config.config import (
        SandboxConfig,
        Mount,
        CapabilitySet,
        NamespaceSet,
        ConfigValidationError,
    )

logger = logging.getLogger(__name__)


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
        "unenv_vars": "list[str]",
    }

    _VALID_MOUNT_TYPES = frozenset(
        {"bind", "ro-bind", "dev-bind", "tmpfs", "proc", "dev", "dir"}
    )

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
        import agent_nook
        from pathlib import Path

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

    def set_config(self, data: dict[str, Any], override: dict | None = None) -> SandboxConfig:
        """Alias for set()."""
        return self.set(data, override)

    def set_from_yaml(self, path: str) -> SandboxConfig:
        """Load configuration from a YAML file path."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return self.set(data)

    def set_from_json(self, path: str) -> SandboxConfig:
        """Load configuration from a JSON file path."""
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return self.set(data)

    def _parse_config(self, data: dict[str, Any]) -> SandboxConfig:
        """Parse raw config data into a SandboxConfig.

        Raises:
            ConfigValidationError: If the data does not match the canonical schema.
            ValueError: If mount types are invalid.
        """
        # Validate field presence and types (strict, no coercion)
        self._validate_structure(data)

        # Build dataclasses from validated data
        mounts = self._parse_mounts(data.get("mounts", []))
        capabilities = self._parse_capabilities(data.get("capabilities", {}))
        unshare = self._parse_unshare(data.get("unshare", {}))
        env_vars = data.get("env_vars", {})
        unenv_vars = data.get("unenv_vars", [])
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
            unenv_vars=unenv_vars,
        )

    def _validate_structure(self, data: dict[str, Any]) -> None:
        """Validate that the config data matches the canonical schema.

        Single gate — if it passes, all downstream code can assume canonical form.
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
            elif field_name == "unenv_vars":
                value = data.get("unenv_vars", [])
            elif field_name == "hostname":
                value = data.get("hostname", None)
            elif field_name == "timeout":
                value = data.get("timeout", None)
            elif field_name == "die_with_parent":
                value = data.get("die_with_parent", True)
            elif field_name == "new_session":
                value = data.get("new_session", True)

            if expected_type == "str":
                if not isinstance(value, str):
                    raise ConfigValidationError(
                        f"Field '{field_name}' must be a string, got "
                        f"{type(value).__name__}. Value: {value!r}"
                    )
            elif expected_type == "list[dict]":
                if not isinstance(value, list):
                    raise ConfigValidationError(
                        f"Field '{field_name}' must be a list of dicts, got "
                        f"{type(value).__name__}"
                    )
                for i, m in enumerate(value):
                    if not isinstance(m, dict):
                        raise ConfigValidationError(
                            f"mount[{i}] must be a dict, got {type(m).__name__}"
                        )
            elif expected_type == "dict":
                if not isinstance(value, dict):
                    raise ConfigValidationError(
                        f"Field '{field_name}' must be a dict, got "
                        f"{type(value).__name__}"
                    )
            elif expected_type == "bool":
                if not isinstance(value, bool):
                    raise ConfigValidationError(
                        f"Field '{field_name}' must be a boolean, got "
                        f"{type(value).__name__}"
                    )
            elif expected_type == "list[str]":
                if not isinstance(value, list):
                    raise ConfigValidationError(
                        f"Field '{field_name}' must be a list of strings, got "
                        f"{type(value).__name__}"
                    )
                for i, item in enumerate(value):
                    if not isinstance(item, str):
                        raise ConfigValidationError(
                            f"Field '{field_name}'[{i}] must be a string, "
                            f"got {type(item).__name__}: {item!r}"
                        )

    def _parse_mounts(self, mounts: list[dict]) -> list[Mount]:
        """Parse mounts from a list of dicts.

        Each mount must be a dict with exactly one of:
          - "source" and "target" (bind types)
          - or a single "target" (tmpfs, proc, dev, dir)
        """
        result: list[Mount] = []

        for i, m in enumerate(mounts):
            if not isinstance(m, dict):
                raise ConfigValidationError(
                    f"mount[{i}] must be a dict, got {type(m).__name__}"
                )

            mount_type_str = m.get("type", "bind").lower().strip()
            if mount_type_str == "":
                mount_type_str = "bind"

            if mount_type_str not in self._VALID_MOUNT_TYPES:
                raise ValueError(
                    f"Mount type '{mount_type_str}' is not valid. "
                    f"Valid types: {', '.join(sorted(self._VALID_MOUNT_TYPES))}"
                )

            mount = Mount(source=None, target=None, type=mount_type_str)

            if mount_type_str == "bind":
                source = m.get("source")
                target = m.get("target")
                if source is None:
                    raise ConfigValidationError(
                        f"mount[{i}] with type 'bind' must have a 'source' field"
                    )
                if target is None:
                    raise ConfigValidationError(
                        f"mount[{i}] with type 'bind' must have a 'target' field"
                    )
                mount.source = source
                mount.target = target

            elif mount_type_str == "ro-bind":
                source = m.get("source")
                target = m.get("target")
                if source is None:
                    raise ConfigValidationError(
                        f"mount[{i}] with type 'ro-bind' must have a 'source' field"
                    )
                if target is None:
                    raise ConfigValidationError(
                        f"mount[{i}] with type 'ro-bind' must have a 'target' field"
                    )
                mount.source = source
                mount.target = target

            elif mount_type_str == "dev-bind":
                source = m.get("source")
                target = m.get("target")
                if source is None:
                    raise ConfigValidationError(
                        f"mount[{i}] with type 'dev-bind' must have a 'source' field"
                    )
                if target is None:
                    raise ConfigValidationError(
                        f"mount[{i}] with type 'dev-bind' must have a 'target' field"
                    )
                mount.source = source
                mount.target = target
                device = m.get("device", False)
                mount.device = device == True

            elif mount_type_str == "tmpfs":
                target = m.get("target")
                if target is None:
                    raise ConfigValidationError(
                        f"mount[{i}] with type 'tmpfs' must have a 'target' field"
                    )
                mount.target = target
                mount.size = m.get("size", "")

            elif mount_type_str == "proc":
                target = m.get("target")
                if target is None:
                    mount.target = "/proc"
                else:
                    mount.target = target

            elif mount_type_str == "dev":
                target = m.get("target")
                if target is None:
                    mount.target = "/dev"
                else:
                    mount.target = target

            elif mount_type_str == "dir":
                target = m.get("target")
                if target is None:
                    raise ConfigValidationError(
                        f"mount[{i}] with type 'dir' must have a 'target' field"
                    )
                mount.target = target

            result.append(mount)

        return result

    def _parse_capabilities(self, caps: dict[str, Any]) -> CapabilitySet:
        """Parse capabilities from a dict.

        Canonical format:
            capabilities:
              drop: ["ALL"]
              keep: ["CAP_CHOWN"]
        """
        dropped: list[str] = []
        kept: list[str] = []

        if "drop" in caps:
            if not isinstance(caps["drop"], list):
                raise ConfigValidationError(
                    f"capabilities.drop must be a list, got "
                    f"{type(caps['drop']).__name__}. Use: drop: ['ALL'] or drop: []"
                )
            for i, item in enumerate(caps["drop"]):
                if not isinstance(item, str):
                    raise ConfigValidationError(
                        f"capabilities.drop[{i}] must be a string, "
                        f"got {type(item).__name__}: {item!r}"
                    )
            dropped = list(caps["drop"])

        if "keep" in caps:
            if not isinstance(caps["keep"], list):
                raise ConfigValidationError(
                    f"capabilities.keep must be a list, got "
                    f"{type(caps['keep']).__name__}. Use: keep: ['CAP_CHOWN']"
                )
            for i, item in enumerate(caps["keep"]):
                if not isinstance(item, str):
                    raise ConfigValidationError(
                        f"capabilities.keep[{i}] must be a string, "
                        f"got {type(item).__name__}: {item!r}"
                    )
            kept = list(caps["keep"])

        return CapabilitySet(dropped=dropped, kept=kept)

    def _parse_unshare(self, unshare: dict[str, bool]) -> NamespaceSet:
        """Parse unshare settings from a dict.

        Canonical format:
            unshare:
              pid: true
              uts: true
              ipc: false
              ...
        """
        namespace_dict: dict[str, bool] = {}

        if not isinstance(unshare, dict):
            raise ConfigValidationError(
                "unshare must be a dict mapping namespace names to booleans. "
                f"Got {type(unshare).__name__}: {unshare!r}"
            )

        for key, value in unshare.items():
            if key not in {"pid", "uts", "ipc", "cgroup", "user", "network"}:
                raise ConfigValidationError(
                    f"unshare key '{key}' is not valid. "
                    f"Valid keys: pid, uts, ipc, cgroup, user, network"
                )
            if not isinstance(value, bool):
                raise ConfigValidationError(
                    f"unshare.{key} must be a boolean (true/false), "
                    f"got {type(value).__name__}: {value!r}"
                )
            namespace_dict[key] = bool(value)

        return NamespaceSet(**namespace_dict)

    def _merge(self, config: SandboxConfig, override: dict[str, Any]) -> SandboxConfig:
        """Merge override values into a config."""
        return SandboxConfig(
            name=config.name,
            chdir=config.chdir,
            mounts=config.mounts,
            capabilities=config.capabilities,
            unshare=config.unshare,
            die_with_parent=override.get("die_with_parent", config.die_with_parent),
            new_session=override.get("new_session", config.new_session),
            hostname=override.get("hostname", config.hostname),
            timeout=override.get("timeout", config.timeout),
            env_vars=override.get("env_vars", config.env_vars),
            unenv_vars=override.get("unenv_vars", config.unenv_vars),
            _raw_config=config._raw_config,
        )

    def _copy_to_user_config_dir(self, data: dict[str, Any]) -> str:
        """Copy the bundled default config to the user config directory."""
        import shutil
        from pathlib import Path

        source = self.find_default_config_path()
        dest = str(Path("~/.config/agent-nook/sandbox.yaml").expanduser())

        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)

        logger.info("Default sandbox config copied to %s", dest)
        return str(Path(dest).resolve())

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
        with open(path, "r", encoding="utf-8") as f:
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
