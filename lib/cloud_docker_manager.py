"""Remote Docker container management over SSH for cloud sessions.

CloudDockerManager subclasses DockerManager, overriding _run_docker() to
execute all Docker commands on a remote cloud node via Tailscale SSH.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .cloud_manager import SSH_OPTS, ssh_host
from .config import CloudNodeConfig, Config, ProjectConfig
from .docker_manager import Container, DockerManager


class CloudDockerManager(DockerManager):
    """Docker operations executed on a remote cloud node via SSH."""

    def __init__(self, config: Config, node: CloudNodeConfig):
        super().__init__(config)
        self.node = node
        self._ssh_host = ssh_host(node)

    def _run_docker(
        self, args: list[str], check: bool = True, capture: bool = True
    ) -> subprocess.CompletedProcess:
        """Run a docker command on the remote node via Tailscale SSH."""
        cmd = [
            "ssh", *SSH_OPTS,
            self._ssh_host, "docker",
        ] + args
        return subprocess.run(cmd, check=check, capture_output=capture, text=True)

    def start_container(
        self,
        session_id: str,
        workspace_path: Path,
        env_vars: Optional[dict[str, str]] = None,
        account: Optional[str] = None,
        project_config: Optional[ProjectConfig] = None,
    ) -> Optional[str]:
        """Start a container on the cloud node with remapped paths.

        Instead of mounting local paths, mounts from the synced workspace
        on the cloud VM.
        """
        container_name = f"{self.CONTAINER_PREFIX}{session_id}"

        # Resolve account name
        account_name = account if account else self.config.accounts.default

        # Remote paths on the cloud VM
        remote_workspace = f"/home/ubuntu/workspaces/{session_id}"
        remote_anthropic = "/home/ubuntu/.anthropic"
        remote_claude = "/home/ubuntu/.claude"
        remote_gitconfig = "/home/ubuntu/.gitconfig"
        remote_claude_json = "/home/ubuntu/.claude.json"

        # Build docker run command with cloud-side paths
        args = [
            "run",
            "-d",
            "-it",
            "--name", container_name,
            # Labels for tracking
            "-l", f"{self.WORKSPACE_LABEL}={workspace_path}",
            "-l", f"{self.SESSION_LABEL}={session_id}",
            "-l", f"{self.ACCOUNT_LABEL}={account_name}",
            # Mount workspace from cloud VM
            "-v", f"{remote_workspace}:/workspace",
        ]

        # Mount credentials from cloud-side paths
        # Anthropic credentials
        args.extend(["-v", f"{remote_anthropic}:/home/claude/.anthropic:ro"])

        # Git config
        args.extend(["-v", f"{remote_gitconfig}:/home/claude/.gitconfig:ro"])

        # Claude config - selective mounts (same logic as local, different source paths)
        encoded_path = str(workspace_path).replace("/", "-").replace(".", "-").replace("_", "-")
        remote_project_dir = f"{remote_claude}/projects/{encoded_path}"

        # Create project dir on remote if it doesn't exist
        subprocess.run(
            ["ssh", *SSH_OPTS, self._ssh_host, "mkdir", "-p", remote_project_dir],
            check=False,
            capture_output=True,
        )

        args.extend(["-v", f"{remote_project_dir}:/home/claude/.claude/projects/-workspace"])

        # Credentials file
        args.extend(["-v", f"{remote_claude}/.credentials.json:/home/claude/.claude/.credentials.json:ro"])

        # Setup token
        args.extend(["-v", f"{remote_claude}/.setup-token:/home/claude/.claude/.setup-token:ro"])

        # Settings
        args.extend(["-v", f"{remote_claude}/settings.json:/home/claude/.claude/settings.json:ro"])

        # Claude.json (OAuth)
        args.extend(["-v", f"{remote_claude_json}:/home/claude/.claude.json"])

        # Extract OAuth token for login bypass
        # Read from remote node
        oauth_token = None
        result = subprocess.run(
            ["ssh", *SSH_OPTS, self._ssh_host, "cat", f"{remote_claude}/.setup-token"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            oauth_token = result.stdout.strip()
        else:
            result = subprocess.run(
                ["ssh", *SSH_OPTS, self._ssh_host, "cat", f"{remote_claude}/.credentials.json"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                try:
                    cred_data = json.loads(result.stdout)
                    oauth_token = cred_data.get("claudeAiOauth", {}).get("accessToken")
                except (json.JSONDecodeError, KeyError):
                    pass

        if oauth_token:
            args.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={oauth_token}"])

        # GitHub token (read from remote if synced)
        gh_token_result = subprocess.run(
            ["ssh", *SSH_OPTS, self._ssh_host, "cat", "/home/ubuntu/.config/remote-claude/github-token"],
            capture_output=True,
            text=True,
            check=False,
        )
        if gh_token_result.returncode == 0 and gh_token_result.stdout.strip():
            args.extend(["-e", f"GH_TOKEN={gh_token_result.stdout.strip()}"])

        # 1Password socket (created by op-bridge systemd service on cloud)
        args.extend(["-v", "/run/op-wrapper:/run/op-wrapper"])

        # Mount hooks from cloud-side repo
        args.extend(["-v", "/home/ubuntu/remote-claude/hooks:/home/claude/.rc-hooks:ro"])

        # Pass project setup commands via environment variable
        if project_config and project_config.setup_commands:
            setup_script = "#!/bin/bash\nset -e\n" + "\n".join(project_config.setup_commands) + "\n"
            args.extend(["-e", f"RC_SETUP_SCRIPT={setup_script}"])

        # Network mode — cloud containers use bridge (no proxy needed)
        # The cloud VM itself provides the network boundary

        # Environment variables
        if env_vars:
            for key, value in env_vars.items():
                args.extend(["-e", f"{key}={value}"])

        # Use effective image (check remote)
        result = self._run_docker(
            ["images", "--format", "table {{.Repository}}:{{.Tag}}"],
            check=False,
            capture=True,
        )
        if result.returncode == 0 and self.CONFIGURED_IMAGE in result.stdout:
            args.append(self.CONFIGURED_IMAGE)
        else:
            args.append(self.image)

        result = self._run_docker(args, check=False)
        if result.returncode != 0:
            return None

        return result.stdout.strip()[:12]

    def attach_to_container(self, container_id_or_name: str) -> None:
        """Attach to a remote container (replaces current process).

        Uses SSH to docker attach on the remote node.
        """
        os.execvp("ssh", [
            "ssh", "-t",
            *SSH_OPTS,
            self._ssh_host,
            "docker", "attach", container_id_or_name,
        ])

    def exec_in_container(
        self, container_id_or_name: str, command: list[str], interactive: bool = False
    ) -> subprocess.CompletedProcess:
        """Execute a command in a remote container."""
        if interactive:
            os.execvp("ssh", [
                "ssh", "-t",
                *SSH_OPTS,
                self._ssh_host,
                "docker", "exec", "-it", container_id_or_name,
            ] + command)

        return self._run_docker(
            ["exec", container_id_or_name] + command,
            check=False,
            capture=True,
        )
