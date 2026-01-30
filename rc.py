#!/usr/bin/env python3
"""
rc - Remote Claude session manager

Manages sandboxed Claude Code sessions in Docker containers with tmux persistence.
"""

import argparse
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from lib.config import AccountProfile, Config, load_config, save_config, get_config_path
from lib.docker_manager import DockerManager
from lib.tmux_manager import TmuxManager


def check_docker_running() -> bool:
    """Check if Docker daemon is running."""
    import subprocess
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=10,
    )
    return result.returncode == 0


def ensure_docker_running() -> bool:
    """Ensure Docker daemon is running, starting it if needed.

    Returns:
        True if Docker is running (or was started successfully)
    """
    import subprocess

    if check_docker_running():
        return True

    print("Docker daemon is not running.")

    # On macOS, try to start Docker Desktop
    if sys.platform == "darwin":
        response = input("Start Docker Desktop? [Y/n] ").strip().lower()
        if response in ("", "y", "yes"):
            print("Starting Docker Desktop...")
            subprocess.run(["open", "-a", "Docker"], check=False)

            # Wait for Docker to be ready (up to 60 seconds)
            print("Waiting for Docker to start", end="", flush=True)
            for _ in range(30):
                time.sleep(2)
                print(".", end="", flush=True)
                if check_docker_running():
                    print(" ready!")
                    return True
            print(" timeout")
            print("Error: Docker did not start in time. Please start it manually.")
            return False
        else:
            print("Please start Docker Desktop and try again.")
            return False
    else:
        # Linux - suggest starting the service
        print("Please start Docker daemon:")
        print("  sudo systemctl start docker")
        print("  # or: sudo service docker start")
        return False


