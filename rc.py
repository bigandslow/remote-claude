#!/usr/bin/env python3
"""
rc - Remote Claude session manager

Manages sandboxed Claude Code sessions in Docker containers with tmux persistence.
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from lib.config import Config, load_config, save_config, get_config_path
from lib.docker_manager import DockerManager
from lib.tmux_manager import TmuxManager


class RemoteClaude:
    """Main application class for remote-claude."""

    def __init__(self, config: Config):
        self.config = config
        self.docker = DockerManager(config)
        self.tmux = TmuxManager(
            socket_name=config.tmux.socket_name,
            prefix=config.tmux.session_prefix,
        )

    def generate_session_id(self, workspace_path: Path) -> str:
        """Generate a unique session ID from workspace path."""
        # Use the last component of the path + timestamp
        name = workspace_path.name
        timestamp = datetime.now().strftime("%H%M%S")
        return f"{name[:20]}-{timestamp}"

    def start(
        self,
        workspace: str,
        attach: bool = True,
        prompt: Optional[str] = None,
        continue_session: bool = False,
    ) -> int:
        """Start a new Claude session.

        Args:
            workspace: Path to the workspace/worktree
            attach: Whether to attach to the session after starting
            prompt: Optional initial prompt for Claude
            continue_session: Whether to continue a previous Claude conversation

        Returns:
            Exit code (0 for success)
        """
        workspace_path = Path(workspace).expanduser().resolve()

        if not workspace_path.exists():
            print(f"Error: Workspace path does not exist: {workspace_path}")
            return 1

        if not workspace_path.is_dir():
            print(f"Error: Workspace path is not a directory: {workspace_path}")
            return 1

        # Check if Docker image exists
        if not self.docker.image_exists():
            print(f"Docker image '{self.docker.image}' not found.")
            print("Building image...")
            if not self.docker.build_image():
                print("Error: Failed to build Docker image")
                return 1
            print("Image built successfully.")

        # Generate session ID
        session_id = self.generate_session_id(workspace_path)
        session_name = self.tmux.get_session_name(session_id)

        print(f"Starting session: {session_name}")
        print(f"Workspace: {workspace_path}")

        # Set up environment variables for the container
        env_vars = {}
        if prompt:
            env_vars["RC_PROMPT"] = prompt
        if continue_session:
            env_vars["RC_CONTINUE"] = "1"

        # Start Docker container
        container_id = self.docker.start_container(
            session_id=session_id,
            workspace_path=workspace_path,
            env_vars=env_vars if env_vars else None,
        )

        if not container_id:
            print("Error: Failed to start Docker container")
            return 1

        print(f"Container started: {container_id}")

        # Create tmux session that attaches to the container
        container_name = f"rc-{session_id}"
        attach_cmd = f"docker attach {container_name}"

        if not self.tmux.create_session(
            session_name=session_name,
            command=attach_cmd,
            working_dir=str(workspace_path),
        ):
            print("Error: Failed to create tmux session")
            # Clean up container
            self.docker.remove_container(container_name, force=True)
            return 1

        print(f"Session created: {session_name}")

        if attach:
            print("Attaching to session... (use Ctrl+b d to detach)")
            time.sleep(0.5)  # Give tmux time to start
            self.tmux.attach_session(session_name)

        return 0

    def list_sessions(self, all_states: bool = False) -> int:
        """List all active sessions.

        Args:
            all_states: Include stopped sessions

        Returns:
            Exit code
        """
        containers = self.docker.list_containers(all_states=all_states)
        sessions = self.tmux.list_sessions()

        if not containers and not sessions:
            print("No active sessions.")
            return 0

        # Create a combined view
        session_map = {s.name: s for s in sessions}

        print(f"{'ID':<15} {'Status':<15} {'Workspace':<40} {'Tmux':<10}")
        print("-" * 80)

        for container in containers:
            # Extract session ID from container name
            session_id = container.name.replace("rc-", "")
            session_name = self.tmux.get_session_name(session_id)

            tmux_status = "attached" if session_map.get(session_name, None) and session_map[session_name].attached else "detached"
            if session_name not in session_map:
                tmux_status = "no tmux"

            workspace = container.workspace or "unknown"
            if len(workspace) > 38:
                workspace = "..." + workspace[-35:]

            print(
                f"{session_id:<15} {container.status:<15} {workspace:<40} {tmux_status:<10}"
            )

        return 0

    def attach(self, session_id: str) -> int:
        """Attach to an existing session.

        Args:
            session_id: Session ID or partial match

        Returns:
            Exit code
        """
        # Find matching session
        sessions = self.tmux.list_sessions()
        matching = [s for s in sessions if session_id in s.name]

        if not matching:
            # Try container-based lookup
            containers = self.docker.list_containers()
            for c in containers:
                if session_id in c.name or session_id in c.id:
                    extracted_id = c.name.replace("rc-", "")
                    session_name = self.tmux.get_session_name(extracted_id)
                    if self.tmux.session_exists(session_name):
                        self.tmux.attach_session(session_name)
                        return 0
            print(f"Error: No session found matching '{session_id}'")
            return 1

        if len(matching) > 1:
            print(f"Multiple sessions match '{session_id}':")
            for s in matching:
                print(f"  {s.name}")
            return 1

        self.tmux.attach_session(matching[0].name)
        return 0

    def kill(self, session_id: str, force: bool = False) -> int:
        """Kill a session and its container.

        Args:
            session_id: Session ID or partial match
            force: Force kill without confirmation

        Returns:
            Exit code
        """
        # Find matching container
        containers = self.docker.list_containers(all_states=True)
        matching = [c for c in containers if session_id in c.name or session_id in c.id]

        if not matching:
            print(f"Error: No session found matching '{session_id}'")
            return 1

        if len(matching) > 1:
            print(f"Multiple sessions match '{session_id}':")
            for c in matching:
                print(f"  {c.name}")
            return 1

        container = matching[0]
        extracted_id = container.name.replace("rc-", "")
        session_name = self.tmux.get_session_name(extracted_id)

        if not force:
            confirm = input(f"Kill session {container.name}? [y/N] ")
            if confirm.lower() != "y":
                print("Cancelled.")
                return 0

        # Kill tmux session first
        if self.tmux.session_exists(session_name):
            self.tmux.kill_session(session_name)
            print(f"Killed tmux session: {session_name}")

        # Stop and remove container
        self.docker.stop_container(container.name)
        self.docker.remove_container(container.name, force=True)
        print(f"Removed container: {container.name}")

        return 0

    def status(self) -> int:
        """Show detailed status of all sessions.

        Returns:
            Exit code
        """
        containers = self.docker.list_containers(all_states=True)

        if not containers:
            print("No sessions found.")
            return 0

        for container in containers:
            print(f"\nSession: {container.name}")
            print(f"  Container ID: {container.id}")
            print(f"  Status: {container.status}")
            print(f"  Workspace: {container.workspace or 'unknown'}")
            print(f"  Created: {container.created}")

            # Check tmux status
            session_id = container.name.replace("rc-", "")
            session_name = self.tmux.get_session_name(session_id)
            if self.tmux.session_exists(session_name):
                sessions = self.tmux.list_sessions()
                for s in sessions:
                    if s.name == session_name:
                        print(f"  Tmux: {'attached' if s.attached else 'detached'}")
                        break
            else:
                print("  Tmux: no session")

        return 0

    def logs(self, session_id: str, tail: int = 100, follow: bool = False) -> int:
        """Show logs for a session.

        Args:
            session_id: Session ID or partial match
            tail: Number of lines to show
            follow: Follow log output

        Returns:
            Exit code
        """
        containers = self.docker.list_containers(all_states=True)
        matching = [c for c in containers if session_id in c.name or session_id in c.id]

        if not matching:
            print(f"Error: No session found matching '{session_id}'")
            return 1

        container = matching[0]

        if follow:
            self.docker.logs(container.name, tail=tail, follow=True)
        else:
            output = self.docker.logs(container.name, tail=tail)
            if output:
                print(output)

        return 0

    def build(self) -> int:
        """Build the Docker image.

        Returns:
            Exit code
        """
        print(f"Building Docker image: {self.docker.image}")
        if self.docker.build_image():
            print("Build successful.")
            return 0
        else:
            print("Build failed.")
            return 1

    def teleport(
        self,
        workspace: str,
        attach: bool = True,
        force: bool = False,
    ) -> int:
        """Teleport an existing Claude session into the framework.

        Finds any running Claude processes in the workspace, stops them,
        and starts a new session with --continue to resume the conversation.

        Args:
            workspace: Path to the workspace
            attach: Whether to attach to the session after starting
            force: Skip confirmation prompts

        Returns:
            Exit code
        """
        import subprocess

        workspace_path = Path(workspace).expanduser().resolve()

        if not workspace_path.exists():
            print(f"Error: Workspace path does not exist: {workspace_path}")
            return 1

        # Find Claude processes running in this workspace
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"claude.*{workspace_path}"],
                capture_output=True,
                text=True,
            )
            pids = result.stdout.strip().split("\n") if result.stdout.strip() else []
        except Exception:
            pids = []

        # Also check for claude processes with cwd in workspace
        try:
            result = subprocess.run(
                ["pgrep", "-f", "claude"],
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                all_claude_pids = result.stdout.strip().split("\n")
                for pid in all_claude_pids:
                    try:
                        # Check if process cwd matches workspace
                        cwd_result = subprocess.run(
                            ["lsof", "-p", pid, "-Fn"],
                            capture_output=True,
                            text=True,
                        )
                        if str(workspace_path) in cwd_result.stdout:
                            if pid not in pids:
                                pids.append(pid)
                    except Exception:
                        pass
        except Exception:
            pass

        pids = [p for p in pids if p]  # Remove empty strings

        if pids:
            print(f"Found {len(pids)} Claude process(es) in {workspace_path}")
            for pid in pids:
                try:
                    result = subprocess.run(
                        ["ps", "-p", pid, "-o", "pid,command"],
                        capture_output=True,
                        text=True,
                    )
                    print(f"  {result.stdout.strip().split(chr(10))[-1][:80]}")
                except Exception:
                    print(f"  PID {pid}")

            if not force:
                confirm = input("\nStop these processes and teleport to framework? [y/N] ")
                if confirm.lower() != "y":
                    print("Cancelled.")
                    return 0

            # Kill the processes
            for pid in pids:
                try:
                    subprocess.run(["kill", pid], check=False)
                    print(f"Stopped process {pid}")
                except Exception as e:
                    print(f"Warning: Could not stop process {pid}: {e}")

            # Wait a moment for processes to stop
            time.sleep(1)
        else:
            print(f"No running Claude processes found in {workspace_path}")
            if not force:
                confirm = input("Continue with --continue to resume last session? [Y/n] ")
                if confirm.lower() == "n":
                    print("Cancelled.")
                    return 0

        # Start new session with --continue
        print(f"\nTeleporting session to framework...")
        return self.start(
            workspace=str(workspace_path),
            attach=attach,
            continue_session=True,
        )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Remote Claude - Sandboxed Claude Code session manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  rc start ~/projects/myapp          Start a new Claude session
  rc start ~/projects/myapp -p "Fix the bug in auth.py"
  rc start ~/projects/myapp -c       Continue previous conversation
  rc teleport ~/projects/myapp       Move existing session into framework
  rc list                            List active sessions
  rc attach myapp                    Attach to a session
  rc kill myapp                      Kill a session
  rc status                          Show detailed status
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # start command
    start_parser = subparsers.add_parser("start", help="Start a new Claude session")
    start_parser.add_argument("workspace", help="Path to workspace/worktree")
    start_parser.add_argument(
        "-p", "--prompt", help="Initial prompt for Claude"
    )
    start_parser.add_argument(
        "-c", "--continue", dest="continue_session", action="store_true",
        help="Continue previous Claude conversation"
    )
    start_parser.add_argument(
        "--no-attach", action="store_true",
        help="Don't attach to session after starting"
    )

    # list command
    list_parser = subparsers.add_parser("list", aliases=["ls"], help="List sessions")
    list_parser.add_argument(
        "-a", "--all", action="store_true", help="Include stopped sessions"
    )

    # attach command
    attach_parser = subparsers.add_parser("attach", aliases=["a"], help="Attach to session")
    attach_parser.add_argument("session_id", help="Session ID (partial match OK)")

    # kill command
    kill_parser = subparsers.add_parser("kill", aliases=["rm"], help="Kill a session")
    kill_parser.add_argument("session_id", help="Session ID (partial match OK)")
    kill_parser.add_argument(
        "-f", "--force", action="store_true", help="Force kill without confirmation"
    )

    # status command
    subparsers.add_parser("status", help="Show detailed status")

    # logs command
    logs_parser = subparsers.add_parser("logs", help="Show session logs")
    logs_parser.add_argument("session_id", help="Session ID (partial match OK)")
    logs_parser.add_argument(
        "-n", "--tail", type=int, default=100, help="Number of lines (default: 100)"
    )
    logs_parser.add_argument(
        "-f", "--follow", action="store_true", help="Follow log output"
    )

    # build command
    subparsers.add_parser("build", help="Build Docker image")

    # teleport command
    teleport_parser = subparsers.add_parser(
        "teleport", aliases=["tp"],
        help="Move existing Claude session into framework"
    )
    teleport_parser.add_argument("workspace", help="Path to workspace with existing session")
    teleport_parser.add_argument(
        "--no-attach", action="store_true",
        help="Don't attach to session after starting"
    )
    teleport_parser.add_argument(
        "-f", "--force", action="store_true",
        help="Skip confirmation prompts"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Load configuration
    config = load_config()

    # Create application
    app = RemoteClaude(config)

    # Dispatch commands
    if args.command == "start":
        return app.start(
            workspace=args.workspace,
            attach=not args.no_attach,
            prompt=args.prompt,
            continue_session=args.continue_session,
        )
    elif args.command in ("list", "ls"):
        return app.list_sessions(all_states=args.all)
    elif args.command in ("attach", "a"):
        return app.attach(args.session_id)
    elif args.command in ("kill", "rm"):
        return app.kill(args.session_id, force=args.force)
    elif args.command == "status":
        return app.status()
    elif args.command == "logs":
        return app.logs(args.session_id, tail=args.tail, follow=args.follow)
    elif args.command == "build":
        return app.build()
    elif args.command in ("teleport", "tp"):
        return app.teleport(
            workspace=args.workspace,
            attach=not args.no_attach,
            force=args.force,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
