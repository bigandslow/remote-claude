# Configuration Reference

Configuration file: `~/.config/remote-claude/config.yaml`

## Full Example

```yaml
docker:
  image: remote-claude:latest

network:
  mode: bridge  # "allowlist", "bridge", or "none"
  allowed_domains:
    - github.com
    - pypi.org
    - registry.npmjs.org
    - api.anthropic.com

credentials:
  anthropic: ~/.anthropic
  git: ~/.gitconfig
  ssh: ~/.ssh
  claude: ~/.claude

tmux:
  session_prefix: rc
  socket_name: remote-claude

notifications:
  enabled: true
  pushover:
    user_key: your-user-key
    api_token: your-app-token
  blink:
    host: your-mac.tailnet
    user: your-username
```

## Sections

### docker

| Key | Default | Description |
|-----|---------|-------------|
| `image` | `remote-claude:latest` | Docker image to use for containers |

### network

| Key | Default | Description |
|-----|---------|-------------|
| `mode` | `bridge` | Network mode: `bridge`, `allowlist`, or `none` |
| `allowed_domains` | (see below) | Domains to allow in `allowlist` mode |

**Network Modes:**
- `bridge` - Normal Docker networking (default)
- `allowlist` - Proxy-based filtering, only allowed domains accessible
- `none` - Complete network isolation

**Default allowed domains** (when `mode: allowlist`):
- github.com, api.github.com
- pypi.org, files.pythonhosted.org
- registry.npmjs.org
- api.anthropic.com

### credentials

Paths to credential files/directories to mount into containers.

| Key | Default | Description |
|-----|---------|-------------|
| `anthropic` | `~/.anthropic` | API key directory |
| `git` | `~/.gitconfig` | Git configuration |
| `ssh` | `~/.ssh` | SSH keys |
| `claude` | `~/.claude` | Claude config (selective mounts, see below) |

## Container Mounts

### Workspace

| Host Path | Container Path | Mode | Purpose |
|-----------|----------------|------|---------|
| `<workspace>` | `/workspace` | read-write | Your project files (non-worktree) |

For **git worktrees**, the workspace and git commondir are mounted at their exact host paths inside the container (e.g. `/Users/you/.cache/workspaces/my-branch` and `/Users/you/GitHub/myrepo/.git`). This avoids any path translation so git works natively. Claude's working directory is set to the host workspace path.

### Claude Config (Selective)

Only specific directories from `~/.claude` are mounted to avoid container changes polluting host config:

| Host Path | Container Path | Mode | Purpose |
|-----------|----------------|------|---------|
| `~/.claude/projects/` | `/home/claude/.claude/projects/` | read-write | Session history (conversations) |
| `~/.claude/.credentials.json` | `/home/claude/.claude/.credentials.json` | read-only | API authentication |
| `~/.claude/.setup-token` | `/home/claude/.claude/.setup-token` | read-only | Long-lived auth token |
| `~/.claude/settings.json` | `/home/claude/.claude/settings.json` | read-only | User preferences |
| `~/.claude/CLAUDE.md` | `/home/claude/.claude/CLAUDE.md` | read-write | User instructions |
| `~/.claude/todos/` | `/home/claude/.claude/todos/` | read-write | Todo items |
| `~/.claude/plans/` | `/home/claude/.claude/plans/` | read-write | Saved plans |
| `~/.claude/plugins/` | `/home/claude/.claude/plugins-host/` | read-only | Installed plugins (copied to `plugins/` at startup with host paths rewritten) |
| `~/.claude.json` | `/home/claude/.claude.json` | read-write | OAuth account info |

### Credentials

| Host Path | Container Path | Mode | Purpose |
|-----------|----------------|------|---------|
| `~/.gitconfig` | `/home/claude/.gitconfig` | read-only | Git configuration |
| `~/.ssh/` | `/home/claude/.ssh/` | read-only | SSH keys |

## Project Configuration

Each workspace requires a `.rc/project.yaml` file before `rc start` will run. Create one with:

