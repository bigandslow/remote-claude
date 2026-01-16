# Remote Claude

Run Claude Code in sandboxed Docker containers with tmux session persistence, enabling secure remote access from anywhere and two-way push notifications that let you approve permission requests directly from your phone.

## Features

- **Sandboxed Execution**: Each Claude session runs in an isolated Docker container with `--dangerously-skip-permissions` enabled safely
- **Session Persistence**: tmux sessions survive network disconnections and allow remote access
- **Two-Way Notifications**: Push notifications with Yes/Always/No buttons to approve Claude actions from your phone
- **Mobile Terminal Access**: Tap notifications to open Blink Shell directly attached to your session
- **Workspace Integration**: Works with git worktrees
- **Credential Isolation**: API keys and SSH keys mounted read-only
- **Network Allowlist**: Optional domain-based network isolation
- **Remote Access**: Full support for SSH, Mosh, and Tailscale

## Quick Start

```bash
# Build the Docker image
./rc.py build

# Start a new Claude session in a workspace
./rc.py start ~/projects/myapp

# List active sessions
./rc.py list

# Attach to an existing session
./rc.py attach myapp

# Kill a session
./rc.py kill myapp
```

## Installation

### Prerequisites

- Python 3.10+
- Docker Desktop (Mac) or Docker Engine (Linux)
- tmux
- PyYAML (`pip install pyyaml`)

### Setup

1. Clone this repository:
   ```bash
   git clone https://github.com/bigandslow/remote-claude.git
   cd remote-claude
   ```

2. Build the Docker image:
   ```bash
   ./rc.py build
   ```

3. (Optional) Copy the example config:
   ```bash
   mkdir -p ~/.config/remote-claude
   cp config/config.yaml.example ~/.config/remote-claude/config.yaml
   ```

4. (Optional) Add shell integration:
   ```bash
   # Add to ~/.bashrc or ~/.zshrc
   source ~/GitHub/remote-claude/setup/profile-snippet.sh
   ```

## Usage

### Starting a Session

```bash
# Basic usage - starts Claude in the specified workspace
rc start ~/projects/myapp

# With an initial prompt
rc start ~/projects/myapp -p "Fix the failing tests"

# Continue a previous conversation
rc start ~/projects/myapp --continue

# Start without attaching (background)
rc start ~/projects/myapp --no-attach
```

### Managing Sessions

```bash
# List active sessions
rc list
rc ls

# Include stopped sessions
rc list -a

# Attach to a session (partial ID match works)
rc attach myapp
rc a myapp

# Show detailed status
rc status

# View session logs
rc logs myapp
rc logs myapp -f  # Follow mode

# Kill a session
rc kill myapp
rc rm myapp -f    # Force, no confirmation
```

## Configuration

Configuration file location: `~/.config/remote-claude/config.yaml`

```yaml
docker:
  image: remote-claude:latest

network:
  mode: allowlist  # "allowlist", "bridge", or "none"
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
```

## Remote Access

Access your Claude sessions from anywhere using Tailscale VPN and Mosh for resilient connections.

### Overview

```
┌──────────────────┐         ┌──────────────────┐
│   Your Phone     │         │   Your Laptop    │
│   (Termius/      │         │   (Terminal)     │
│    Blink)        │         │                  │
└────────┬─────────┘         └────────┬─────────┘
         │                            │
         │    Tailscale VPN           │
         │    (encrypted mesh)        │
         ▼                            ▼
┌─────────────────────────────────────────────────┐
│              Home Mac / Cloud VM                 │
│                                                  │
│  ┌─────────────────────────────────────────┐    │
│  │          tmux (remote-claude)           │    │
│  │  ┌──────────┐ ┌──────────┐ ┌─────────┐  │    │
│  │  │ Session 1│ │ Session 2│ │   ...   │  │    │
│  │  └────┬─────┘ └────┬─────┘ └────┬────┘  │    │
│  └───────┼────────────┼────────────┼───────┘    │
│          ▼            ▼            ▼            │
│  ┌──────────────────────────────────────────┐   │
│  │           Docker Containers              │   │
│  │  (Claude Code in sandbox mode)           │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

### Step 1: Install Tailscale

Tailscale creates a secure VPN between all your devices without complex configuration.

```bash
# Run the setup script
bash setup/tailscale-setup.sh

# After installation, authenticate:
tailscale up

# Verify connection:
tailscale status
```

See [setup/tailscale-setup.sh](setup/tailscale-setup.sh) for detailed instructions.

### Step 2: Configure SSH

Enable and harden SSH on your host machine:

```bash
# Run the SSH setup script
bash setup/ssh-setup.sh
```

This will:
- Generate SSH keys if needed
- Enable SSH server
- Show hardening recommendations
- Configure authorized_keys

### Step 3: Install Mosh (Optional but Recommended)

Mosh provides resilient connections that survive network changes, sleep/wake cycles, and IP changes.

```bash
# Build from source (no package manager required)
bash setup/mosh-build.sh

