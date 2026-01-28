# Remote Access Setup

Access your Claude sessions from anywhere using Tailscale VPN and Mosh.

## Architecture

```
┌──────────────────┐         ┌──────────────────┐
│   Your Phone     │         │   Your Laptop    │
│   (Blink Shell)  │         │   (Terminal)     │
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

## Tailscale Setup

Tailscale creates a secure VPN between all your devices without complex configuration.

### Installation

```bash
# Run the setup script
bash setup/tailscale-setup.sh

# After installation, authenticate:
tailscale up

# Verify connection:
tailscale status
```

### Verify

Your machine should appear at https://login.tailscale.com/admin/machines with a hostname like `your-mac.tailnet`.

## SSH Setup

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

### Recommended SSH Config

On your Mac, enable Remote Login in System Preferences > Sharing, then:

```bash
# Disable password auth (key-only)
sudo sed -i '' 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo launchctl stop com.openssh.sshd
sudo launchctl start com.openssh.sshd
```

## Mosh Setup (Optional)

Mosh provides resilient connections that survive network changes, sleep/wake cycles, and IP changes.

```bash
# Build from source
bash setup/mosh-build.sh

# Or with automatic installation:
INSTALL=1 bash setup/mosh-build.sh
```

## Shell Integration

Add to `~/.bashrc` or `~/.zshrc`:

```bash
source ~/GitHub/remote-claude/setup/profile-snippet.sh
```

This provides:
- `rc` - Alias for rc.py
- `rc-attach` - Quick session attachment
- `rc-mosh <host>` - Connect via Mosh and auto-attach
- `rc-ssh <host>` - Connect via SSH and auto-attach

## Connecting Remotely

### From Terminal

```bash
# SSH
ssh user@your-mac.tailnet
rc attach

# Or one command
ssh user@your-mac.tailnet -t "tmux -L remote-claude attach"

# Mosh (more reliable)
mosh user@your-mac.tailnet -- tmux -L remote-claude attach
```

### From Blink Shell (iOS)

1. Add host: Settings > Hosts > Add
   - Hostname: `your-mac.tailnet`
   - User: your username
   - Key: select your SSH key

2. Connect and attach:
   ```bash
   ssh your-mac
   rc attach
   ```

### Auto-Attach on Login

To automatically attach when SSH'ing in, add to your shell profile on the host:

```bash
if [ -n "$SSH_CONNECTION" ] && [ -z "$TMUX" ]; then
    if tmux -L remote-claude list-sessions &>/dev/null; then
        exec tmux -L remote-claude attach
    fi
fi
```

## Mobile Clients

Recommended terminal apps:

- **iOS**: [Blink Shell](https://blink.sh) (best Mosh support), [Termius](https://termius.com)
- **Android**: [Termius](https://termius.com), [JuiceSSH](https://juicessh.com)

Tips:
- Use Mosh for reliable connections over cellular
- Enable Tailscale on your phone
- Save connection profiles in your terminal app
