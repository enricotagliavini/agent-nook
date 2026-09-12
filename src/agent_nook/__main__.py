"""Agent Nook CLI — Main entry point.

Provides a command-line interface for creating and running sandboxed agents.

Usage:
    agent-nook run python3 agent.py arg1 arg2
    agent-nook run --chdir /app echo hello
    agent-nook run -- /bin/ls /tmp
    agent-nook run -- /usr/bin/python3 -c "print(42)"
    agent-nook init              # Creates sandbox directory structure
    agent-nook status            # Shows status of running sandboxes
    agent-nook logs              # Shows recent log output
"""

from __future__ import annotations

import argparse
import sys
import os

__version__ = "0.1.0"


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="agent-nook",
        description="A lightweight bwrap sandbox for AI agents",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress non-error output"
    )
    parser.add_argument(
        "--config-dir",
        default=None,
        help="Override XDG_CONFIG_HOME (default: ~/.config/agent-nook)",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Available commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run a command in a sandbox")
    # Use REMAINDER so that -c, --foo, etc. inside the command are not parsed
    # as agent-nook options. Pass everything after first arg through to the sandbox.
    run_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        metavar="CMD [ARGS...]",
        help="Command to execute in the sandbox (e.g. 'python3 agent.py')"
    )
    run_parser.add_argument(
        "--chdir",
        metavar="DIR",
        help="Change working directory in sandbox (overrides config)"
    )
    run_parser.add_argument(
        "--cap-add",
        action="append",
        default=[],
        metavar="CAP",
        help="Add capability (e.g. 'CAP_NET_BIND_SERVICE', repeat for multiple)"
    )
    run_parser.add_argument(
        "--cap-drop",
        action="append",
        default=[],
        metavar="CAP",
        help="Drop capability (e.g. 'CAP_SYS_ADMIN', repeat for multiple)"
    )
    run_parser.add_argument(
        "--unshare",
        action="append",
        default=[],
        metavar="NS",
        help="Unshare namespace (e.g. 'pid,uts', repeat for multiple)"
    )
    run_parser.add_argument(
        "--bind",
        action="append",
        default=[],
        metavar="SRC:DEST",
        help="Bind mount host SRC to sandbox DEST (repeat for multiple)"
    )
    run_parser.add_argument(
        "--ro-bind",
        action="append",
        default=[],
        metavar="SRC:DEST",
        help="Read-only bind mount host SRC to sandbox DEST (repeat for multiple)"
    )
    run_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set environment variable (e.g. 'KEY=value', repeat for multiple)"
    )
    run_parser.add_argument(
        "--unset-env",
        action="append",
        default=[],
        metavar="KEY",
        help="Unset environment variable (repeat for multiple)"
    )
    run_parser.add_argument(
        "--hostname",
        metavar="HOSTNAME",
        help="Set sandbox hostname"
    )
    run_parser.add_argument(
        "--new-session",
        action="store_true",
        default=True,
        help="Create new session (prevents TIOCSTI attacks, default: on)"
    )
    run_parser.add_argument(
        "--no-new-session",
        action="store_false",
        dest="new_session",
        help="Don't create new session (allows TIOCSTI)"
    )
    run_parser.add_argument(
        "--exit-on-fail",
        action="store_true",
        help="Exit immediately on failure (don't show logs)"
    )
    run_parser.add_argument(
        "--caps",
        action="append",
        default=[],
        help="Additional capabilities to add (repeat for multiple)"
    )
    run_parser.add_argument(
        "--drop-caps",
        action="append",
        default=[],
        help="Additional capabilities to drop (repeat for multiple)"
    )
    run_parser.add_argument(
        "--die-with-parent",
        action="store_true",
        default=True,
        help="Kill sandbox child when parent dies (default: on)"
    )
    run_parser.add_argument(
        "--no-die-with-parent",
        action="store_false",
        dest="die_with_parent",
        help="Keep sandbox alive after parent exits"
    )

    # init command
    init_parser = subparsers.add_parser("init", help="Initialize config")
    init_parser.add_argument(
        "--config-dir",
        default=None,
        help="Config directory"
    )

    # status command
    status_parser = subparsers.add_parser("status", help="Show sandbox status")
    status_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed information"
    )

    # logs command
    logs_parser = subparsers.add_parser("logs", help="Show recent logs")
    logs_parser.add_argument(
        "--tail", "-n",
        type=int,
        default=50,
        help="Number of lines to show (default: 50)"
    )

    # list command
    list_parser = subparsers.add_parser("list", help="List available sandboxes")
    list_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show details"
    )

    args = parser.parse_args()

    if args.subcommand is None:
        parser.print_help()
        return 0

    if args.verbose:
        os.environ["PYTHONVERBOSE"] = "1"

    try:
        return _dispatch_command(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except SystemExit as e:
        return e.code if e.code is not None else 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _dispatch_command(args: argparse.Namespace) -> int:
    """Dispatch to the appropriate command handler."""
    subcommand = args.subcommand if hasattr(args, "subcommand") else args.command
    if subcommand == "run":
        return _cmd_run(args)
    elif subcommand == "init":
        return _cmd_init(args)
    elif subcommand == "status":
        return _cmd_status(args)
    elif subcommand == "logs":
        return _cmd_logs(args)
    elif subcommand == "list":
        return _cmd_list(args)
    else:
        print(f"Unknown command: {subcommand}", file=sys.stderr)
        return 1


def _cmd_run(args: argparse.Namespace) -> int:
    """Run a command inside a sandbox."""
    from agent_nook.config import get_config_dir, get_state_dir
    from agent_nook.config.loader import ConfigLoader
    from agent_nook.runner import run_in_sandbox, BwrapError
    from agent_nook.config.loader import ConfigValidationError
    from agent_nook.sandbox.builder import BwrapBuilder, SandboxConfig
    from agent_nook.utils.logger import setup_logger
    from agent_nook.utils.directories import ensure_directories

    # Setup logging
    logger = setup_logger("agent_nook", level="INFO" if not args.verbose else "DEBUG")
    logger.info("Agent Nook v%s — Running command", __version__)

    # Resolve config and state directories using XDG paths
    config_dir = get_config_dir()
    state_dir = get_state_dir()

    ensure_directories([config_dir, state_dir, os.path.join(state_dir, "logs")])

    # Setup config
    config_loader = ConfigLoader(config_dir)

    # Load configuration
    try:
        if hasattr(args, "config") and args.config:
            # Override config path with CLI argument
            config_dict = config_loader.load(args.config)
        else:
            # Find and load the bundled default config
            config_path = config_loader.find_default_config()
            config_dict = config_loader.load(config_path)

        # Set as global config (accessible via `from config import nook_config`)
        from agent_nook.config import set_config
        set_config(config_dict)
    except FileNotFoundError as e:
        logger.error("Config file not found: %s", e)
        return 1
    except ConfigValidationError as e:
        logger.error("Configuration validation failed: %s", e)
        return 1
    except Exception as e:
        logger.error("Failed to load config: %s", e)
        return 1

    # Apply CLI overrides to config
    config_dict = _apply_cli_overrides(dict(config_dict.__dict__), args)

    # Build SandboxConfig from the normalized dict
    try:
        config = SandboxConfig(**config_dict)
        builder = BwrapBuilder(config)
        builder.validate()
    except TypeError as e:
        logger.error("Configuration error: %s", e)
        return 1
    except ConfigValidationError as e:
        logger.error("Configuration validation failed: %s", e)
        return 1

    # Build the command from positional args
    command: list[str] = args.command if args.command else []
    if not command:
        print("error: no command specified. Use 'agent-nook run <command>'", file=sys.stderr)
        return 1

    logger.debug("Executing: %s", " ".join(command))

    full_bwrap_cmd = builder.build(command)
    logger.debug("Full bwrap command: %s", " ".join(full_bwrap_cmd))

    try:
        with run_in_sandbox(config, full_bwrap_cmd) as result:
            if result.success:
                logger.info("✓ Sandbox executed successfully")
                if result.stdout:
                    print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, end="")
                return 0
            else:
                logger.error("✗ Sandbox execution failed")
                if result.stderr:
                    logger.error("  stderr: %s", result.stderr[:2000])
                return result.return_code or 1
    except BwrapError as e:
        logger.error("Bubblewrap error: %s", e)
        return 1
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        return 1


