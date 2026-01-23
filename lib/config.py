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

    # Deploy key credentials (takes precedence over bot account if configured)
    deploy_keys_git: Optional[Path] = None       # Git config for deploy keys
    deploy_keys_ssh: Optional[Path] = None       # SSH directory with deploy keys
    deploy_keys_registry: Optional[Path] = None  # JSON registry of repo -> alias mappings


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
class AccountProfile:
    """Credential overrides for a specific account.

    All fields are optional - unset fields fall back to global credentials.
    """

    anthropic: Optional[Path] = None
    claude: Optional[Path] = None
    git: Optional[Path] = None
    ssh: Optional[Path] = None
    claude_gcp: Optional[Path] = None


@dataclass
class AccountsConfig:
    """Multi-account configuration."""

    default: str = "default"  # Name of default account profile
    on_rate_limit: str = "manual"  # "manual", "notify", or "auto"
    profiles: dict[str, AccountProfile] = field(default_factory=dict)


@dataclass
class Config:
    """Main configuration container."""

    docker: DockerConfig = field(default_factory=DockerConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    credentials: CredentialsConfig = field(default_factory=CredentialsConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    tmux: TmuxConfig = field(default_factory=TmuxConfig)
    accounts: AccountsConfig = field(default_factory=AccountsConfig)

    def get_credentials_for_account(self, account_name: Optional[str] = None) -> CredentialsConfig:
        """Get effective credentials for an account.

        Args:
            account_name: Account name, or None to use default account.

        Returns:
            CredentialsConfig with account overrides applied.
        """
        if account_name is None:
            account_name = self.accounts.default

        # Start with global credentials
        creds = CredentialsConfig(
            anthropic=self.credentials.anthropic,
            git=self.credentials.git,
            ssh=self.credentials.ssh,
            claude=self.credentials.claude,
            claude_git=self.credentials.claude_git,
            claude_ssh=self.credentials.claude_ssh,
            claude_gcp=self.credentials.claude_gcp,
            deploy_keys_git=self.credentials.deploy_keys_git,
            deploy_keys_ssh=self.credentials.deploy_keys_ssh,
            deploy_keys_registry=self.credentials.deploy_keys_registry,
        )

        # Apply account-specific overrides if profile exists
        if account_name in self.accounts.profiles:
            profile = self.accounts.profiles[account_name]
            if profile.anthropic:
                creds.anthropic = profile.anthropic
            if profile.claude:
                creds.claude = profile.claude
            if profile.git:
                creds.git = profile.git
            if profile.ssh:
                creds.ssh = profile.ssh
            if profile.claude_gcp:
                creds.claude_gcp = profile.claude_gcp

        return creds


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
        # Deploy key credentials (optional, takes precedence)
        if "deploy_keys_git" in cred_data:
            config.credentials.deploy_keys_git = Path(cred_data["deploy_keys_git"]).expanduser()
        if "deploy_keys_ssh" in cred_data:
            config.credentials.deploy_keys_ssh = Path(cred_data["deploy_keys_ssh"]).expanduser()
        if "deploy_keys_registry" in cred_data:
            config.credentials.deploy_keys_registry = Path(cred_data["deploy_keys_registry"]).expanduser()

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

    # Accounts config
    if "accounts" in data:
        acct_data = data["accounts"]
        config.accounts.default = acct_data.get("default", config.accounts.default)
        config.accounts.on_rate_limit = acct_data.get(
            "on_rate_limit", config.accounts.on_rate_limit
        )
        if "profiles" in acct_data:
            for name, profile_data in acct_data["profiles"].items():
                profile = AccountProfile()
                if profile_data:  # profile_data can be None for empty profiles
                    if "anthropic" in profile_data:
                        profile.anthropic = Path(profile_data["anthropic"]).expanduser()
                    if "claude" in profile_data:
                        profile.claude = Path(profile_data["claude"]).expanduser()
                    if "git" in profile_data:
                        profile.git = Path(profile_data["git"]).expanduser()
                    if "ssh" in profile_data:
                        profile.ssh = Path(profile_data["ssh"]).expanduser()
                    if "claude_gcp" in profile_data:
                        profile.claude_gcp = Path(profile_data["claude_gcp"]).expanduser()
                config.accounts.profiles[name] = profile

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
            **({"deploy_keys_git": str(config.credentials.deploy_keys_git)} if config.credentials.deploy_keys_git else {}),
            **({"deploy_keys_ssh": str(config.credentials.deploy_keys_ssh)} if config.credentials.deploy_keys_ssh else {}),
            **({"deploy_keys_registry": str(config.credentials.deploy_keys_registry)} if config.credentials.deploy_keys_registry else {}),
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

    # Accounts config (only if profiles exist)
    if config.accounts.profiles:
        profiles_data = {}
        for name, profile in config.accounts.profiles.items():
            profile_dict = {}
            if profile.anthropic:
                profile_dict["anthropic"] = str(profile.anthropic)
            if profile.claude:
                profile_dict["claude"] = str(profile.claude)
            if profile.git:
                profile_dict["git"] = str(profile.git)
            if profile.ssh:
                profile_dict["ssh"] = str(profile.ssh)
            if profile.claude_gcp:
                profile_dict["claude_gcp"] = str(profile.claude_gcp)
            profiles_data[name] = profile_dict if profile_dict else None

        data["accounts"] = {
            "default": config.accounts.default,
            "on_rate_limit": config.accounts.on_rate_limit,
            "profiles": profiles_data,
        }

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