# Or with automatic installation:
INSTALL=1 bash setup/mosh-build.sh
```

### Step 4: Add Shell Integration

Add remote-claude helpers to your shell:

```bash
# Add to ~/.bashrc or ~/.zshrc:
echo 'source ~/GitHub/remote-claude/setup/profile-snippet.sh' >> ~/.bashrc

# Reload:
source ~/.bashrc
```

This provides:
- `rc` - Alias for rc.py
- `rc-attach` - Quick session attachment
- `rc-mosh <host>` - Connect via Mosh and auto-attach
- `rc-ssh <host>` - Connect via SSH and auto-attach

### Connecting Remotely

From your phone or another computer:

```bash
# Simple SSH connection
ssh user@your-machine.tailnet

# Then attach to sessions
rc-attach

# Or connect and auto-attach in one command
rc-mosh your-machine.tailnet
rc-ssh your-machine.tailnet

# Direct tmux attach via Mosh
mosh user@your-machine.tailnet -- tmux -L remote-claude attach
```

### Mobile Clients

Recommended terminal apps:

- **iOS**: [Termius](https://termius.com), [Blink Shell](https://blink.sh)
- **Android**: [Termius](https://termius.com), [JuiceSSH](https://juicessh.com)

Tips for mobile:
- Use Mosh for reliable connections over cellular
- Enable Tailscale on your phone
- Save connection profiles in your terminal app

### Auto-Attach on Login

To automatically attach to Claude sessions when SSH'ing in, uncomment the auto-attach section in `setup/profile-snippet.sh`:

```bash
# In profile-snippet.sh, uncomment:
auto_attach_on_login() {
    if [ -n "$SSH_CONNECTION" ] && [ -z "$TMUX" ]; then
        if tmux -L remote-claude list-sessions &>/dev/null; then
            rc-attach --auto
        fi
    fi
}
auto_attach_on_login
```

## Notifications

Get notified when Claude needs your input, even when you're away from your computer.

### Quick Setup with ntfy.sh

[ntfy.sh](https://ntfy.sh) is a free, simple notification service that works with iOS, Android, and web browsers.

1. Install the ntfy app on your phone
2. Subscribe to a topic (e.g., `my-claude-alerts`)
3. Configure Remote Claude:

```yaml
# In ~/.config/remote-claude/config.yaml
notifications:
  enabled: true
  webhook_url: https://ntfy.sh/my-claude-alerts
```

4. Test it:

```bash
python3 hooks/notify.py --test
```

### Session Watcher

The session watcher monitors your Claude sessions and sends notifications when Claude is waiting for input:

```bash
# Watch all sessions (foreground)
python3 hooks/watch.py

# Run as background daemon
python3 hooks/watch.py --daemon

# Stop daemon
python3 hooks/watch.py --stop

# One-shot check (useful for cron)
python3 hooks/watch.py --once
```

Add to crontab for periodic checks:

```bash
# Check every 5 minutes
*/5 * * * * cd ~/GitHub/remote-claude && python3 hooks/watch.py --once
```

### Manual Notifications

Send notifications directly:

```bash
# Basic notification
python3 hooks/notify.py --title "Task Complete" --message "Review ready"

# High priority
python3 hooks/notify.py -t "Urgent" -m "Build failed" --priority high

# With specific webhook
python3 hooks/notify.py --webhook-url "https://ntfy.sh/mytopic" -t "Alert"
```

### Supported Services

| Service | URL Format |
|---------|------------|
| ntfy.sh | `https://ntfy.sh/your-topic` |
| Slack | `https://hooks.slack.com/services/xxx/yyy/zzz` |
| Discord | `https://discord.com/api/webhooks/xxx/yyy` |
| Pushover | `https://api.pushover.net/1/messages.json` |
| Generic | Any URL accepting JSON POST |

### Interactive Notifications (Two-Way Communication)

For full two-way communication with Claude sessions from your phone, use the interactive notification system with Pushover:

```
┌─────────────────┐         ┌─────────────────────────────────┐
│   iPhone        │         │         Mac                     │
│                 │         │                                 │
│  ┌───────────┐  │         │  ┌─────────────┐               │
│  │ Pushover  │◄─┼─────────┼──│ notify.py   │               │
│  │   App     │  │   Push  │  └─────────────┘               │
│  └─────┬─────┘  │         │         │                      │
│        │        │         │         ▼                      │
│        │ Tap    │         │  ┌─────────────┐  ┌─────────┐  │
│        │ Button │         │  │ responder.py│──│ tmux    │  │
│        ▼        │         │  └─────────────┘  │ session │  │
│  ┌───────────┐  │         │         ▲        └─────────┘  │
│  │  Blink    │──┼─────────┼─────────┘                      │
│  │  Shell    │  │Tailscale│  HTTP (signed tokens)          │
│  └───────────┘  │   VPN   │                                │
└─────────────────┘         └─────────────────────────────────┘
```

