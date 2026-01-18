"""Docker container management for remote-claude."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config


@dataclass
class Container:
    """Represents a Docker container."""

    id: str
    name: str
    status: str
    image: str
    created: str
    workspace: Optional[str] = None


class DockerManager:
    """Manages Docker containers for remote-claude sessions."""

    CONTAINER_PREFIX = "rc-"
    WORKSPACE_LABEL = "rc.workspace"
    SESSION_LABEL = "rc.session"

    def __init__(self, config: Config):
        self.config = config
        self.image = config.docker.image

    def _run_docker(
        self, args: list[str], check: bool = True, capture: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a docker command."""
        cmd = ["docker"] + args
        return subprocess.run(cmd, check=check, capture_output=capture, text=True)

    def image_exists(self) -> bool:
        """Check if the remote-claude image exists."""
        # Use docker images and grep for the image name
        # (filtering via docker args hangs on some Docker versions)
        result = self._run_docker(
            ["images", "--format", "table {{.Repository}}:{{.Tag}}"],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            return False
        return self.image in result.stdout

    def build_image(self, context_path: Optional[Path] = None) -> bool:
        """Build the remote-claude Docker image.

        Args:
            context_path: Path to Dockerfile directory

        Returns:
            True if build succeeded
        """
        if context_path is None:
            # Default to docker/ directory relative to this file
            context_path = Path(__file__).parent.parent / "docker"

        result = self._run_docker(
            ["build", "-t", self.image, str(context_path)],
            check=False,
            capture=False,  # Show build output
        )
        return result.returncode == 0

    def start_container(
        self,
        session_id: str,
        workspace_path: Path,
        env_vars: Optional[dict[str, str]] = None,
    ) -> Optional[str]:
        """Start a new container for a Claude session.

        Args:
            session_id: Unique session identifier
            workspace_path: Path to the worktree/workspace to mount
            env_vars: Optional environment variables

        Returns:
            Container ID if successful, None otherwise
        """
        container_name = f"{self.CONTAINER_PREFIX}{session_id}"

        # Build docker run command
        args = [
            "run",
            "-d",
            "-it",  # Interactive with TTY for tmux attachment
            "--name",
            container_name,
            # Labels for tracking
            "-l",
            f"{self.WORKSPACE_LABEL}={workspace_path}",
            "-l",
            f"{self.SESSION_LABEL}={session_id}",
            # Mount workspace read-write
            "-v",
            f"{workspace_path}:/workspace",
        ]

        # Mount credentials read-only
        # Use dedicated Claude credentials if configured, otherwise fall back to personal
        creds = self.config.credentials

        if creds.anthropic.exists():
            args.extend(["-v", f"{creds.anthropic}:/home/claude/.anthropic:ro"])

        # Git config - prefer dedicated claude_git if set
        git_config = creds.claude_git if creds.claude_git and creds.claude_git.exists() else creds.git
        if git_config.exists():
            args.extend(["-v", f"{git_config}:/home/claude/.gitconfig:ro"])

        # SSH keys - prefer dedicated claude_ssh if set
        ssh_dir = creds.claude_ssh if creds.claude_ssh and creds.claude_ssh.exists() else creds.ssh
        if ssh_dir.exists():
            args.extend(["-v", f"{ssh_dir}:/home/claude/.ssh:ro"])

        # Claude settings (mounted to separate path, merged in entrypoint)
        if creds.claude.exists():
            args.extend(["-v", f"{creds.claude}:/home/claude/.claude-host:ro"])

        # GCP credentials - mount service account key if configured
        if creds.claude_gcp and creds.claude_gcp.exists():
            args.extend(["-v", f"{creds.claude_gcp}:/home/claude/.config/gcloud/application_default_credentials.json:ro"])
            args.extend(["-e", "GOOGLE_APPLICATION_CREDENTIALS=/home/claude/.config/gcloud/application_default_credentials.json"])

        # Mount safety hooks for YOLO mode protection
        hooks_dir = Path(__file__).parent.parent / "hooks"
        if hooks_dir.exists():
            args.extend(["-v", f"{hooks_dir}:/home/claude/.rc-hooks:ro"])

        # Network mode
        if self.config.network.mode == "none":
            args.extend(["--network", "none"])
        # TODO: Implement allowlist mode with proxy

        # Environment variables
        if env_vars:
            for key, value in env_vars.items():
                args.extend(["-e", f"{key}={value}"])

        # Image
        args.append(self.image)

        result = self._run_docker(args, check=False)
        if result.returncode != 0:
            return None

        return result.stdout.strip()[:12]  # Short container ID

    def stop_container(self, container_id_or_name: str) -> bool:
        """Stop a running container.

        Args:
            container_id_or_name: Container ID or name

        Returns:
            True if stopped successfully
        """
        result = self._run_docker(
            ["stop", container_id_or_name], check=False, capture=True
        )
        return result.returncode == 0

    def remove_container(self, container_id_or_name: str, force: bool = False) -> bool:
        """Remove a container.

        Args:
            container_id_or_name: Container ID or name
            force: Force removal even if running

        Returns:
            True if removed successfully
        """
        args = ["rm"]
        if force:
            args.append("-f")
        args.append(container_id_or_name)

        result = self._run_docker(args, check=False, capture=True)
        return result.returncode == 0

    def list_containers(self, all_states: bool = False) -> list[Container]:
        """List remote-claude containers.

        Args:
            all_states: Include stopped containers

        Returns:
            List of Container objects
        """
        # Use table format (plain format can hang on some Docker versions)
        format_str = "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.CreatedAt}}"
        args = ["ps", "--format", format_str]
        if all_states:
            args.insert(1, "-a")

        result = self._run_docker(args, check=False)
        if result.returncode != 0:
            return []

        containers = []
        lines = result.stdout.strip().split("\n")
        # Skip header line (table format includes headers)
        for line in lines[1:]:
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 5:
                name = parts[1]
                # Filter for our containers (no --filter to avoid hang)
                if not name.startswith(self.CONTAINER_PREFIX):
                    continue
                containers.append(
                    Container(
                        id=parts[0],
                        name=name,
                        status=parts[2],
                        image=parts[3],
                        created=parts[4],
                    )
                )

        # Get workspace labels
        for container in containers:
            inspect_result = self._run_docker(
                [
                    "inspect",
                    "--format",
                    f"{{{{index .Config.Labels \"{self.WORKSPACE_LABEL}\"}}}}",
                    container.id,
                ],
                check=False,
            )
            if inspect_result.returncode == 0:
                container.workspace = inspect_result.stdout.strip() or None

        return containers

    def get_container(self, session_id: str) -> Optional[Container]:
        """Get a specific container by session ID.

        Args:
            session_id: Session identifier

        Returns:
            Container if found, None otherwise
        """
        container_name = f"{self.CONTAINER_PREFIX}{session_id}"
        containers = self.list_containers(all_states=True)
        for c in containers:
            if c.name == container_name or c.id.startswith(session_id):
                return c
        return None

    def exec_in_container(
        self, container_id_or_name: str, command: list[str], interactive: bool = False
    ) -> subprocess.CompletedProcess:
        """Execute a command in a running container.

        Args:
            container_id_or_name: Container ID or name
            command: Command to execute
            interactive: Whether to attach stdin/stdout

        Returns:
            CompletedProcess result
        """
        args = ["exec"]
        if interactive:
            args.extend(["-it"])
        args.append(container_id_or_name)
        args.extend(command)

        return self._run_docker(args, check=False, capture=not interactive)

    def attach_to_container(self, container_id_or_name: str) -> None:
        """Attach to a running container (replaces current process).

        Args:
            container_id_or_name: Container ID or name
        """
        import os

        os.execvp("docker", ["docker", "attach", container_id_or_name])

    def logs(
        self, container_id_or_name: str, tail: int = 100, follow: bool = False
    ) -> Optional[str]:
        """Get container logs.

        Args:
            container_id_or_name: Container ID or name
            tail: Number of lines to show
            follow: Follow log output

        Returns:
            Log output or None if failed
        """
        args = ["logs", "--tail", str(tail)]
        if follow:
            args.append("-f")
        args.append(container_id_or_name)

        result = self._run_docker(args, check=False, capture=not follow)
        if follow:
            return None
        return result.stdout if result.returncode == 0 else None