```bash
rc init ~/projects/myapp
```

### `.rc/project.yaml`

```yaml
# Commands to run when the container starts
setup_commands:
  - pip install --break-system-packages mypy ruff

# Private repos this project needs access to (org/repo format)
deploy_keys:
  - myorg/myrepo
```

| Key | Type | Description |
|-----|------|-------------|
| `setup_commands` | list | Shell commands run by entrypoint before Claude starts |
| `deploy_keys` | list | Repos (org/repo) to configure git URL rewriting for |
| `features` | dict | Feature flags (project-specific) |

Deploy keys are scoped per-project. Only repos listed in `deploy_keys` get git insteadOf rules configured. This prevents credential leakage across unrelated projects.

### Adding non-GitHub SSH hosts

When deploy keys are active, containers use a managed `~/.ssh/config` that only covers configured GitHub repos. To also allow SSH access to other hosts (Bitbucket, GitLab, self-hosted git, etc.), register a personal key with:

```bash
./setup/credentials-setup.sh --add-ssh-host <hostname> <key-path>

# Example:
./setup/credentials-setup.sh --add-ssh-host bitbucket.org ~/.ssh/id_ed25519_bitbucket
```

This:
1. Copies the key file into `~/.config/remote-claude/credentials/deploy-keys/.ssh/`
2. Writes a `Host` entry to `extra-hosts.conf` in the same directory
3. Regenerates `~/.ssh/config` inside the deploy keys directory, appending the extra host entries

The `extra-hosts.conf` file is not overwritten when deploy key repos are added or removed, so this registration is persistent. Each developer runs this once with their own key — it is a local machine configuration, not a project configuration.

## Session Persistence

Sessions are stored in `~/.claude/projects/<encoded-path>/`. The path is encoded by replacing `/`, `.`, and `_` with `-`.

Example: `/Users/chris/.cache/workspaces/my_project` becomes `-Users-chris--cache-workspaces-my-project`

### Docker ↔ Local Continuity

A symlink `-workspace` is created in the container pointing to the host-path-encoded directory. This allows:

```bash
# Start in Docker
rc start ~/projects/myapp

# Later, continue locally (same session)
cd ~/projects/myapp
claude --continue
```

Both use the same session files in `~/.claude/projects/`.

### tmux

| Key | Default | Description |
|-----|---------|-------------|
| `session_prefix` | `rc` | Prefix for tmux session names |
| `socket_name` | `remote-claude` | tmux socket name |

### notifications

| Key | Description |
|-----|-------------|
| `enabled` | Enable/disable notifications |
| `webhook_url` | Generic webhook URL (ntfy, Slack, Discord) |
| `pushover.user_key` | Pushover user key |
| `pushover.api_token` | Pushover application token |
| `blink.host` | Tailscale hostname for Blink deep links |
| `blink.user` | SSH username for Blink |

### cloud

See [Cloud Setup](cloud-setup.md) for full setup instructions.

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable cloud infrastructure |
| `provider` | `digitalocean` | Cloud provider: `digitalocean` or `hetzner` |
| `api_token_ref` | | 1Password `op://` URI for provider API token |
| `tailscale_oauth_client_id` | | Tailscale OAuth client ID |
| `tailscale_oauth_secret_ref` | | 1Password `op://` URI for Tailscale OAuth secret |
| `default_server_type` | *(provider default)* | Server size (e.g., `s-2vcpu-4gb`, `cpx21`) |
| `default_region` | *(provider default)* | Region shorthand (e.g., `nyc`, `ash`) |
| `placement` | `auto` | Session placement: `auto`, `manual`, or `round-robin` |
| `max_sessions_per_node` | `6` | Max concurrent sessions per VM |

## Environment Variables

These override config file settings:

| Variable | Description |
|----------|-------------|
| `PUSHOVER_USER_KEY` | Pushover user key |
| `PUSHOVER_API_TOKEN` | Pushover API token |
| `RC_WEBHOOK_URL` | Webhook URL for notifications |
