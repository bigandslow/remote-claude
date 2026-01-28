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
| `<workspace>` | `/workspace` | read-write | Your project files |
| `<parent-repo>/.git` | same path | read-write | Git worktree support (if applicable) |

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
| `~/.claude/plugins/` | `/home/claude/.claude/plugins/` | read-write | Installed plugins |
| `~/.claude.json` | `/home/claude/.claude.json` | read-write | OAuth account info |

### Credentials

| Host Path | Container Path | Mode | Purpose |
|-----------|----------------|------|---------|
| `~/.gitconfig` | `/home/claude/.gitconfig` | read-only | Git configuration |
| `~/.ssh/` | `/home/claude/.ssh/` | read-only | SSH keys |

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

## Environment Variables

These override config file settings:

| Variable | Description |
|----------|-------------|
| `PUSHOVER_USER_KEY` | Pushover user key |
| `PUSHOVER_API_TOKEN` | Pushover API token |
| `RC_WEBHOOK_URL` | Webhook URL for notifications |
