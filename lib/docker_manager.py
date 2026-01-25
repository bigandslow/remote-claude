"""Docker container management for remote-claude."""

import atexit
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config


# Track temp files for secure cleanup (WIF tokens, credential configs)
_TEMP_FILES_TO_CLEANUP: set[str] = set()


def _cleanup_temp_files() -> None:
    """Clean up temporary credential files on exit.

    This ensures WIF tokens and modified credential configs don't persist
    on disk after the process exits.
    """
    for filepath in list(_TEMP_FILES_TO_CLEANUP):
        try:
            os.unlink(filepath)
            _TEMP_FILES_TO_CLEANUP.discard(filepath)
        except OSError:
            pass


atexit.register(_cleanup_temp_files)


def _generate_wif_token(credential_config_path: Path) -> Optional[tuple[str, str]]:
    """Generate a WIF identity token on the host.

    Reads the WIF credential config to extract the audience, then uses
    gcloud to generate an identity token. This token is injected into
    the container instead of mounting user credentials.

    Args:
        credential_config_path: Path to the WIF credential configuration JSON

    Returns:
        Tuple of (token, audience) if successful, None otherwise
    """
    try:
        config = json.loads(credential_config_path.read_text())

        # Check if this is a WIF config
        if config.get("type") != "external_account":
            return None

        # Extract the audience for the identity token
        audience = config.get("audience", "")
        if not audience:
            return None

        # Generate identity token using gcloud
        result = subprocess.run(
            ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            return None

        token = result.stdout.strip()
        return (token, audience)

    except (json.JSONDecodeError, FileNotFoundError, KeyError):
        return None


def _create_container_wif_config(
    original_config_path: Path, token_path_in_container: str
) -> dict:
    """Create a WIF credential config for container use.

    Converts executable-sourced credentials to file-sourced credentials,
    pointing to the injected token file.

    Args:
        original_config_path: Path to the original WIF credential config
        token_path_in_container: Path where the token will be mounted in container

    Returns:
        Modified credential config dict for container use
    """
    config = json.loads(original_config_path.read_text())

    # Replace executable source with file source
    config["credential_source"] = {
        "file": token_path_in_container,
        "format": {"type": "text"},
    }

    return config


@dataclass
class Container:
    """Represents a Docker container."""

    id: str
    name: str
    status: str
    image: str
    created: str
    workspace: Optional[str] = None
    account: Optional[str] = None


class DockerManager:
    """Manages Docker containers for remote-claude sessions."""

    CONTAINER_PREFIX = "rc-"
    WORKSPACE_LABEL = "rc.workspace"
    SESSION_LABEL = "rc.session"
    ACCOUNT_LABEL = "rc.account"
    PROXY_IMAGE = "rc-proxy:latest"
    PROXY_PREFIX = "rc-proxy-"
    NETWORK_PREFIX = "rc-net-"

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

    def proxy_image_exists(self) -> bool:
        """Check if the proxy image exists."""
        result = self._run_docker(
            ["images", "--format", "table {{.Repository}}:{{.Tag}}"],
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            return False
        return self.PROXY_IMAGE in result.stdout

    def build_proxy_image(self) -> bool:
        """Build the proxy Docker image for network allowlisting."""
        context_path = Path(__file__).parent.parent / "docker" / "proxy"
        if not context_path.exists():
            return False

        result = self._run_docker(
            ["build", "-t", self.PROXY_IMAGE, str(context_path)],
            check=False,
            capture=False,
        )
        return result.returncode == 0

    def _create_proxy_network(self, session_id: str) -> Optional[str]:
        """Create an isolated network for proxy-based filtering.

        Returns:
            Network name if successful, None otherwise
        """
        network_name = f"{self.NETWORK_PREFIX}{session_id}"
        result = self._run_docker(
            ["network", "create", "--internal", network_name],
            check=False,
        )
        return network_name if result.returncode == 0 else None

    def _start_proxy_container(self, session_id: str, network_name: str) -> Optional[str]:
        """Start a proxy container for network filtering.

        Returns:
            Proxy container ID if successful, None otherwise
        """
        proxy_name = f"{self.PROXY_PREFIX}{session_id}"
        result = self._run_docker(
            [
                "run", "-d",
                "--name", proxy_name,
                "--network", network_name,
                self.PROXY_IMAGE,
            ],
            check=False,
        )
        return result.stdout.strip()[:12] if result.returncode == 0 else None

    def _get_container_ip(self, container_name: str, network_name: str) -> Optional[str]:
        """Get the IP address of a container on a specific network."""
        result = self._run_docker(
            [
                "inspect", "-f",
                f"{{{{.NetworkSettings.Networks.{network_name}.IPAddress}}}}",
                container_name,
            ],
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return None

    def _cleanup_proxy(self, session_id: str) -> None:
        """Clean up proxy container and network for a session."""
        proxy_name = f"{self.PROXY_PREFIX}{session_id}"
        network_name = f"{self.NETWORK_PREFIX}{session_id}"

        # Stop and remove proxy container
        self._run_docker(["stop", proxy_name], check=False)
        self._run_docker(["rm", "-f", proxy_name], check=False)

        # Remove network
        self._run_docker(["network", "rm", network_name], check=False)

    def start_container(
        self,
        session_id: str,
        workspace_path: Path,
        env_vars: Optional[dict[str, str]] = None,
        account: Optional[str] = None,
    ) -> Optional[str]:
        """Start a new container for a Claude session.

        Args:
            session_id: Unique session identifier
            workspace_path: Path to the worktree/workspace to mount
            env_vars: Optional environment variables
            account: Account profile name (uses default if None)

        Returns:
            Container ID if successful, None otherwise
        """
        container_name = f"{self.CONTAINER_PREFIX}{session_id}"

        # Resolve account name
        account_name = account if account else self.config.accounts.default

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
            "-l",
            f"{self.ACCOUNT_LABEL}={account_name}",
            # Mount workspace read-write
            "-v",
            f"{workspace_path}:/workspace",
        ]

        # Mount credentials read-only (resolved for account)
        # Priority: deploy keys > bot account > personal credentials
        creds = self.config.get_credentials_for_account(account_name)

        if creds.anthropic.exists():
            args.extend(["-v", f"{creds.anthropic}:/home/claude/.anthropic:ro"])

        # Determine which git/ssh credentials to use
        # Deploy keys take precedence if configured
        use_deploy_keys = (
            creds.deploy_keys_ssh
            and creds.deploy_keys_ssh.exists()
            and creds.deploy_keys_registry
            and creds.deploy_keys_registry.exists()
        )

        if use_deploy_keys:
            # Use deploy keys
            if creds.deploy_keys_git and creds.deploy_keys_git.exists():
                args.extend(["-v", f"{creds.deploy_keys_git}:/home/claude/.gitconfig:ro"])
            args.extend(["-v", f"{creds.deploy_keys_ssh}:/home/claude/.ssh:ro"])
            # Mount registry for entrypoint to set up git insteadOf rules
            args.extend(["-v", f"{creds.deploy_keys_registry}:/home/claude/.deploy-keys-registry.json:ro"])
            args.extend(["-e", "RC_USE_DEPLOY_KEYS=1"])
        else:
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

        # Claude state file (~/.claude.json) - contains oauthAccount for login bypass
        claude_json = Path.home() / ".claude.json"
        if claude_json.exists():
            args.extend(["-v", f"{claude_json}:/home/claude/.claude-host.json:ro"])

        # Extract OAuth token for login bypass (CLAUDE_CODE_OAUTH_TOKEN)
        # Priority: setup-token file > credentials.json
        setup_token_file = creds.claude / ".setup-token"
        credentials_file = creds.claude / ".credentials.json"

        oauth_token = None
        if setup_token_file.exists():
            # Use long-lived setup token (generated via `claude setup-token`)
            oauth_token = setup_token_file.read_text().strip()
        elif credentials_file.exists():
            # Fall back to credentials.json token
            try:
                cred_data = json.loads(credentials_file.read_text())
                oauth_token = cred_data.get("claudeAiOauth", {}).get("accessToken")
            except (json.JSONDecodeError, KeyError):
                pass

        if oauth_token:
            args.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={oauth_token}"])

        # GCP credentials - handle WIF or service account key
        wif_temp_files: list[str] = []  # Track for cleanup after container starts
        if creds.claude_gcp and creds.claude_gcp.exists():
            # Check if this is a WIF credential config
            wif_result = _generate_wif_token(creds.claude_gcp)

            if wif_result:
                # WIF authentication - inject token instead of mounting user creds
                token, audience = wif_result
                token_path_in_container = "/home/claude/.config/gcloud/wif-token"
                cred_config_path_in_container = "/home/claude/.config/gcloud/application_default_credentials.json"

                # Create temp files for token and modified config
                # These are tracked for cleanup after Docker mounts them
                token_file = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".wif-token", delete=False, prefix="rc-"
                )
                token_file.write(token)
                token_file.close()
                wif_temp_files.append(token_file.name)
                _TEMP_FILES_TO_CLEANUP.add(token_file.name)

                # Create container-compatible credential config
                container_config = _create_container_wif_config(
                    creds.claude_gcp, token_path_in_container
                )
                config_file = tempfile.NamedTemporaryFile(
                    mode="w", suffix=".wif-config.json", delete=False, prefix="rc-"
                )
                json.dump(container_config, config_file)
                config_file.close()
                wif_temp_files.append(config_file.name)
                _TEMP_FILES_TO_CLEANUP.add(config_file.name)

                # Mount token and config into container
                args.extend(["-v", f"{token_file.name}:{token_path_in_container}:ro"])
                args.extend(["-v", f"{config_file.name}:{cred_config_path_in_container}:ro"])
                args.extend(["-e", f"GOOGLE_APPLICATION_CREDENTIALS={cred_config_path_in_container}"])
            else:
                # Regular service account key - mount directly
                args.extend(["-v", f"{creds.claude_gcp}:/home/claude/.config/gcloud/application_default_credentials.json:ro"])
                args.extend(["-e", "GOOGLE_APPLICATION_CREDENTIALS=/home/claude/.config/gcloud/application_default_credentials.json"])

        # Mount safety hooks for YOLO mode protection
        hooks_dir = Path(__file__).parent.parent / "hooks"
        if hooks_dir.exists():
            args.extend(["-v", f"{hooks_dir}:/home/claude/.rc-hooks:ro"])

        # Network mode
        proxy_container_id = None
        network_name = None

        if self.config.network.mode == "none":
            args.extend(["--network", "none"])
        elif self.config.network.mode == "allowlist":
            # Create isolated network with proxy for domain filtering
            if not self.proxy_image_exists():
                # Auto-build proxy image if needed
                if not self.build_proxy_image():
                    print("Warning: Failed to build proxy image, using bridge network")
                else:
                    pass  # Continue to set up proxy

            if self.proxy_image_exists():
                # Create isolated network
                network_name = self._create_proxy_network(session_id)
                if network_name:
                    # Start proxy container
                    proxy_container_id = self._start_proxy_container(session_id, network_name)
                    if proxy_container_id:
                        # Get proxy IP
                        proxy_name = f"{self.PROXY_PREFIX}{session_id}"
                        proxy_ip = self._get_container_ip(proxy_name, network_name)
                        if proxy_ip:
                            # Configure Claude container to use proxy
                            args.extend(["--network", network_name])
                            args.extend(["-e", f"HTTP_PROXY=http://{proxy_ip}:3128"])
                            args.extend(["-e", f"HTTPS_PROXY=http://{proxy_ip}:3128"])
                            args.extend(["-e", f"http_proxy=http://{proxy_ip}:3128"])
                            args.extend(["-e", f"https_proxy=http://{proxy_ip}:3128"])
                            args.extend(["-e", "NO_PROXY=localhost,127.0.0.1"])
                        else:
                            # Cleanup on failure
                            self._cleanup_proxy(session_id)
                            print("Warning: Failed to get proxy IP, using bridge network")
                    else:
                        # Cleanup on failure
                        self._cleanup_proxy(session_id)
                        print("Warning: Failed to start proxy, using bridge network")
                else:
                    print("Warning: Failed to create network, using bridge network")
        # else: default bridge network (no isolation)

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

    def remove_container(self, container_id_or_name: str, force: bool = False, cleanup_proxy: bool = True) -> bool:
        """Remove a container.

        Args:
            container_id_or_name: Container ID or name
            force: Force removal even if running
            cleanup_proxy: Also clean up associated proxy container and network

        Returns:
            True if removed successfully
        """
        # Extract session_id for proxy cleanup
        session_id = None
        if cleanup_proxy and container_id_or_name.startswith(self.CONTAINER_PREFIX):
            session_id = container_id_or_name[len(self.CONTAINER_PREFIX):]

        args = ["rm"]
        if force:
            args.append("-f")
        args.append(container_id_or_name)

        result = self._run_docker(args, check=False, capture=True)

        # Clean up associated proxy if this was our container
        if session_id and result.returncode == 0:
            self._cleanup_proxy(session_id)

        return result.returncode == 0

    def list_containers(self, all_states: bool = False) -> list[Container]:
        """List remote-claude containers.

        Args:
            all_states: Include stopped containers

        Returns:
            List of Container objects
        """
        # Use non-table format with tab separator (table format uses spaces, not tabs)
        format_str = "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.CreatedAt}}"
        args = ["ps", "--format", format_str]
        if all_states:
            args.insert(1, "-a")

        result = self._run_docker(args, check=False)
        if result.returncode != 0:
            return []

        containers = []
        lines = result.stdout.strip().split("\n")
        for line in lines:
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

        # Get workspace and account labels
        for container in containers:
            inspect_result = self._run_docker(
                [
                    "inspect",
                    "--format",
                    f"{{{{index .Config.Labels \"{self.WORKSPACE_LABEL}\"}}}}|{{{{index .Config.Labels \"{self.ACCOUNT_LABEL}\"}}}}",
                    container.id,
                ],
                check=False,
            )
            if inspect_result.returncode == 0:
                parts = inspect_result.stdout.strip().split("|")
                container.workspace = parts[0] if parts[0] else None
                container.account = parts[1] if len(parts) > 1 and parts[1] else None

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
