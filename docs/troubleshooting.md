# Troubleshooting

## Docker Issues

### Commands hang

Some Docker CLI commands may hang on older Docker Desktop versions. Update Docker Desktop or try:

```bash
# Force kill stuck container
docker kill <container-id>

# Restart Docker
osascript -e 'quit app "Docker"' && open -a Docker
```

### Image build fails

```bash
# Clean build (no cache)
docker build --no-cache -t remote-claude:latest docker/

# Check Docker disk space
docker system df
docker system prune
```

## tmux Issues

### Socket not found

```bash
# The socket is created when the first session starts
tmux -L remote-claude list-sessions

# If no sessions, start one:
rc start ~/some/workspace
```

### Session exists but can't attach

```bash
# List all tmux sockets
ls -la /tmp/tmux-*/

# Check if process is running
pgrep -fl tmux
```

## Connection Issues

### Mosh not connecting

```bash
# Ensure mosh-server is in PATH on the host
which mosh-server

# Check firewall allows UDP 60000-61000
# On macOS, allow in System Preferences > Security > Firewall > Options
```

### Tailscale not connecting

```bash
# Check status
tailscale status

# Re-authenticate
tailscale up

# Check admin console
# https://login.tailscale.com/admin/machines
```

### SSH connection refused

```bash
# Check if SSH is enabled (macOS)
sudo systemsetup -getremotelogin

# Enable if needed
sudo systemsetup -setremotelogin on
```

## Notification Issues

### No notifications received

```bash
# Test notification
python3 hooks/notify.py --test

# Check config
cat ~/.config/remote-claude/config.yaml

# Check watcher is running
pgrep -fl watch.py
```

### Button taps not working

```bash
# Check responder is running
pgrep -fl responder.py

# Test responder endpoint (from Tailscale network)
curl http://your-mac.tailnet:8765/health

# Check responder logs
tail -f /tmp/rc-responder.log
```

## Session Issues

### rc list shows empty

```bash
# Check Docker containers directly
docker ps --filter "name=rc-"

# Check tmux sessions
tmux -L remote-claude list-sessions
```

### Can't attach to session

```bash
# Check session exists
rc list

# Try attaching directly via tmux
tmux -L remote-claude attach -t rc-<session-id>

# Check if another client is attached
tmux -L remote-claude list-clients
```

## Logs

### View container logs

```bash
rc logs <session-id>
rc logs <session-id> -f  # Follow mode
```

### View audit logs

```bash
ls ~/.config/remote-claude/audit/
cat ~/.config/remote-claude/audit/safety-*.log
```
