"""Agent Nook CLI — Main entry point.

Provides a command-line interface for creating and running sandboxed agents.

Usage:
    agent-nook run python3 agent.py arg1 arg2
    agent-nook run --chdir /app echo hello
    agent-nook run -- /bin/ls /tmp
    agent-nook run -- /usr/bin/python3 -c "print(42)"
    agent-nook status            # Shows status of running sandboxes
    agent-nook logs              # Shows recent log output
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path


__version__ = "0.1.0"


def _auto_init() -> None:
    """Automatically initialize configuration and directories on first run.

    This ensures that the required directories exist before any command is run:
    - ~/.config/agent-nook/ (config)
    - ~/.local/state/agent-nook/logs/ (logs)

    It also copies the bundled default config file if it doesn't exist.
    """
    from agent_nook.config import copy_bundled_config
    from agent_nook.config import get_config_path
    from agent_nook.utils.directories import ensure_directories
    from agent_nook.utils.logger import get_log_directory

    logger = logging.getLogger("agent_nook")

    # Ensure directories exist using XDG-compliant paths
    # This respects XDG_CONFIG_HOME and XDG_STATE_HOME environment variables
    config_dir = Path(get_config_path()).parent
    xdg_state_logs = Path(get_log_directory())

    ensure_directories([config_dir, xdg_state_logs])
    logger.debug("Ensured directories exist")

    # Copy bundled config if user config doesn't exist
    bundled_config_path = Path(__file__).parent / "config" / "sandbox.yaml"
    if bundled_config_path.exists():
        if not Path(get_config_path()).exists():
            copy_bundled_config(bundled_config_path)
            logger.debug("Copied bundled config to user config dir")


def _setup_logging(verbose: bool) -> None:
    """Initialize logging at startup.

    This must be called early in the application lifecycle,
    before importing modules that use the logger.
    """
    from agent_nook.utils.logger import main_logger

    logger = main_logger("agent_nook", level="DEBUG" if verbose else "INFO")
    logger.debug("Logger initialized")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="agent-nook",
        description="A lightweight bwrap sandbox for AI agents",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose output")
    parser.add_argument("--quiet", action="store_true", help="Suppress non-error output")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available commands")

    # run command
    run_parser = subparsers.add_parser("run", help="Run a command in a sandbox")
    # Use REMAINDER so that -c, --foo, etc. inside the command are not parsed
    # as agent-nook options. Pass everything after first arg through to the sandbox.
    run_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        metavar="CMD [ARGS...]",
        help="Command to execute in the sandbox (e.g. 'python3 agent.py')",
    )
    run_parser.add_argument(
        "--config",
        default=None,
        help="Override config file path (default: ~/.config/agent-nook/sandbox.yaml)",
    )
    run_parser.add_argument("--chdir", metavar="DIR", help="Change working directory in sandbox (overrides config)")
    run_parser.add_argument(
        "--cap-add",
        action="append",
        default=[],
        metavar="CAP",
        help="Add capability (e.g. 'CAP_NET_BIND_SERVICE', repeat for multiple)",
    )
    run_parser.add_argument(
        "--cap-drop", action="append", default=[], metavar="CAP", help="Drop capability (e.g. 'CAP_SYS_ADMIN', repeat for multiple)"
    )
    run_parser.add_argument(
        "--unshare", action="append", default=[], metavar="NS", help="Unshare namespace (e.g. 'pid,uts', repeat for multiple)"
    )
    run_parser.add_argument(
        "--bind", action="append", default=[], metavar="SRC:DEST", help="Bind mount host SRC to sandbox DEST (repeat for multiple)"
    )
    run_parser.add_argument(
        "--ro-bind",
        action="append",
        default=[],
        metavar="SRC:DEST",
        help="Read-only bind mount host SRC to sandbox DEST (repeat for multiple)",
    )
    run_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set environment variable (e.g. 'KEY=value', repeat for multiple)",
    )
    run_parser.add_argument(
        "--unset-env", action="append", default=[], metavar="KEY", help="Unset environment variable (repeat for multiple)"
    )
    run_parser.add_argument("--hostname", metavar="HOSTNAME", help="Set sandbox hostname")
    run_parser.add_argument(
        "--new-session", action="store_true", default=True, help="Create new session (prevents TIOCSTI attacks, default: on)"
    )
    run_parser.add_argument(
        "--no-new-session", action="store_false", dest="new_session", help="Don't create new session (allows TIOCSTI)"
    )
    run_parser.add_argument("--caps", action="append", default=[], help="Additional capabilities to add (repeat for multiple)")
    run_parser.add_argument("--drop-caps", action="append", default=[], help="Additional capabilities to drop (repeat for multiple)")
    run_parser.add_argument(
        "--die-with-parent", action="store_true", default=True, help="Kill sandbox child when parent dies (default: on)"
    )
    run_parser.add_argument(
        "--no-die-with-parent", action="store_false", dest="die_with_parent", help="Keep sandbox alive after parent exits"
    )

    # status command
    status_parser = subparsers.add_parser("status", help="Show sandbox status")
    status_parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed information")

    # logs command
    logs_parser = subparsers.add_parser("logs", help="Show recent logs")
    logs_parser.add_argument("--tail", "-n", type=int, default=50, help="Number of lines to show (default: 50)")
    logs_parser.add_argument("-f", "-F", "--follow", action="store_true", help="Follow log output (like tail -F)")

    # list command
    list_parser = subparsers.add_parser("list", help="List available sandboxes")
    list_parser.add_argument("--verbose", "-v", action="store_true", help="Show details")

    args = parser.parse_args()

    if args.subcommand is None:
        parser.print_help()
        return 0

    if args.verbose:
        os.environ["PYTHONVERBOSE"] = "1"

    # Initialize logging early, before any modules are imported
    _setup_logging(args.verbose)

    # Auto-initialize configuration and directories
    _auto_init()

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
    from agent_nook.config.loader import ConfigLoader
    from agent_nook.config.loader import ConfigValidationError
    from agent_nook.sandbox import BwrapSandbox

    # Logging is already initialized by _setup_logging()
    logger = logging.getLogger("agent_nook")
    logger.info("Agent Nook v%s — Running command", __version__)

    # Unified config loading: handles file resolution, validation, overrides, and construction
    try:
        config_loader = ConfigLoader(None)  # Let load_with_overrides resolve the path
        config = config_loader.load_with_overrides(args)
        from agent_nook.utils.logger import set_sandbox_name

        set_sandbox_name(config.name)
    except FileNotFoundError as e:
        logger.error("Config file not found: %s", e)
        return 1
    except ConfigValidationError as e:
        logger.error("Configuration validation failed: %s", e)
        return 1
    except RuntimeError as e:
        logger.error("Failed to load config: %s", e)
        return 1

    # Build the command from positional args
    command: list[str] = args.command if args.command else []
    if not command:
        print("error: no command specified. Use 'agent-nook run <command>'", file=sys.stderr)
        return 1

    logger.debug("Executing: %s", " ".join(command))

    # Run the command in the sandbox using the unified API
    try:
        result = BwrapSandbox(config).run(command)
        if result.success:
            logger.info("✓ Sandbox executed successfully")
            return 0
        else:
            logger.error("✗ Sandbox execution failed with return code %d", result.return_code)
            return result.return_code or 1
    except RuntimeError as e:
        logger.error("Bubblewrap error: %s", e)
        return 1
    except subprocess.TimeoutExpired:
        logger.error("Sandbox execution timed out after %s seconds", config.timeout)
        return 1
    except Exception:
        logger.exception("Unexpected error")
        return 1


def _cmd_status(args: argparse.Namespace) -> int:
    """Display agent-nook status."""
    from agent_nook.config import get_config_path

    logger = logging.getLogger("agent_nook")
    logger.info("Agent Nook Status")
    logger.info("=" * 40)

    try:
        result = subprocess.run(["bwrap", "--version"], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            logger.info("  Bubblewrap version: %s", result.stdout.strip())
        else:
            logger.warning("  Bubblewrap not found on PATH")
    except FileNotFoundError:
        logger.warning("  Bubblewrap not found")

    config_path = get_config_path()

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
    logger.info("  agent-nook logs")
    logger.info("  agent-nook list")

    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    """Show recent logs, optionally following them live (like tail -F)."""
    from agent_nook.utils.logger import get_log_directory

    logger = logging.getLogger("agent_nook")

    log_file = os.path.join(get_log_directory(), "agent-nook.log")

    if args.follow:
        # tail -F (--follow=name --retry) reopens the file after log rotation
        # and waits if it does not exist yet, so this can be started before
        # any sandbox has run.
        try:
            proc = subprocess.Popen(["tail", "-n", str(args.tail), "-F", log_file])
        except FileNotFoundError:
            logger.error("tail command not found; cannot follow logs")
            return 1
        try:
            returncode = proc.wait()
        except KeyboardInterrupt:
            # In a terminal, Ctrl+C reaches both processes; the kill covers
            # the case where only this process received the signal.
            proc.kill()
            proc.wait()
            raise  # main() converts this to exit code 130
        return returncode or 0

    if not os.path.exists(log_file):
        logger.info("No log file found yet.")
        return 0

    try:
        result = subprocess.run(["tail", "-n", str(args.tail), log_file], check=False)
    except FileNotFoundError:
        logger.error("tail command not found; cannot show logs")
        return 1
    return result.returncode or 0


def _cmd_list(args: argparse.Namespace) -> int:
    """List available sandboxes (placeholder)."""
    logger = logging.getLogger("agent_nook")
    logger.info("Agent Nook — Available Sandboxes")
    logger.info("=" * 40)
    logger.info("No custom sandboxes configured.")
    logger.info("")
    logger.info("Create a sandbox by editing ~/.config/agent-nook/sandbox.yaml")
    logger.info("or by adding entries to the --mounts option.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
