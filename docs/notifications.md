# Notifications

Get notified when Claude needs your input, even when you're away from your computer.

## Pushover (Recommended)

Pushover provides interactive notifications with tap-to-respond buttons.

### Setup

1. Create account at [pushover.net](https://pushover.net)
2. Install the Pushover app on your phone
3. Create an application at https://pushover.net/apps/build
4. Configure in `~/.config/remote-claude/config.yaml`:

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

### Start Services

```bash
# Responder handles button taps
python3 hooks/responder.py --daemon

# Watcher monitors sessions for prompts
python3 hooks/watch.py --daemon
```

### Test

```bash
python3 hooks/notify.py --test
python3 hooks/notify.py --interactive -s your-session-name
```

### How It Works

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
│  │  Shell    │  │Tailscale│  HTTP callback                 │
│  └───────────┘  │   VPN   │                                │
└─────────────────┘         └─────────────────────────────────┘
```

- **Tap notification body** → Opens Blink Shell attached to the session
- **Tap Yes/Always/No** → Sends keystroke to tmux session

## ntfy.sh (Free, Simple)

[ntfy.sh](https://ntfy.sh) is a free notification service.

### Setup

1. Install the ntfy app on your phone
2. Subscribe to a topic (e.g., `my-claude-alerts`)
3. Configure:

```yaml
notifications:
  enabled: true
  webhook_url: https://ntfy.sh/my-claude-alerts
```

### Test

```bash
python3 hooks/notify.py --test
```

## Other Services

| Service | URL Format |
|---------|------------|
| ntfy.sh | `https://ntfy.sh/your-topic` |
| Slack | `https://hooks.slack.com/services/xxx/yyy/zzz` |
| Discord | `https://discord.com/api/webhooks/xxx/yyy` |
| Generic | Any URL accepting JSON POST |

## Session Watcher

The watcher monitors your Claude sessions and sends notifications when Claude is waiting for input.

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

### Cron Setup

```bash
# Check every 5 minutes
*/5 * * * * cd ~/GitHub/remote-claude && python3 hooks/watch.py --once
```

## Manual Notifications

```bash
# Basic notification
python3 hooks/notify.py --title "Task Complete" --message "Review ready"

# High priority
python3 hooks/notify.py -t "Urgent" -m "Build failed" --priority high

# With specific webhook
python3 hooks/notify.py --webhook-url "https://ntfy.sh/mytopic" -t "Alert"
```

## Commands Reference

| Script | Command | Description |
|--------|---------|-------------|
| `responder.py` | `--daemon` | Start responder in background |
| | `--stop` | Stop responder daemon |
| `watch.py` | `--daemon` | Watch sessions in background |
| | `--stop` | Stop watcher daemon |
| | `--once` | One-shot check |
| `notify.py` | `--test` | Send test notification |
| | `--interactive -s SESSION` | Send interactive test |