class RemoteClaude:
    """Main application class for remote-claude."""

    def __init__(self, config: Config):
        self.config = config
        self.docker = DockerManager(config)
        self.tmux = TmuxManager(
            socket_name=config.tmux.socket_name,
            prefix=config.tmux.session_prefix,
        )

    def generate_session_id(self, workspace_path: Path, name: Optional[str] = None) -> str:
        """Generate a unique session ID.

        Uses custom name or workspace name prefix + cryptographically random suffix
        for unpredictability while maintaining human readability.

        Args:
            workspace_path: Path to the workspace
            name: Optional custom name to use instead of workspace name
        """
        base_name = name if name else workspace_path.name
        # Sanitize: dots break tmux target parsing (session.window.pane)
        base_name = base_name.replace(".", "-")
        # Use random hex instead of timestamp for unpredictability
        random_suffix = secrets.token_hex(4)  # 8 hex chars
        return f"{base_name[:16]}-{random_suffix}"

    def start(
        self,
        workspace: str,
        attach: bool = True,
        prompt: Optional[str] = None,
        continue_session: bool = False,
        name: Optional[str] = None,
        account: Optional[str] = None,
    ) -> int:
        """Start a new Claude session.

        Args:
            workspace: Path to the workspace/worktree
            attach: Whether to attach to the session after starting
            prompt: Optional initial prompt for Claude
            continue_session: Whether to continue a previous Claude conversation
            name: Optional custom session name
            account: Account profile name (uses default if None)

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

        # Ensure Docker is running
        if not ensure_docker_running():
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
        session_id = self.generate_session_id(workspace_path, name)
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
            account=account,
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

        # Auto-select dark mode theme on first run
        # Claude shows a theme picker on first start - send Enter to select default (dark mode)
        self._auto_select_theme(session_name)

        if attach:
            print("Attaching to session... (use Ctrl+b d to detach)")
            time.sleep(0.5)  # Give tmux time to start
            self.tmux.attach_session(session_name)

        return 0

    def _auto_select_theme(self, session_name: str) -> None:
        """Auto-navigate Claude's first-run prompts.

        Handles:
        - Theme picker: sends Enter to select dark mode (default)
        - Login method: sends Enter to select Claude subscription (option 1)
        """
        # Wait for Claude to boot and handle first-run prompts
        # Check up to 20 times with 0.5s intervals (10 seconds max)
        prompts_handled = set()

        for _ in range(20):
            time.sleep(0.5)
            output = self.tmux.capture_pane(session_name, lines=50)
            if not output:
                continue

            # Theme picker - send Enter to select dark mode (option 1, default)
            if "Choose the text style" in output and "theme" not in prompts_handled:
                self.tmux.send_keys(session_name, "", enter=True)
                prompts_handled.add("theme")
                continue

            # Login method picker - send Enter to select Claude subscription (option 1)
            if "Select login method" in output and "login" not in prompts_handled:
                self.tmux.send_keys(session_name, "", enter=True)
                prompts_handled.add("login")
                continue

            # If we see the main prompt or an error, we're done
            if ">" in output or "Error" in output:
                break

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

        for container in containers:
            # Extract session ID from container name
            session_id = container.name.replace("rc-", "")
            session_name = self.tmux.get_session_name(session_id)

            tmux_status = "attached" if session_map.get(session_name, None) and session_map[session_name].attached else "detached"
            if session_name not in session_map:
                tmux_status = "no tmux"

            workspace = container.workspace or "unknown"

            # Simple name for attach (portion before random suffix)
            attach_name = session_id.rsplit("-", 1)[0] if "-" in session_id else session_id
            account_display = container.account or "default"

            print(f"{session_id}  [{tmux_status}]  {container.status}")
            print(f"  account: {account_display}")
            print(f"  attach: rc attach {attach_name}")
            print(f"  workspace: {workspace}")
            print()

        return 0

    def attach(self, session_id: Optional[str] = None) -> int:
        """Attach to an existing session.

        Args:
            session_id: Session ID or partial match. If None, shows interactive picker.

        Returns:
            Exit code
        """
        container = self._find_or_select_container(
            session_id, "Select a session to attach:"
        )
        if not container:
            return 1

        extracted_id = container.name.replace("rc-", "")
        session_name = self.tmux.get_session_name(extracted_id)

        if self.tmux.session_exists(session_name):
            self.tmux.attach_session(session_name)
            return 0
        else:
            print(f"Error: Tmux session not found for {extracted_id}")
            return 1

    def kill(self, session_id: Optional[str] = None, force: bool = False) -> int:
        """Kill a session and its container.

        Args:
            session_id: Session ID or partial match. If None, shows picker.
            force: Force kill without confirmation

        Returns:
            Exit code
        """
        container = self._find_or_select_container(
            session_id, "Select a session to kill:", all_states=True
        )
        if not container:
            return 1

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

    def restart(self, session_id: Optional[str] = None) -> int:
        """Restart Claude in a session to pick up new configs.

        Sends /exit to Claude, which triggers the entrypoint loop to restart
        Claude with --continue (resuming the previous conversation).

        Args:
            session_id: Session ID or partial match. If None, shows picker.

        Returns:
            Exit code
        """
        container = self._find_or_select_container(
            session_id, "Select a session to restart:"
        )
        if not container:
            return 1

        extracted_id = container.name.replace("rc-", "")
        session_name = self.tmux.get_session_name(extracted_id)

        if not self.tmux.session_exists(session_name):
            print(f"Error: tmux session not found: {session_name}")
            return 1

        # Send /exit to Claude to trigger restart
        print(f"Restarting Claude in {session_name}...")
        self.tmux.send_keys(session_name, "/exit", enter=True)
        print("Sent /exit - Claude will restart with --continue")

        return 0

    def shell(self, session_id: Optional[str] = None) -> int:
        """Open a shell in a session's container.

        Args:
            session_id: Session ID or partial match. If None, shows interactive picker.

        Returns:
            Exit code
        """
        container = self._find_or_select_container(
            session_id, "Select a session for shell:"
        )
        if not container:
            return 1

        # Start interactive shell in the container
        print(f"Opening shell in {container.name}...")
        os.execvp("docker", ["docker", "exec", "-it", container.name, "/bin/bash"])

        return 0

    def _interactive_select(self, containers: list, prompt: str):
        """Show interactive session picker.

        Args:
            containers: List of Container objects
            prompt: Prompt to display

        Returns:
            Selected Container or None if cancelled
        """
        from datetime import datetime as dt

        # Sort by created time, most recent first
        def parse_created(c):
            try:
                date_str = " ".join(c.created.split()[:2])
                return dt.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except (ValueError, IndexError):
                return dt.min

        sorted_containers = sorted(containers, key=parse_created, reverse=True)

        print(prompt)
        print()
        for i, c in enumerate(sorted_containers, 1):
            session_id = c.name.replace("rc-", "")
            attach_name = session_id.rsplit("-", 1)[0] if "-" in session_id else session_id
            print(f"  {i}) {attach_name:<20} {c.status}")

        print()
        try:
            choice = input("Enter number (or q to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if not choice or choice.lower() == 'q':
            return None

        try:
            idx = int(choice) - 1
        except ValueError:
            print(f"Invalid input: '{choice}'")
            return None

        if not (0 <= idx < len(sorted_containers)):
            print(f"Invalid selection: {choice}. Enter 1-{len(sorted_containers)}.")
            return None

        return sorted_containers[idx]

    def _find_or_select_container(
        self,
        session_id: Optional[str],
        prompt: str,
        all_states: bool = False,
    ):
        """Find a container by ID or show interactive picker if no ID provided.

        This is the unified helper for session selection across commands.

        Args:
            session_id: Session ID or partial match. If None, shows picker.
            prompt: Prompt to display for interactive picker.
            all_states: Include stopped containers in the list.

        Returns:
            Selected Container or None if not found/cancelled.
        """
        containers = self.docker.list_containers(all_states=all_states)

        if not containers:
            print("No active sessions.")
            return None

        # If no session_id provided, show interactive picker
        if not session_id:
            return self._interactive_select(containers, prompt)

        # Find matching container
        matching = [c for c in containers if session_id in c.name or session_id in c.id]

        if not matching:
            print(f"Error: No session found matching '{session_id}'")
            return None

        if len(matching) > 1:
            print(f"Multiple sessions match '{session_id}':")
            for c in matching:
                print(f"  {c.name}")
            return None

        return matching[0]

    def setup(self) -> int:
        """Run interactive setup to create a pre-configured image.

        Starts a temporary container, lets user complete onboarding (theme,
        login, etc.), then commits the container as remote-claude:configured.

        Returns:
            Exit code
        """
        # Ensure Docker is running
        if not ensure_docker_running():
            return 1

        # Check if base image exists
        if not self.docker.image_exists():
            print(f"Docker image '{self.docker.image}' not found.")
            print("Building image...")
            if not self.docker.build_image():
                print("Error: Failed to build Docker image")
                return 1
            print("Image built successfully.")

        # Check if configured image already exists
        if self.docker.configured_image_exists():
            confirm = input(
                f"Configured image already exists. Recreate? [y/N] "
            )
            if confirm.lower() != "y":
                print("Cancelled.")
                return 0
            # Remove old configured image
            subprocess.run(
                ["docker", "rmi", self.docker.CONFIGURED_IMAGE],
                capture_output=True,
            )

        # Clean up any existing setup container
        self.docker.remove_setup_container()

        print("Starting setup container...")
        container_id = self.docker.start_setup_container()
        if not container_id:
            print("Error: Failed to start setup container")
            return 1

        print(f"Container started: {container_id}")
        print()
        print("=" * 60)
        print("Complete the onboarding process:")
        print("  1. Select theme (dark mode recommended)")
        print("  2. Login with your account")
        print("  3. Accept any security prompts")
        print("  4. Once at the main prompt, type /exit to finish")
        print("=" * 60)
        print()

        # Attach to the container interactively
        try:
            subprocess.run(
                ["docker", "attach", self.docker.SETUP_CONTAINER],
                check=False,
            )
        except KeyboardInterrupt:
            print("\nSetup interrupted.")

        # Ask if we should save the configured image
        print()
        confirm = input("Save this configuration as the base image? [Y/n] ")
        if confirm.lower() == "n":
            print("Discarding setup container...")
            self.docker.remove_setup_container()
            return 0

        # Commit the container
        print(f"Saving configured image as {self.docker.CONFIGURED_IMAGE}...")
        if self.docker.commit_configured_image(self.docker.SETUP_CONTAINER):
            print("Success! Future sessions will use this pre-configured image.")
            print()
            print("To start a session: rc start /path/to/workspace")
        else:
            print("Error: Failed to commit configured image")
            self.docker.remove_setup_container()
            return 1

        # Clean up setup container
        self.docker.remove_setup_container()

        return 0

    def switch(self, session_id: str, account: str) -> int:
        """Switch a session to a different account.

        Stops the current container and starts a new one with the new account's
        credentials. Workspace is preserved but Claude conversation starts fresh.

        Args:
            session_id: Session ID or partial match
            account: Account profile name to switch to

        Returns:
            Exit code
        """
        # Validate account exists
        if account not in self.config.accounts.profiles and account != "default":
            print(f"Error: Account '{account}' not found in config")
            print("Available accounts:")
            print("  default")
            for name in self.config.accounts.profiles:
                print(f"  {name}")
            return 1

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
        current_account = container.account or "default"

        if current_account == account:
            print(f"Session already using account '{account}'")
            return 0

        extracted_id = container.name.replace("rc-", "")
        session_name = self.tmux.get_session_name(extracted_id)
        workspace = container.workspace

        if not workspace:
            print("Error: Could not determine workspace for session")
            return 1

        print(f"Switching session from '{current_account}' to '{account}'...")
        print("Note: Claude conversation will start fresh (workspace preserved)")

        # Kill tmux session first
        if self.tmux.session_exists(session_name):
            self.tmux.kill_session(session_name)

        # Stop and remove old container
        self.docker.stop_container(container.name)
        self.docker.remove_container(container.name, force=True)

        # Start new container with new account
        container_id = self.docker.start_container(
            session_id=extracted_id,
            workspace_path=Path(workspace),
            account=account,
        )

        if not container_id:
            print("Error: Failed to start new container")
            return 1

        # Create new tmux session
        container_name = f"rc-{extracted_id}"
        attach_cmd = f"docker attach {container_name}"

        if not self.tmux.create_session(
            session_name=session_name,
            command=attach_cmd,
            working_dir=workspace,
        ):
            print("Error: Failed to create tmux session")
            return 1

        print(f"Switched to account '{account}'")
        print(f"Attach with: rc attach {extracted_id.rsplit('-', 1)[0]}")

        return 0

    def account_list(self) -> int:
        """List configured accounts.

        Returns:
            Exit code
        """
        print("Configured accounts:")
        print()

        default_account = self.config.accounts.default

        # Always show 'default' as an option (uses global credentials)
        marker = " (active)" if default_account == "default" else ""
        print(f"  default{marker}")
        print("    Uses global credentials from 'credentials' config section")
        print()

        for name, profile in self.config.accounts.profiles.items():
            marker = " (active)" if name == default_account else ""
            print(f"  {name}{marker}")
            if profile.anthropic:
                print(f"    anthropic: {profile.anthropic}")
            if profile.claude:
                print(f"    claude: {profile.claude}")
            if profile.git:
                print(f"    git: {profile.git}")
            if profile.ssh:
                print(f"    ssh: {profile.ssh}")
            if profile.claude_gcp:
                print(f"    claude_gcp: {profile.claude_gcp}")
            if not any([profile.anthropic, profile.claude, profile.git, profile.ssh, profile.claude_gcp]):
                print("    (no overrides, uses global credentials)")
            print()

        return 0

    def account_add(self, name: str) -> int:
        """Add a new account profile with setup wizard.

        Args:
            name: Account profile name

        Returns:
            Exit code
        """
        import subprocess

        # Validate name
        if name == "default":
            print("Error: 'default' is reserved for global credentials")
            return 1

        if name in self.config.accounts.profiles:
            print(f"Error: Account '{name}' already exists")
            return 1

        print(f"Setting up account profile: {name}")
        print()

        # Define credential directories
        anthropic_dir = Path.home() / f".anthropic-{name}"
        claude_dir = Path.home() / f".claude-{name}"

        # Create directories
        print(f"Creating credential directories...")
        anthropic_dir.mkdir(exist_ok=True)
        claude_dir.mkdir(exist_ok=True)
        print(f"  {anthropic_dir}")
        print(f"  {claude_dir}")
        print()

        # Check if Docker image exists
        if not self.docker.image_exists():
            print(f"Docker image '{self.docker.image}' not found.")
            print("Building image...")
            if not self.docker.build_image():
                print("Error: Failed to build Docker image")
                return 1
            print("Image built successfully.")
            print()

        # Launch temporary container for login
        print("Launching temporary container for authentication...")
        print("Run 'claude /login' to authenticate, then 'exit' when done.")
        print()

        container_name = f"rc-login-{name}-{secrets.token_hex(4)}"

        # Build docker run command for interactive login
        docker_args = [
            "docker", "run", "-it", "--rm",
            "--name", container_name,
            "-v", f"{anthropic_dir}:/home/claude/.anthropic",
            "-v", f"{claude_dir}:/home/claude/.claude-host",
            self.docker.image,
            "/bin/bash", "-c",
            "echo 'Run: claude /login' && echo 'Then: exit' && exec bash"
        ]

        result = subprocess.run(docker_args)

        if result.returncode != 0:
            print()
            print("Warning: Container exited with non-zero status")

        # Check if credentials were created
        api_key_file = anthropic_dir / "api_key"
        credentials_file = anthropic_dir / "credentials.json"

        if not api_key_file.exists() and not credentials_file.exists():
            print()
            print("Warning: No credentials found. Did you run 'claude /login'?")
            confirm = input("Continue anyway? [y/N] ")
            if confirm.lower() != "y":
                print("Cancelled.")
                return 1

        print()

        # Ask about git/ssh overrides
        profile = AccountProfile(
            anthropic=anthropic_dir,
            claude=claude_dir,
        )

        print("Optional: Configure separate git/ssh credentials for this account?")
        configure_git = input("Set up git/ssh overrides? [y/N] ")

        if configure_git.lower() == "y":
            print()
            print("Enter paths (leave blank to use global credentials):")

            git_path = input(f"  Git config path [default: use global]: ").strip()
            if git_path:
                expanded = Path(git_path).expanduser()
                if expanded.exists():
                    profile.git = expanded
                else:
                    print(f"    Warning: {expanded} does not exist")

            ssh_path = input(f"  SSH directory path [default: use global]: ").strip()
            if ssh_path:
                expanded = Path(ssh_path).expanduser()
                if expanded.exists():
                    profile.ssh = expanded
                else:
                    print(f"    Warning: {expanded} does not exist")

            gcp_path = input(f"  GCP credentials path [default: use global]: ").strip()
            if gcp_path:
                expanded = Path(gcp_path).expanduser()
                if expanded.exists():
                    profile.claude_gcp = expanded
                else:
                    print(f"    Warning: {expanded} does not exist")

        # Save profile
        self.config.accounts.profiles[name] = profile
        save_config(self.config)

        print()
        print(f"Account '{name}' created successfully!")
        print()
        print("Usage:")
        print(f"  rc start ~/project --account {name}")
        print(f"  rc switch <session> {name}")

        # Ask if this should be the default
        print()
        set_default = input(f"Set '{name}' as the default account? [y/N] ")
        if set_default.lower() == "y":
            self.config.accounts.default = name
            save_config(self.config)
            print(f"Default account set to '{name}'")

        return 0

    def account_remove(self, name: str, force: bool = False) -> int:
        """Remove an account profile.

        Args:
            name: Account profile name
            force: Skip confirmation prompts

        Returns:
            Exit code
        """
        import shutil

        if name == "default":
            print("Error: Cannot remove 'default' (use global credentials config)")
            return 1

        if name not in self.config.accounts.profiles:
            print(f"Error: Account '{name}' not found")
            return 1

        profile = self.config.accounts.profiles[name]

        # Check if any active sessions use this account
        containers = self.docker.list_containers()
        using_account = [c for c in containers if c.account == name]

        if using_account:
            print(f"Warning: {len(using_account)} active session(s) using account '{name}':")
            for c in using_account:
                print(f"  {c.name}")
            print()
            if not force:
                confirm = input("Continue anyway? [y/N] ")
                if confirm.lower() != "y":
                    print("Cancelled.")
                    return 0

        # Confirm removal
        if not force:
            print(f"Remove account profile '{name}'?")
            if profile.anthropic:
                print(f"  anthropic: {profile.anthropic}")
            if profile.claude:
                print(f"  claude: {profile.claude}")
            confirm = input("Confirm removal? [y/N] ")
            if confirm.lower() != "y":
                print("Cancelled.")
                return 0

        # Remove from config
        del self.config.accounts.profiles[name]

        # Update default if this was the default account
        if self.config.accounts.default == name:
            self.config.accounts.default = "default"
            print(f"Default account reset to 'default'")

        save_config(self.config)
        print(f"Account '{name}' removed from config.")

        # Offer to delete credential directories
        dirs_to_delete = []
        if profile.anthropic and profile.anthropic.exists():
            dirs_to_delete.append(profile.anthropic)
        if profile.claude and profile.claude.exists():
            dirs_to_delete.append(profile.claude)

        if dirs_to_delete:
            print()
            print("Credential directories:")
            for d in dirs_to_delete:
                print(f"  {d}")

            if not force:
                delete_dirs = input("Delete these directories? [y/N] ")
            else:
                delete_dirs = "n"

            if delete_dirs.lower() == "y":
                for d in dirs_to_delete:
                    try:
                        shutil.rmtree(d)
                        print(f"  Deleted: {d}")
                    except Exception as e:
                        print(f"  Failed to delete {d}: {e}")
            else:
                print("Credential directories preserved.")

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
            print(f"  Account: {container.account or 'default'}")
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

    def logs(
        self, session_id: Optional[str] = None, tail: int = 100, follow: bool = False
    ) -> int:
        """Show logs for a session.

        Args:
            session_id: Session ID or partial match. If None, shows picker.
            tail: Number of lines to show
            follow: Follow log output

        Returns:
            Exit code
        """
        container = self._find_or_select_container(
            session_id, "Select a session to view logs:", all_states=True
        )
        if not container:
            return 1

        if follow:
            self.docker.logs(container.name, tail=tail, follow=True)
        else:
            output = self.docker.logs(container.name, tail=tail)
            if output:
                print(output)

        return 0

    def build(self, refresh: bool = False) -> int:
        """Build the Docker image.

        Args:
            refresh: If True, also update the configured image (preserves onboarding)

        Returns:
            Exit code
        """
        print(f"Building Docker image: {self.docker.image}")
        if not self.docker.build_image():
            print("Build failed.")
            return 1
        print("Build successful.")

        if refresh and self.docker.configured_image_exists():
            print()
            print("Updating configured image...")
            return self._refresh_configured_image()

        return 0

    def _refresh_configured_image(self) -> int:
        """Update the configured image with new base image layers.

        Preserves the onboarding state from the old configured image
        while picking up entrypoint and other changes from the base image.

        Returns:
            Exit code
        """
        # Clean up any existing setup container
        self.docker.remove_setup_container()

        # Start a temp container from the NEW base image
        print("Starting temporary container from base image...")
        container_id = self.docker.start_setup_container()
        if not container_id:
            print("Error: Failed to start temporary container")
            return 1

        # Wait for entrypoint to complete initial setup
        print("Running entrypoint setup...")
        time.sleep(3)

        # The container runs in setup mode (exits after first claude run)
        # We just need the entrypoint to finish its setup
        # Send Ctrl-C to exit claude, then wait for container
        subprocess.run(
            ["docker", "exec", self.docker.SETUP_CONTAINER, "pkill", "-INT", "claude"],
            capture_output=True,
        )
        time.sleep(2)

        # Remove old configured image
        print(f"Removing old configured image...")
        subprocess.run(
            ["docker", "rmi", "-f", self.docker.CONFIGURED_IMAGE],
            capture_output=True,
        )

        # Commit the container as new configured image
        print(f"Saving as {self.docker.CONFIGURED_IMAGE}...")
        if self.docker.commit_configured_image(self.docker.SETUP_CONTAINER):
            print("Configured image updated successfully.")
            # Clean up
            self.docker.remove_setup_container()
            return 0
        else:
            print("Error: Failed to commit configured image")
            self.docker.remove_setup_container()
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
  rc setup                           One-time setup (creates pre-configured image)
  rc start ~/projects/myapp          Start a new session
  rc start ~/projects/myapp -p "Fix the bug in auth.py"
  rc start ~/projects/myapp -c       Continue previous conversation
  rc restart myapp                   Restart session (picks up new MCP configs)
  rc list                            List active sessions
  rc attach myapp                    Attach to a session
  rc kill myapp                      Kill a session
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # start command
    start_parser = subparsers.add_parser("start", aliases=["s"], help="Start a new Claude session")
    start_parser.add_argument("workspace", help="Path to workspace/worktree")
    start_parser.add_argument(
        "-n", "--name", help="Custom session name (default: derived from workspace)"
    )
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
    start_parser.add_argument(
        "-a", "--account",
        help="Account profile to use (default: from config)"
    )

    # list command
    list_parser = subparsers.add_parser("list", aliases=["ls"], help="List sessions")
    list_parser.add_argument(
        "-a", "--all", action="store_true", help="Include stopped sessions"
    )

    # attach command
    attach_parser = subparsers.add_parser("attach", aliases=["a"], help="Attach to session")
    attach_parser.add_argument("session_id", nargs="?", default=None, help="Session ID (partial match OK). If omitted, shows picker.")

    # kill command
    kill_parser = subparsers.add_parser("kill", aliases=["rm"], help="Kill a session")
    kill_parser.add_argument("session_id", nargs="?", default=None, help="Session ID (partial match OK). If omitted, shows picker.")
    kill_parser.add_argument(
        "-f", "--force", action="store_true", help="Force kill without confirmation"
    )

    # restart command
    restart_parser = subparsers.add_parser("restart", aliases=["r"], help="Restart Claude in a session")
    restart_parser.add_argument("session_id", nargs="?", default=None, help="Session ID (partial match OK). If omitted, shows picker.")

    # shell command
    shell_parser = subparsers.add_parser("shell", aliases=["sh"], help="Open shell in a session's container")
    shell_parser.add_argument("session_id", nargs="?", default=None, help="Session ID (partial match OK). If omitted, shows picker.")

    # status command
    subparsers.add_parser("status", help="Show detailed status")

    # switch command
    switch_parser = subparsers.add_parser("switch", help="Switch session to different account")
    switch_parser.add_argument("session_id", help="Session ID (partial match OK)")
    switch_parser.add_argument("account", help="Account profile to switch to")

    # account command
    account_parser = subparsers.add_parser("account", help="Manage account profiles")
    account_subparsers = account_parser.add_subparsers(dest="account_command", help="Account commands")

    account_subparsers.add_parser("list", help="List configured accounts")

    account_add_parser = account_subparsers.add_parser("add", help="Add new account profile")
    account_add_parser.add_argument("name", help="Account profile name")

    account_remove_parser = account_subparsers.add_parser("remove", help="Remove account profile")
    account_remove_parser.add_argument("name", help="Account profile name")
    account_remove_parser.add_argument(
        "-f", "--force", action="store_true",
        help="Skip confirmation prompts"
    )

    # logs command
    logs_parser = subparsers.add_parser("logs", help="Show session logs")
    logs_parser.add_argument("session_id", nargs="?", default=None, help="Session ID (partial match OK). If omitted, shows picker.")
    logs_parser.add_argument(
        "-n", "--tail", type=int, default=100, help="Number of lines (default: 100)"
    )
    logs_parser.add_argument(
        "-f", "--follow", action="store_true", help="Follow log output"
    )

    # build command
    build_parser = subparsers.add_parser("build", help="Build Docker image")
    build_parser.add_argument(
        "--refresh", action="store_true",
        help="Rebuild base image and update configured image (preserves onboarding state)"
    )

    # setup command
    subparsers.add_parser("setup", help="Run interactive setup to create pre-configured image")

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
    if args.command in ("start", "s"):
        return app.start(
            workspace=args.workspace,
            attach=not args.no_attach,
            prompt=args.prompt,
            continue_session=args.continue_session,
            name=args.name,
            account=args.account,
        )
    elif args.command in ("list", "ls"):
        return app.list_sessions(all_states=args.all)
    elif args.command in ("attach", "a"):
        return app.attach(args.session_id)
    elif args.command in ("kill", "rm"):
        return app.kill(args.session_id, force=args.force)
    elif args.command in ("restart", "r"):
        return app.restart(args.session_id)
    elif args.command in ("shell", "sh"):
        return app.shell(args.session_id)
    elif args.command == "status":
        return app.status()
    elif args.command == "logs":
        return app.logs(args.session_id, tail=args.tail, follow=args.follow)
    elif args.command == "build":
        return app.build(refresh=args.refresh)
    elif args.command == "setup":
        return app.setup()
    elif args.command in ("teleport", "tp"):
        return app.teleport(
            workspace=args.workspace,
            attach=not args.no_attach,
            force=args.force,
        )
    elif args.command == "switch":
        return app.switch(args.session_id, args.account)
    elif args.command == "account":
        if args.account_command == "list" or args.account_command is None:
            return app.account_list()
        elif args.account_command == "add":
            return app.account_add(args.name)
        elif args.account_command == "remove":
            return app.account_remove(args.name, force=args.force)

    return 0


if __name__ == "__main__":
    sys.exit(main())