def _cmd_init(args: argparse.Namespace) -> int:
    """Initialize the agent-nook config directory."""
    from agent_nook.config import get_config_dir, get_state_dir
    from agent_nook.config.loader import ConfigLoader
    from agent_nook.utils.logger import setup_logger
    from agent_nook.utils.directories import ensure_directories
    import logging

    logger = setup_logger("agent_nook", level="DEBUG")
    logger.info("Initializing Agent Nook config...")

    config_dir = get_config_dir()
    state_dir = get_state_dir()

    ensure_directories([config_dir, state_dir, os.path.join(state_dir, "logs")])

    config_loader = ConfigLoader(config_dir)

    try:
        config_path = config_loader.find_default_config()
    except FileNotFoundError as e:
        logger.error("Cannot find default config. Install agent-nook first.")
        return 1

    logger.info("✓ Config directory: %s", config_dir)
    logger.info("✓ State directory: %s", state_dir)
    logger.info("✓ Logs directory: %s", os.path.join(state_dir, "logs"))
    logger.info("✓ Default config: %s", config_path)
    logger.info("")
    logger.info("Edit %s to customize your sandbox.", config_path)
    logger.info("")
    logger.info("Example:")
    logger.info("  agent-nook run python3 -c 'print(\\\"hello\\\")'")

    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Show sandbox status."""
    from agent_nook.utils.logger import setup_logger
    import logging

    logger = setup_logger("agent_nook", level="INFO" if args.verbose else "INFO")
    logger.info("Agent Nook Status")
    logger.info("=" * 40)

    import subprocess
    try:
        result = subprocess.run(["bwrap", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            logger.info("  Bubblewrap version: %s", result.stdout.strip())
        else:
            logger.warning("  Bubblewrap not found on PATH")
            logger.info("  Install it with: sudo apt install bubblewrap")
    except FileNotFoundError:
        logger.warning("  Bubblewrap not found")

    config_dir = os.environ.get("XDG_CONFIG_HOME", "~/.config")
    config_dir = os.path.expanduser(config_dir)
    config_path = os.path.join(config_dir, "agent-nook", "sandbox.yaml")

    if os.path.exists(config_path):
        logger.info("  Config: %s (found)", config_path)
    else:
        logger.info("  Config: %s (not found)", config_path)

    state_dir = os.environ.get("XDG_STATE_HOME", "~/.local/state")
    state_dir = os.path.expanduser(state_dir)
    state_path = os.path.join(state_dir, "agent-nook")

    if os.path.exists(state_path):
        logger.info("  State: %s (found)", state_path)
        log_path = os.path.join(state_path, "logs", "agent-nook.log")
        if os.path.exists(log_path):
            logger.info("  Logs: %s (%d KB)", log_path, os.path.getsize(log_path) / 1024)
    else:
        logger.info("  State: %s (not found)", state_path)

    logger.info("")
    logger.info("Commands:")
    logger.info("  agent-nook run python3 agent.py")
    logger.info("  agent-nook init")
    logger.info("  agent-nook logs")
    logger.info("  agent-nook list")

    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    """Show recent logs."""
    from agent_nook.utils.logger import setup_logger, get_log_directory
    import logging

    logger = setup_logger("agent_nook", level="INFO")
    logger.info("Showing last %d lines of logs...", args.tail)

    log_dir = get_log_directory()
    log_file = os.path.join(log_dir, "agent-nook.log")

    if not os.path.exists(log_file):
        logger.info("No log file found yet.")
        return 0

    try:
        with open(log_file, "r") as f:
            lines = f.readlines()
            lines = lines[-args.tail:]

        for line in reversed(lines):
            logger.info(line.strip())
    except Exception as e:
        logger.error("Failed to read logs: %s", e)
        return 1

    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    """List available sandboxes (placeholder)."""
    from agent_nook.utils.logger import setup_logger

    logger = setup_logger("agent_nook", level="INFO")
    logger.info("Agent Nook — Available Sandboxes")
    logger.info("=" * 40)
    logger.info("No custom sandboxes configured.")
    logger.info("")
    logger.info("Create a sandbox by editing ~/.config/agent-nook/sandbox.yaml")
    logger.info("or by adding entries to the --mounts option.")

    return 0


def _apply_cli_overrides(config_dict: dict, args: argparse.Namespace) -> dict[str, Any]:
    """Apply command-line argument overrides to the config.

    Args:
        config_dict: The configuration dictionary (already a SandboxConfig.__dict__).
        args: Parsed CLI arguments.

    Returns:
        A new SandboxConfig with CLI overrides applied.
    """
    import sys
    from agent_nook.config.config import SandboxConfig

    # Convert to dict, mutate, then rebuild SandboxConfig
    result = config_dict.copy()

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
            result.setdefault("mounts", []).append({
                "source": parts[0],
                "target": parts[1],
                "readonly": False,
            })

    for bind in getattr(args, "ro_bind", []):
        parts = bind.split(":")
        if len(parts) == 2:
            result.setdefault("mounts", []).append({
                "source": parts[0],
                "target": parts[1],
                "readonly": True,
            })

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
        result.setdefault("unenv_vars", []).append(var)

    return dict(result)


if __name__ == "__main__":
    sys.exit(main())