**Features:**
- Tap notification body → Opens Blink Shell attached to the session
- Tap [Yes] [Always] [No] buttons → Sends keystroke to tmux session
- Auto-detects permission prompts and sends interactive notifications
- Secure: Tailscale-only access + signed, time-limited, single-use tokens

**Quick Setup:**

1. Install Tailscale on Mac and iPhone (same account)
2. Install Blink Shell on iPhone
3. Configure Pushover credentials in `~/.config/remote-claude/config.yaml`
4. Start the responder server:
   ```bash
   python3 hooks/responder.py --daemon
   ```
5. Start the session watcher:
   ```bash
   python3 hooks/watch.py --daemon
   ```

See [docs/interactive-notifications-setup.md](docs/interactive-notifications-setup.md) for detailed setup instructions.

### iOS Shortcuts Integration

You can trigger notifications from iOS Shortcuts:

1. Create a shortcut with "Get Contents of URL" action
2. URL: Your ntfy.sh topic URL
3. Method: POST
4. Request Body: JSON with `title` and `message`

This allows you to send commands to your Claude session from your phone (combined with the rc-attach workflow).

### Hooks Quick Reference

| Script | Command | Description |
|--------|---------|-------------|
| `responder.py` | `python3 hooks/responder.py` | Start responder (foreground) |
| | `python3 hooks/responder.py --daemon` | Start responder (background) |
| | `python3 hooks/responder.py --stop` | Stop responder daemon |
| `watch.py` | `python3 hooks/watch.py` | Watch sessions (foreground) |
| | `python3 hooks/watch.py --daemon` | Watch sessions (background) |
| | `python3 hooks/watch.py --stop` | Stop watcher daemon |
| | `python3 hooks/watch.py --once` | One-shot check (for cron) |
| `notify.py` | `python3 hooks/notify.py --test` | Send test notification |
| | `python3 hooks/notify.py --interactive -s SESSION` | Send interactive test |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Host Machine                                 │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    tmux server                            │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐                     │   │
│  │  │session-1│ │session-2│ │session-3│                     │   │
│  │  └────┬────┘ └────┬────┘ └────┬────┘                     │   │
│  └───────┼──────────┼──────────┼────────────────────────────┘   │
│          ▼          ▼          ▼                                │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐                     │
│  │  Docker   │ │  Docker   │ │  Docker   │                     │
│  │ Container │ │ Container │ │ Container │                     │
│  │           │ │           │ │           │                     │
│  │ Workspace │ │ Workspace │ │ Workspace │                     │
│  │ (mounted) │ │ (mounted) │ │ (mounted) │                     │
│  │           │ │           │ │           │                     │
│  │ claude    │ │ claude    │ │ claude    │                     │
│  │ --yolo    │ │ --yolo    │ │ --yolo    │                     │
│  └───────────┘ └───────────┘ └───────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

## Integration with cproj

If you use cproj for worktree management:

```bash
# Create a worktree with cproj
cproj open myproject feature-x

# Start a Claude session in the worktree
rc start ~/cproj-workspaces/myproject_feature/feature-x_*

# Work on multiple features in parallel
cproj open myproject feature-y
rc start ~/cproj-workspaces/myproject_feature/feature-y_*
```

## Security Considerations

- Containers run as non-root user `claude`
- Credentials are mounted read-only
- Network can be restricted to allowlisted domains
- Each session is isolated in its own container
- Workspace changes stay in the worktree for PR review
- SSH key-based authentication recommended (disable password auth)
- Tailscale provides encrypted connections without port forwarding

## Troubleshooting

### Docker commands hang

Some Docker CLI commands may hang on older Docker Desktop versions. The rc.py script works around this by using compatible command formats.

### tmux socket not found

```bash
# The socket is created when the first session starts
# Check if any sessions exist:
tmux -L remote-claude list-sessions

# If no sessions, start one first:
rc start ~/some/workspace
```

### Mosh connection issues

```bash
# Ensure mosh-server is in PATH on the host
which mosh-server

# Check firewall allows UDP 60000-61000
# On macOS, Mosh should work through the firewall automatically
```

### Tailscale not connecting

```bash
# Check status
tailscale status

# Re-authenticate if needed
tailscale up

# Check if machine is online in admin console
# https://login.tailscale.com/admin/machines
```

## License

MIT
