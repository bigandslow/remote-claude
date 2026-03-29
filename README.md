# Remote Claude

Run Claude Code in sandboxed Docker containers with mobile access and push notifications.

## Quick Start

**Prerequisites:** Python 3.10+, Docker, tmux, PyYAML (`pip install pyyaml`)

```bash
# Clone and build
git clone https://github.com/bigandslow/remote-claude.git
cd remote-claude
./rc.py build

# One-time setup (configures auth, skips onboarding prompts)
./rc.py setup

# Start a session
./rc.py start ~/projects/myapp
```

Add to your shell profile:

```bash
alias rc="$HOME/GitHub/remote-claude/rc.py"
```

## Commands

All session commands support interactive selection when called without arguments:

```bash
rc start <workspace> [--name NAME]  # Start new session
rc ls                               # List sessions
rc a [session]                      # Attach to session
rc kill [session]                   # Kill session
rc restart [session]                # Restart Claude (picks up new MCP configs)
rc shell [session]                  # Open bash in container
rc send <session> "<text>"          # Send command to session (non-interactive)
rc logs [session] [-f]              # View container logs
```

Partial name matching works: `rc a myapp` matches `myapp-abc123`.

## Sending Commands Non-Interactively

`rc send` sends keystrokes to a running session without attaching:

```bash
# Fire-and-forget
rc send myapp "echo hello"

# Wait for output pattern (returns 0 on match, 1 on timeout)
rc send myapp "run-tests" --wait-for "PASS" --timeout 60
```

Works with both local and cloud sessions. Useful for scripted orchestration and batch workflows.

## Setup Command

`rc setup` creates a pre-configured Docker image with your credentials baked in:

- Copies auth tokens so Claude works immediately
- Skips onboarding prompts (theme selection, login, etc.)
- Only needs to run once (or after `rc build --refresh`)

### Headless Setup

`rc setup --headless` runs setup non-interactively. It auto-navigates all prompts via tmux and only requires you to complete OAuth login:

```bash
rc setup --headless
# → Opens browser for OAuth
# → Prints: rc send rc-setup "<paste-code-here>"

# In another terminal (or via an agent), send the auth code:
rc send rc-setup "YOUR_AUTH_CODE_HERE"
# → Setup completes automatically and commits the image
```

This is useful when running setup from a script or agent that can't provide interactive terminal input.

## Multi-Account Support

Manage multiple API accounts for rate limit rotation:

```bash
rc account add work        # Add account interactively
rc account list            # Show all accounts
rc switch <session> work   # Switch session to different account
```

Sessions automatically rotate to the next account when rate limited.

## Deploy Keys (Private Repos)

For private repository access in containers:

```bash
# Generate a deploy key for a repo
ssh-keygen -t ed25519 -f ~/.ssh/deploy-myrepo -N ""

# Add to GitHub: Settings → Deploy keys → Add deploy key

# Register with remote-claude
rc account deploy-key add myorg/myrepo ~/.ssh/deploy-myrepo
```

## Mobile Access

Access sessions from iPhone using [Blink Shell](https://blink.sh) + [Tailscale](https://tailscale.com):

1. Install Tailscale on Mac and iPhone
2. In Blink, add your Mac using its Tailscale hostname
3. Connect: `ssh your-mac.tailnet` then `rc a`

For cellular reliability, use Mosh:
```bash
mosh your-mac.tailnet -- tmux -L remote-claude attach
```

## Push Notifications

Get notified when Claude needs input. Requires [Pushover](https://pushover.net).

Configure `~/.config/remote-claude/config.yaml`:

```yaml
notifications:
  enabled: true
  pushover:
    user_key: your-user-key
    api_token: your-app-token
  blink:
    host: your-mac.tailnet
    user: your-username
```

Start services:
```bash
python3 hooks/responder.py --daemon
python3 hooks/watch.py --daemon
```

Tap Yes/No/Always buttons directly from notifications, or tap to open Blink attached to the session.

## Updating

```bash
rc build              # Rebuild base image
rc build --refresh    # Also update configured image (preserves auth)
```

## Integration with cproj

[cproj](https://github.com/bigandslow/cproj) auto-starts sessions for new worktrees. Add to `.cproj/project.yaml`:

```yaml
custom_actions:
  - type: run_command
    command: bash -c 'rc start {worktree_path} --name "$(echo {branch} | sed "s|^[^/]*/||")" --no-attach'
```

## Cloud Sessions

Run sessions on DigitalOcean or Hetzner VMs via Tailscale:

```bash
rc cloud node add --name rc-1     # Provision a VM
rc start ~/project -C              # Start session on cloud
rc teleport myapp --to cloud       # Move existing session to cloud
rc cloud status                    # Show nodes and cost estimate
```

See [Cloud Setup](docs/cloud-setup.md) for configuration.

## Documentation

- [Configuration Reference](docs/configuration.md)
- [Cloud Setup](docs/cloud-setup.md)
- [Remote Access Setup](docs/remote-access.md)
- [Notifications](docs/notifications.md)
- [Security](docs/security.md)
- [Troubleshooting](docs/troubleshooting.md)

## License

MIT
