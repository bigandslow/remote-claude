# Interactive Notifications Setup Guide

This guide walks you through setting up two-way communication between your phone and Remote Claude sessions using Pushover, Tailscale, and Blink Shell.

## Overview

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
│        │ Link   │         │  │ responder.py│──│ tmux    │  │
│        ▼        │         │  └─────────────┘  │ session │  │
│  ┌───────────┐  │         │         ▲        └─────────┘  │
│  │  Blink    │──┼─────────┼─────────┘                      │
│  │  Shell    │  │Tailscale│  HTTP (signed tokens)          │
│  └───────────┘  │   VPN   │                                │
│                 │         │                                 │
└─────────────────┘         └─────────────────────────────────┘
```

**Flow:**
1. Claude needs input → `notify.py` sends Pushover notification
2. Notification shows clickable [Yes] [Always] [No] links
3. Tap link → HTTP request to `responder.py` via Tailscale
4. Responder sends keystroke to tmux session
5. Or tap notification body → Blink Shell opens, attached to session

---

## Prerequisites

- Mac with Remote Claude installed
- iPhone
- Pushover account (already configured)

---

## Step 1: Install Tailscale on Mac

Tailscale creates a secure, private network between your devices.

### Download and Install

```bash
# Run the setup script
cd ~/GitHub/remote-claude
bash setup/tailscale-setup.sh
```

Or manually:
1. Download from: https://pkgs.tailscale.com/stable/
2. Install the PKG file
3. Open Tailscale from Applications

### Authenticate

```bash
# Start Tailscale and log in
tailscale up

# Verify connection
tailscale status

# Get your Tailscale IP and hostname
tailscale ip -4
tailscale status --json | grep DNSName
```

Note your Mac's Tailscale hostname (e.g., `your-mac.tailnet-xxxx.ts.net`).

---

## Step 2: Install Tailscale on iPhone

1. **Download** Tailscale from the App Store
2. **Open** the app and tap "Get Started"
3. **Sign in** with the same account used on your Mac
4. **Enable** the VPN when prompted

### Verify Connection

In the Tailscale app, you should see your Mac listed as a connected device.

Test connectivity:
- On iPhone, open Safari
- Navigate to `http://<mac-tailscale-ip>:8422/health`
- Should see: `{"status": "ok"}` (after starting responder)

---

## Step 3: Install Blink Shell on iPhone

Blink Shell is a professional terminal app with Mosh support and URL scheme integration.

1. **Download** Blink Shell from the App Store ($20 one-time, or build from source)
2. **Open** Blink and complete initial setup

### Configure SSH Key

Option A: Generate in Blink
```
# In Blink, tap Config → Keys → + → Create New
# Choose Ed25519, save as "default"
```

Option B: Import existing key
```
# In Blink, tap Config → Keys → + → Import
# Paste your private key
```

### Add Your Mac as a Host

1. Tap **Config** → **Hosts** → **+**
2. Configure:
   - **Alias:** `mac` (or any name)
   - **Host:** Your Mac's Tailscale hostname (e.g., `your-mac.tailnet-xxxx.ts.net`)
   - **User:** Your Mac username
   - **Key:** Select your SSH key
   - **Mosh:** Enable (recommended)

3. **Test connection:**
   ```
   # In Blink terminal
   mosh mac
   ```

### Add SSH Key to Mac

Copy your Blink public key to your Mac's authorized_keys:

```bash
# On Mac, add the public key from Blink
echo "YOUR_BLINK_PUBLIC_KEY" >> ~/.ssh/authorized_keys
```

Or use the SSH setup script:
```bash
bash ~/GitHub/remote-claude/setup/ssh-setup.sh
```

---

## Step 4: Configure Remote Claude

Update your config with Tailscale details:

```bash
# Edit config
nano ~/.config/remote-claude/config.yaml
```

Add/update the responder section:

```yaml
responder:
  port: 8422
  blink_user: your_mac_username
  blink_host: your-mac.tailnet-xxxx.ts.net
```

---

## Step 5: Start the Responder Server

The responder receives button taps and sends keystrokes to tmux.

```bash
cd ~/GitHub/remote-claude

# Start in foreground (for testing)
python3 hooks/responder.py

# Or start as daemon (background)
python3 hooks/responder.py --daemon

# Check status
curl http://$(tailscale ip -4):8422/health

# Stop daemon
python3 hooks/responder.py --stop
```

### Auto-start on Login (Optional)

Create a LaunchAgent to start responder automatically:

```bash
mkdir -p ~/Library/LaunchAgents

cat > ~/Library/LaunchAgents/com.remote-claude.responder.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.remote-claude.responder</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/YOUR_USERNAME/GitHub/remote-claude/hooks/responder.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/rc-responder.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/rc-responder.log</string>
</dict>
</plist>
EOF

# Load the agent
launchctl load ~/Library/LaunchAgents/com.remote-claude.responder.plist
```

---

## Step 6: Test the Full Flow

### Test 1: Basic Notification

```bash
python3 hooks/notify.py --test
```
→ Should receive notification on phone

### Test 2: Interactive Notification

```bash
# Start responder if not running
python3 hooks/responder.py &

# Send interactive notification
python3 hooks/notify.py --interactive --test --session rc-test-123
```
→ Should receive notification with clickable Yes/Always/No links

### Test 3: Full Integration

```bash
# 1. Create a test tmux session
tmux -L remote-claude new-session -d -s rc-test-123

# 2. Send interactive notification
python3 hooks/notify.py --interactive \
  --title "Test Permission" \
  --message "Allow test action?" \
  --session rc-test-123

# 3. On phone, tap "Yes" link

# 4. Check tmux received the keystroke
tmux -L remote-claude capture-pane -t rc-test-123 -p
```

### Test 4: Blink Deep Link

Tap the notification body → should open Blink Shell and connect to your Mac.

---

## Troubleshooting

### Tailscale not connecting

```bash
# Check status
tailscale status

# Re-authenticate
tailscale up

# Check firewall
sudo pfctl -sr | grep tailscale
```

### Responder not reachable from phone

1. Verify Tailscale is connected on both devices
2. Check responder is running: `curl http://localhost:8422/health`
3. Check responder is bound to Tailscale IP: `netstat -an | grep 8422`
4. Test from phone browser: `http://<tailscale-ip>:8422/health`

### Blink not opening

1. Verify Blink is installed
2. Test URL scheme manually: Open Safari, enter `blinkshell://`
3. Check host configuration in Blink

### Token validation errors

- Tokens expire after 5 minutes
- Each token can only be used once
- Ensure clocks are synchronized

### SSH/Mosh connection fails

```bash
# Test SSH directly
ssh user@your-mac.tailnet

# Check authorized_keys
cat ~/.ssh/authorized_keys

# Check SSH is enabled
sudo systemsetup -getremotelogin
```

---

## Security Notes

1. **Tailscale-only access:** Responder binds to Tailscale IP, not accessible from public internet
2. **Signed tokens:** Each action URL contains a cryptographically signed, time-limited token
3. **Single-use:** Tokens are invalidated after first use (replay protection)
4. **No passwords:** SSH key authentication only

---

## Quick Reference

| Command | Description |
|---------|-------------|
| `python3 hooks/responder.py` | Start responder (foreground) |
| `python3 hooks/responder.py --daemon` | Start responder (background) |
| `python3 hooks/responder.py --stop` | Stop responder daemon |
| `python3 hooks/notify.py --test` | Send test notification |
| `python3 hooks/notify.py --interactive --session NAME` | Send interactive notification |
| `tailscale status` | Check Tailscale connection |
| `tailscale ip -4` | Get Tailscale IP |
