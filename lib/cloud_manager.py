"""Cloud VM lifecycle management for remote-claude.

Manages cloud VMs (Hetzner or DigitalOcean) with Tailscale connectivity,
workspace sync, and 1Password bridge support.
"""

import json
import os
import signal
import subprocess
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .cloud_provider import CloudProvider, get_provider
from .config import CloudConfig, CloudNodeConfig, Config, save_config

# Default SSH user on cloud VMs (cloud-init creates this user)
CLOUD_USER = "ubuntu"

# SSH options for cloud node connections: auto-accept new host keys, short timeout.
SSH_OPTS = [
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
]

# Rsync uses -e to pass SSH options
RSYNC_SSH = ["rsync", "-az", "-e", "ssh " + " ".join(SSH_OPTS)]


def ssh_host(node: "CloudNodeConfig") -> str:
    """Return user@host string for SSH to a cloud node."""
    host = node.tailscale_hostname or node.tailscale_ip
    return f"{CLOUD_USER}@{host}"


@dataclass
class CloudNode:
    """Represents a provisioned cloud VM with runtime status."""

    name: str
    server_id: int
    server_type: str
    tailscale_ip: str
    tailscale_hostname: str
    region: str
    status: str  # running/stopped/provisioning/unknown
    session_count: int = 0


