"""Configuration management for remote-claude."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class DockerConfig:
    """Docker-related configuration."""

    image: str = "remote-claude:latest"
    build_context: Optional[Path] = None


@dataclass
class NetworkConfig:
    """Network isolation configuration."""

    mode: str = "allowlist"  # "allowlist", "bridge", or "none"
    allowed_domains: list[str] = field(
        default_factory=lambda: [
            "github.com",
            "api.github.com",
            "pypi.org",
            "files.pythonhosted.org",
            "registry.npmjs.org",
            "api.anthropic.com",
        ]
    )


@dataclass
class CredentialsConfig:
    """Credential mount paths."""

    # Personal credentials (fallback)
    anthropic: Path = field(default_factory=lambda: Path.home() / ".anthropic")
    git: Path = field(default_factory=lambda: Path.home() / ".gitconfig")
    ssh: Path = field(default_factory=lambda: Path.home() / ".ssh")
    claude: Path = field(default_factory=lambda: Path.home() / ".claude")

    # Dedicated Claude credentials (optional, used if configured)
    claude_git: Optional[Path] = None      # Git config for bot account
    claude_ssh: Optional[Path] = None      # SSH keys for bot account
    claude_gcp: Optional[Path] = None      # GCP service account key


@dataclass
class NotificationsConfig:
    """Notification settings."""

    webhook_url: Optional[str] = None
    enabled: bool = False


@dataclass
class TmuxConfig:
    """Tmux session configuration."""

    session_prefix: str = "rc"
    socket_name: str = "remote-claude"


@dataclass
class Config:
    """Main configuration container."""

    docker: DockerConfig = field(default_factory=DockerConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    tmux: TmuxConfig = field(default_factory=TmuxConfig)


def get_config_path() -> Path:
    """Get the configuration file path."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    return Path(xdg_config) / "remote-claude" / "config.yaml"


def load_config() -> Config:
    """Load configuration from file, with defaults for missing values."""
    config_path = get_config_path()

    if not config_path.exists():
        return Config()

    with open(config_path) as f:
        data = yaml.safe_load(f) or {}

    config = Config()

    # Docker config
    if "docker" in data:
        docker_data = data["docker"]
        config.docker.image = docker_data.get("image", config.docker.image)
        if "build_context" in docker_data:
            config.docker.build_context = Path(docker_data["build_context"])

    # Network config
    if "network" in data:
        net_data = data["network"]
        config.network.mode = net_data.get("mode", config.network.mode)
        if "allowed_domains" in net_data:
            config.network.allowed_domains = net_data["allowed_domains"]

    # Credentials config
    if "credentials" in data:
        cred_data = data["credentials"]
        if "anthropic" in cred_data:
            config.credentials.anthropic = Path(cred_data["anthropic"]).expanduser()
        if "git" in cred_data:
            config.credentials.git = Path(cred_data["git"]).expanduser()
        if "ssh" in cred_data:
            config.credentials.ssh = Path(cred_data["ssh"]).expanduser()
        if "claude" in cred_data:
            config.credentials.claude = Path(cred_data["claude"]).expanduser()
        # Dedicated Claude credentials (optional)
        if "claude_git" in cred_data:
            config.credentials.claude_git = Path(cred_data["claude_git"]).expanduser()
        if "claude_ssh" in cred_data:
            config.credentials.claude_ssh = Path(cred_data["claude_ssh"]).expanduser()
        if "claude_gcp" in cred_data:
            config.credentials.claude_gcp = Path(cred_data["claude_gcp"]).expanduser()

    # Notifications config
    if "notifications" in data:
        notif_data = data["notifications"]
        config.notifications.webhook_url = notif_data.get("webhook_url")
        config.notifications.enabled = notif_data.get(
            "enabled", config.notifications.webhook_url is not None
        )

    # Tmux config
    if "tmux" in data:
        tmux_data = data["tmux"]
        config.tmux.session_prefix = tmux_data.get(
            "session_prefix", config.tmux.session_prefix
        )
        config.tmux.socket_name = tmux_data.get(
            "socket_name", config.tmux.socket_name
        )

    return config


def save_config(config: Config) -> None:
    """Save configuration to file."""
    config_path = get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "docker": {
            "image": config.docker.image,
        },
        "network": {
            "mode": config.network.mode,
            "allowed_domains": config.network.allowed_domains,
        },
        "credentials": {
            "anthropic": str(config.credentials.anthropic),
            "git": str(config.credentials.git),
            "ssh": str(config.credentials.ssh),
            "claude": str(config.credentials.claude),
            **({"claude_git": str(config.credentials.claude_git)} if config.credentials.claude_git else {}),
            **({"claude_ssh": str(config.credentials.claude_ssh)} if config.credentials.claude_ssh else {}),
            **({"claude_gcp": str(config.credentials.claude_gcp)} if config.credentials.claude_gcp else {}),
        },
        "notifications": {
            "webhook_url": config.notifications.webhook_url,
            "enabled": config.notifications.enabled,
        },
        "tmux": {
            "session_prefix": config.tmux.session_prefix,
            "socket_name": config.tmux.socket_name,
        },
    }

    if config.docker.build_context:
        data["docker"]["build_context"] = str(config.docker.build_context)

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