class CloudManager:
    """Manages cloud VMs for remote-claude sessions."""

    def __init__(self, config: Config):
        self.config = config
        self.cloud = config.cloud
        self._api_token: Optional[str] = None
        self._provider: Optional[CloudProvider] = None

    # ── Cloud provider API ─────────────────────────────────────

    def _get_api_token(self) -> str:
        """Get cloud provider API token via op-wrapper."""
        if self._api_token:
            return self._api_token

        ref = self.cloud.api_token_ref
        if not ref:
            raise RuntimeError("cloud.api_token_ref not configured")

        result = subprocess.run(
            ["op", "read", ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to read API token: {result.stderr.strip()}")

        self._api_token = result.stdout.strip()
        return self._api_token

    def _get_provider(self) -> CloudProvider:
        """Get the configured cloud provider instance."""
        if self._provider:
            return self._provider
        token = self._get_api_token()
        self._provider = get_provider(self.cloud.provider, token)
        return self._provider

    # ── Tailscale auth ──────────────────────────────────────────

    def _get_tailscale_access_token(self) -> str:
        """Get a Tailscale OAuth access token.

        Uses OAuth client credentials to obtain a short-lived access token
        for the Tailscale API.
        """
        import urllib.request
        import urllib.error

        client_id = self.cloud.tailscale_oauth_client_id
        if not client_id:
            raise RuntimeError("tailscale_oauth_client_id not configured")

        secret_ref = self.cloud.tailscale_oauth_secret_ref
        if not secret_ref:
            raise RuntimeError("tailscale_oauth_secret_ref not configured")

        result = subprocess.run(
            ["op", "read", secret_ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to read Tailscale OAuth secret: {result.stderr.strip()}")
        client_secret = result.stdout.strip()

        token_data = f"client_id={client_id}&client_secret={client_secret}&grant_type=client_credentials"
        req = urllib.request.Request(
            "https://api.tailscale.com/api/v2/oauth/token",
            data=token_data.encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                oauth_resp = json.loads(resp.read().decode())
                return oauth_resp["access_token"]
        except (urllib.error.HTTPError, KeyError) as e:
            raise RuntimeError(f"Failed to get Tailscale OAuth token: {e}")

    def _get_tailscale_auth_key(self) -> str:
        """Generate a one-time Tailscale auth key for VM provisioning."""
        import urllib.request
        import urllib.error

        access_token = self._get_tailscale_access_token()

        auth_key_data = {
            "capabilities": {
                "devices": {
                    "create": {
                        "reusable": False,
                        "ephemeral": False,
                        "preauthorized": True,
                        "tags": ["tag:cloud"],
                    }
                }
            },
            "expirySeconds": 3600,
        }

        req = urllib.request.Request(
            "https://api.tailscale.com/api/v2/tailnet/-/keys",
            data=json.dumps(auth_key_data).encode(),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                key_resp = json.loads(resp.read().decode())
                return key_resp["key"]
        except (urllib.error.HTTPError, KeyError) as e:
            raise RuntimeError(f"Failed to create Tailscale auth key: {e}")

    def _remove_tailscale_device(self, hostname: str) -> None:
        """Remove a device from the tailnet by hostname (best-effort).

        Looks up the device by hostname via the Tailscale API and deletes it.
        """
        import urllib.request
        import urllib.error

        try:
            access_token = self._get_tailscale_access_token()
        except RuntimeError:
            return  # Can't clean up without credentials

        # List devices and find matching hostname
        req = urllib.request.Request(
            "https://api.tailscale.com/api/v2/tailnet/-/devices",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                devices = json.loads(resp.read().decode()).get("devices", [])
        except (urllib.error.HTTPError, KeyError):
            return

        for device in devices:
            if device.get("hostname") == hostname:
                device_id = device.get("id")
                if not device_id:
                    continue
                try:
                    del_req = urllib.request.Request(
                        f"https://api.tailscale.com/api/v2/device/{device_id}",
                        headers={"Authorization": f"Bearer {access_token}"},
                        method="DELETE",
                    )
                    urllib.request.urlopen(del_req, timeout=15)
                    print(f"Removed '{hostname}' from tailnet")
                except urllib.error.HTTPError:
                    pass

    # ── Local Tailscale IP ──────────────────────────────────────

    def _get_local_tailscale_ip(self) -> str:
        """Get the local machine's Tailscale IP address."""
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Could not get local Tailscale IP. Is Tailscale running?")
        return result.stdout.strip()

    # ── Cloud-init ──────────────────────────────────────────────

    def _render_cloud_init(
        self, hostname: str, ts_auth_key: str, local_ts_ip: str
    ) -> str:
        """Render cloud-init user data for VM provisioning.

        Installs Docker, Tailscale (with SSH), socat, clones repo and
        builds the Docker image. Sets up systemd service for 1Password
        bridge.
        """
        return textwrap.dedent(f"""\
            #cloud-config
            hostname: {hostname}

            packages:
              - apt-transport-https
              - ca-certificates
              - curl
              - gnupg
              - socat
              - rsync
              - git
              - jq

            write_files:
              - path: /etc/systemd/system/op-bridge.service
                content: |
                  [Unit]
                  Description=1Password proxy bridge
                  After=tailscaled.service

                  [Service]
                  ExecStart=/usr/bin/socat \\
                      UNIX-LISTEN:/run/op-wrapper/daemon.sock,fork,unlink-early,mode=0666 \\
                      TCP:{local_ts_ip}:7779
                  ExecStartPre=/bin/mkdir -p /run/op-wrapper
                  Restart=always
                  User=root

                  [Install]
                  WantedBy=multi-user.target

            runcmd:
              # Install Docker
              - curl -fsSL https://get.docker.com | sh
              - systemctl enable docker
              - systemctl start docker

              # Install Tailscale
              - curl -fsSL https://tailscale.com/install.sh | sh
              - tailscale up --auth-key={ts_auth_key} --ssh --hostname={hostname}

              # Enable 1Password bridge service
              - systemctl daemon-reload
              - systemctl enable op-bridge.service
              - systemctl start op-bridge.service

              # Create ubuntu user (exists on Hetzner images, not on DO)
              - id ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu
              - usermod -aG docker ubuntu

              # Create ubuntu user home structure
              - mkdir -p /home/ubuntu/workspaces
              - mkdir -p /home/ubuntu/.anthropic
              - mkdir -p /home/ubuntu/.claude
              - mkdir -p /home/ubuntu/.config/remote-claude
              - chown -R ubuntu:ubuntu /home/ubuntu

              # Clone and build remote-claude
              - su - ubuntu -c "git clone https://github.com/cstrahorn/remote-claude.git /home/ubuntu/remote-claude"
              - su - ubuntu -c "cd /home/ubuntu/remote-claude && docker build -t remote-claude:latest docker/"
        """)

    # ── Node lifecycle ──────────────────────────────────────────

    def provision_node(
        self,
        name: str,
        server_type: Optional[str] = None,
        region: Optional[str] = None,
    ) -> CloudNode:
        """Provision a new cloud VM.

        Creates the server with cloud-init, waits for Tailscale to join,
        and saves the node config.

        Args:
            name: Node name (e.g., "rc-1")
            server_type: Server type (default from provider)
            region: Region shorthand (default from provider)

        Returns:
            CloudNode with provisioned details
        """
        provider = self._get_provider()
        server_type = server_type or self.cloud.default_server_type or provider.default_server_type()
        region = region or self.cloud.default_region or provider.default_region()

        # Check for duplicate names
        for node in self.cloud.nodes:
            if node.name == name:
                raise RuntimeError(f"Node '{name}' already exists")

        print(f"Generating Tailscale auth key...")
        ts_auth_key = self._get_tailscale_auth_key()

        print(f"Getting local Tailscale IP...")
        local_ts_ip = self._get_local_tailscale_ip()

        print(f"Rendering cloud-init template...")
        user_data = self._render_cloud_init(name, ts_auth_key, local_ts_ip)

        print(f"Creating {self.cloud.provider} server '{name}' ({server_type} in {region})...")
        result = provider.create_server(name, server_type, region, user_data)
        server_id = result["server_id"]

        print(f"Server created (ID: {server_id}). Waiting for Tailscale join...")

        # Wait for Tailscale to pick up the node (poll tailscale status)
        tailscale_ip = ""
        tailscale_hostname = ""
        for attempt in range(60):  # 5 minutes max
            time.sleep(5)
            try:
                result = subprocess.run(
                    ["tailscale", "status", "--json"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode == 0:
                    status = json.loads(result.stdout)
                    peers = status.get("Peer", {})
                    for _, peer in peers.items():
                        peer_hostname = peer.get("HostName", "")
                        if peer_hostname == name:
                            ips = peer.get("TailscaleIPs", [])
                            if ips:
                                tailscale_ip = ips[0]
                                tailscale_hostname = peer.get("DNSName", "").rstrip(".")
                                break
                if tailscale_ip:
                    break
            except Exception:
                pass

            if (attempt + 1) % 6 == 0:
                print(f"  Still waiting... ({(attempt + 1) * 5}s)")

        if not tailscale_ip:
            print(f"Warning: Tailscale did not join within 5 minutes.")
            print(f"  Server ID: {server_id}")
            print(f"  Check: ssh root@<public-ip> journalctl -u tailscaled")
            # Save with empty tailscale info so user can update later
            tailscale_hostname = name

        # Save node config
        node_config = CloudNodeConfig(
            name=name,
            server_id=server_id,
            server_type=server_type,
            tailscale_ip=tailscale_ip,
            tailscale_hostname=tailscale_hostname,
            region=region,
        )
        self.cloud.nodes.append(node_config)
        save_config(self.config)

        return CloudNode(
            name=name,
            server_id=server_id,
            server_type=server_type,
            tailscale_ip=tailscale_ip,
            tailscale_hostname=tailscale_hostname,
            region=region,
            status="running",
        )

    def destroy_node(self, name: str) -> bool:
        """Destroy a cloud VM and remove from config.

        Args:
            name: Node name to destroy

        Returns:
            True if destroyed successfully
        """
        node_config = None
        for nc in self.cloud.nodes:
            if nc.name == name:
                node_config = nc
                break

        if not node_config:
            print(f"Error: Node '{name}' not found in config")
            return False

        if node_config.server_id:
            print(f"Destroying {self.cloud.provider} server {node_config.server_id}...")
            try:
                provider = self._get_provider()
                provider.destroy_server(node_config.server_id)
            except RuntimeError as e:
                print(f"Warning: API error: {e}")

        # Remove from Tailscale (best-effort)
        if node_config.name:
            self._remove_tailscale_device(node_config.name)

        # Remove from config
        self.cloud.nodes = [n for n in self.cloud.nodes if n.name != name]
        save_config(self.config)

        print(f"Node '{name}' destroyed and removed from config.")
        return True

    def list_nodes(self) -> list[CloudNode]:
        """List all configured cloud nodes with live status.

        Returns:
            List of CloudNode with current status
        """
        nodes = []

        # Get session counts from registry
        registry = SessionRegistry.load()

        for nc in self.cloud.nodes:
            # Count sessions on this node
            session_count = sum(
                1 for s in registry.sessions.values()
                if s.get("location") == "cloud" and s.get("node") == nc.name
            )

            # Check if reachable via Tailscale SSH
            status = "unknown"
            if nc.tailscale_hostname:
                result = subprocess.run(
                    ["ssh", *SSH_OPTS, nc.tailscale_hostname, "echo", "ok"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                status = "running" if result.returncode == 0 else "unreachable"
            elif nc.server_id:
                status = "no-tailscale"

            nodes.append(CloudNode(
                name=nc.name,
                server_id=nc.server_id,
                server_type=nc.server_type,
                tailscale_ip=nc.tailscale_ip,
                tailscale_hostname=nc.tailscale_hostname,
                region=nc.region,
                status=status,
                session_count=session_count,
            ))

        return nodes

    def node_status(self, name: str) -> Optional[CloudNode]:
        """Get status of a specific node.

        Args:
            name: Node name

        Returns:
            CloudNode if found, None otherwise
        """
        nodes = self.list_nodes()
        for node in nodes:
            if node.name == name:
                return node
        return None

    def get_node_config(self, name: str) -> Optional[CloudNodeConfig]:
        """Get the config for a named node.

        Args:
            name: Node name

        Returns:
            CloudNodeConfig if found, None otherwise
        """
        for nc in self.cloud.nodes:
            if nc.name == name:
                return nc
        return None

    def select_node(self, preferred: Optional[str] = None) -> Optional[CloudNodeConfig]:
        """Select a node for a new session based on placement strategy.

        Args:
            preferred: Preferred node name (used with manual placement)

        Returns:
            CloudNodeConfig for the selected node, or None
        """
        if not self.cloud.nodes:
            return None

        if preferred:
            return self.get_node_config(preferred)

        if self.cloud.placement == "manual":
            return None  # Caller must specify

        registry = SessionRegistry.load()

        if self.cloud.placement == "round-robin":
            # Simple round-robin across nodes
            counts = {}
            for nc in self.cloud.nodes:
                counts[nc.name] = sum(
                    1 for s in registry.sessions.values()
                    if s.get("node") == nc.name and s.get("location") == "cloud"
                )
            return min(
                self.cloud.nodes,
                key=lambda nc: counts.get(nc.name, 0),
            )

        # "auto" — place on node with fewest sessions under max
        best = None
        best_count = float("inf")
        for nc in self.cloud.nodes:
            count = sum(
                1 for s in registry.sessions.values()
                if s.get("node") == nc.name and s.get("location") == "cloud"
            )
            if count < self.cloud.max_sessions_per_node and count < best_count:
                best = nc
                best_count = count

        return best

    # ── Workspace sync ──────────────────────────────────────────

    def sync_to_cloud(
        self,
        node: CloudNodeConfig,
        workspace_path: Path,
        session_id: str,
        claude_dir: Optional[Path] = None,
    ) -> bool:
        """Sync workspace and Claude state to a cloud node.

        Args:
            node: Target cloud node
            workspace_path: Local workspace path
            session_id: Session identifier (used for remote workspace dir)
            claude_dir: Local Claude config dir (defaults to ~/.claude)

        Returns:
            True if sync succeeded
        """
        host = ssh_host(node)
        if not node.tailscale_hostname and not node.tailscale_ip:
            print("Error: Node has no Tailscale address")
            return False

        remote_workspace = f"/home/ubuntu/workspaces/{session_id}"

        # Create remote directory
        subprocess.run(
            ["ssh", *SSH_OPTS, host, "mkdir", "-p", remote_workspace],
            check=False,
            capture_output=True,
        )

        # Sync workspace (respects .gitignore)
        print(f"Syncing workspace to {host}...")
        result = subprocess.run(
            [
                *RSYNC_SSH, "--delete",
                "--filter=:- .gitignore",
                f"{workspace_path}/",
                f"{host}:{remote_workspace}/",
            ],
            check=False,
        )
        if result.returncode != 0:
            print(f"Warning: Workspace sync returned {result.returncode}")

        # Sync Claude project state
        if claude_dir is None:
            claude_dir = Path.home() / ".claude"

        encoded_path = str(workspace_path).replace("/", "-").replace(".", "-").replace("_", "-")
        local_project_dir = claude_dir / "projects" / encoded_path

        if local_project_dir.exists():
            remote_project_dir = f"/home/ubuntu/.claude/projects/{encoded_path}"
            subprocess.run(
                ["ssh", *SSH_OPTS, host, "mkdir", "-p", remote_project_dir],
                check=False,
                capture_output=True,
            )
            print(f"Syncing project state...")
            subprocess.run(
                [
                    *RSYNC_SSH, "--delete",
                    f"{local_project_dir}/",
                    f"{host}:{remote_project_dir}/",
                ],
                check=False,
            )

        # Sync credentials (one-time, non-destructive)
        self._sync_credentials_to_cloud(node)

        return True

    def _sync_credentials_to_cloud(self, node: CloudNodeConfig) -> None:
        """Sync credentials to cloud node (non-destructive, one-time items)."""
        host = ssh_host(node)

        # Anthropic credentials
        anthropic_dir = self.config.credentials.anthropic
        if anthropic_dir.exists():
            subprocess.run(
                [*RSYNC_SSH, f"{anthropic_dir}/", f"{host}:/home/ubuntu/.anthropic/"],
                check=False,
                capture_output=True,
            )

        # Claude credentials file
        cred_file = self.config.credentials.claude / ".credentials.json"
        if cred_file.exists():
            subprocess.run(
                [*RSYNC_SSH, str(cred_file), f"{host}:/home/ubuntu/.claude/.credentials.json"],
                check=False,
                capture_output=True,
            )

        # Setup token
        setup_token = self.config.credentials.claude / ".setup-token"
        if setup_token.exists():
            subprocess.run(
                [*RSYNC_SSH, str(setup_token), f"{host}:/home/ubuntu/.claude/.setup-token"],
                check=False,
                capture_output=True,
            )

        # Settings
        settings_file = self.config.credentials.claude / "settings.json"
        if settings_file.exists():
            subprocess.run(
                [*RSYNC_SSH, str(settings_file), f"{host}:/home/ubuntu/.claude/settings.json"],
                check=False,
                capture_output=True,
            )

        # Git config
        git_config = self.config.credentials.git
        if git_config.exists():
            subprocess.run(
                [*RSYNC_SSH, str(git_config), f"{host}:/home/ubuntu/.gitconfig"],
                check=False,
                capture_output=True,
            )

        # Claude.json (OAuth account info)
        claude_json = Path.home() / ".claude.json"
        if claude_json.exists():
            subprocess.run(
                [*RSYNC_SSH, str(claude_json), f"{host}:/home/ubuntu/.claude.json"],
                check=False,
                capture_output=True,
            )

    def sync_from_cloud(
        self,
        node: CloudNodeConfig,
        workspace_path: Path,
        session_id: str,
        claude_dir: Optional[Path] = None,
    ) -> bool:
        """Sync workspace and Claude state from a cloud node back to local.

        Args:
            node: Source cloud node
            workspace_path: Local workspace path
            session_id: Session identifier
            claude_dir: Local Claude config dir (defaults to ~/.claude)

        Returns:
            True if sync succeeded
        """
        host = ssh_host(node)
        if not node.tailscale_hostname and not node.tailscale_ip:
            print("Error: Node has no Tailscale address")
            return False

        remote_workspace = f"/home/ubuntu/workspaces/{session_id}"

        # Sync workspace back
        print(f"Syncing workspace from {host}...")
        result = subprocess.run(
            [
                *RSYNC_SSH, "--delete",
                "--filter=:- .gitignore",
                f"{host}:{remote_workspace}/",
                f"{workspace_path}/",
            ],
            check=False,
        )
        if result.returncode != 0:
            print(f"Warning: Workspace sync returned {result.returncode}")

        # Sync Claude project state back
        if claude_dir is None:
            claude_dir = Path.home() / ".claude"

        encoded_path = str(workspace_path).replace("/", "-").replace(".", "-").replace("_", "-")
        remote_project_dir = f"/home/ubuntu/.claude/projects/{encoded_path}"
        local_project_dir = claude_dir / "projects" / encoded_path
        local_project_dir.mkdir(parents=True, exist_ok=True)

        print(f"Syncing project state back...")
        subprocess.run(
            [
                *RSYNC_SSH, "--delete",
                f"{host}:{remote_project_dir}/",
                f"{local_project_dir}/",
            ],
            check=False,
        )

        return True

    # ── 1Password bridge ────────────────────────────────────────

    def start_op_bridge(self, node: CloudNodeConfig) -> Optional[subprocess.Popen]:
        """Start the local 1Password bridge (socat TCP listener).

        Bridges the local op-wrapper Unix socket to a TCP port on the
        Tailscale interface so the cloud VM can reach it.

        Args:
            node: Cloud node that will connect to this bridge

        Returns:
            Popen process if started, None on failure
        """
        local_ts_ip = self._get_local_tailscale_ip()
        socket_path = self.config.credentials.op_socket
        if not socket_path or not socket_path.exists():
            print("Warning: 1Password socket not found, skipping bridge")
            return None

        daemon_sock = socket_path / "daemon.sock"
        if not daemon_sock.exists():
            print("Warning: op-wrapper daemon.sock not found, skipping bridge")
            return None

        # Check if bridge is already running
        pid_file = self._get_bridge_pid_file()
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)  # Check if process exists
                print(f"1Password bridge already running (PID {pid})")
                return None
            except (OSError, ValueError):
                pid_file.unlink(missing_ok=True)

        print(f"Starting 1Password bridge on {local_ts_ip}:7779...")
        proc = subprocess.Popen(
            [
                "socat",
                f"TCP-LISTEN:7779,bind={local_ts_ip},fork,reuseaddr",
                f"UNIX-CONNECT:{daemon_sock}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Save PID
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(proc.pid))

        return proc

    def stop_op_bridge(self, node: Optional[CloudNodeConfig] = None) -> None:
        """Stop the local 1Password bridge.

        Args:
            node: Cloud node (unused, for API consistency)
        """
        pid_file = self._get_bridge_pid_file()
        if not pid_file.exists():
            return

        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"Stopped 1Password bridge (PID {pid})")
        except (OSError, ValueError):
            pass
        finally:
            pid_file.unlink(missing_ok=True)

    def _get_bridge_pid_file(self) -> Path:
        """Get path to the 1Password bridge PID file."""
        xdg_config = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        return Path(xdg_config) / "remote-claude" / "op-bridge.pid"

    # ── Remote image build ──────────────────────────────────────

    def build_on_node(self, node: CloudNodeConfig) -> bool:
        """Rebuild the Docker image on a cloud node.

        Syncs the Dockerfile and scripts, then runs docker build remotely.

        Args:
            node: Target cloud node

        Returns:
            True if build succeeded
        """
        host = ssh_host(node)
        if not node.tailscale_hostname and not node.tailscale_ip:
            print(f"Error: Node '{node.name}' has no Tailscale address")
            return False

        # Sync the docker/ directory and hooks to the node
        repo_dir = Path(__file__).parent.parent
        docker_dir = repo_dir / "docker"
        hooks_dir = repo_dir / "hooks"

        print(f"Syncing build context to {node.name}...")
        for src, dst in [
            (f"{docker_dir}/", f"{host}:/home/ubuntu/remote-claude/docker/"),
            (f"{hooks_dir}/", f"{host}:/home/ubuntu/remote-claude/hooks/"),
        ]:
            result = subprocess.run(
                [*RSYNC_SSH, "--delete", src, dst],
                check=False,
            )
            if result.returncode != 0:
                print(f"Warning: rsync to {dst} returned {result.returncode}")

        print(f"Building Docker image on {node.name}...")
        result = subprocess.run(
            ["ssh", *SSH_OPTS, host, "docker", "build", "-t", "remote-claude:latest",
             "/home/ubuntu/remote-claude/docker/"],
            check=False,
        )
        return result.returncode == 0


class SessionRegistry:
    """Lightweight JSON registry for tracking session locations.

    Tracks whether sessions are local or cloud, and which node they're on.
    """

    _registry_path: Optional[Path] = None

    @classmethod
    def _get_path(cls) -> Path:
        if cls._registry_path:
            return cls._registry_path
        xdg_config = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        return Path(xdg_config) / "remote-claude" / "sessions.json"

    @classmethod
    def load(cls) -> "SessionRegistry":
        """Load the session registry from disk."""
        reg = cls()
        path = cls._get_path()
        if path.exists():
            try:
                data = json.loads(path.read_text())
                reg.sessions = data.get("sessions", {})
            except (json.JSONDecodeError, OSError):
                reg.sessions = {}
        else:
            reg.sessions = {}
        return reg

    def __init__(self):
        self.sessions: dict[str, dict] = {}

    def save(self) -> None:
        """Save the registry to disk."""
        path = self._get_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"sessions": self.sessions}, indent=2))

    def register_session(
        self,
        session_id: str,
        location: str,
        node: Optional[str] = None,
        workspace: Optional[str] = None,
        account: Optional[str] = None,
    ) -> None:
        """Register a new session.

        Args:
            session_id: Session identifier
            location: "local" or "cloud"
            node: Cloud node name (required if location is "cloud")
            workspace: Workspace path
            account: Account profile name
        """
        from datetime import datetime

        self.sessions[session_id] = {
            "location": location,
            "node": node,
            "workspace": workspace,
            "account": account,
            "created": datetime.now().isoformat(),
        }
        self.save()

    def unregister_session(self, session_id: str) -> Optional[dict]:
        """Remove a session from the registry.

        Returns:
            Session data if found, None otherwise
        """
        data = self.sessions.pop(session_id, None)
        if data is not None:
            self.save()
        return data

    def get_session(self, session_id: str) -> Optional[dict]:
        """Get session data by ID."""
        return self.sessions.get(session_id)

    def find_session(self, partial_id: str) -> Optional[tuple[str, dict]]:
        """Find a session by partial ID match.

        Returns:
            Tuple of (full_session_id, session_data) if found, None otherwise
        """
        matches = [
            (sid, data) for sid, data in self.sessions.items()
            if partial_id in sid
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def cloud_sessions(self) -> dict[str, dict]:
        """Get all cloud sessions."""
        return {
            sid: data for sid, data in self.sessions.items()
            if data.get("location") == "cloud"
        }

    def sessions_on_node(self, node_name: str) -> dict[str, dict]:
        """Get all sessions on a specific node."""
        return {
            sid: data for sid, data in self.sessions.items()
            if data.get("node") == node_name
        }
