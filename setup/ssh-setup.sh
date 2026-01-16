#!/bin/bash
# SSH Setup and Hardening for Remote Claude
# Configures SSH for secure remote access

set -e

echo "=== SSH Setup for Remote Claude ==="
echo ""

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    SSHD_CONFIG="/etc/ssh/sshd_config"
else
    OS="linux"
    SSHD_CONFIG="/etc/ssh/sshd_config"
fi

echo "Detected OS: $OS"
echo ""

# ============================================================
# Generate SSH key pair if needed
# ============================================================
setup_ssh_keys() {
    echo "=== SSH Key Setup ==="

    SSH_DIR="$HOME/.ssh"
    mkdir -p "$SSH_DIR"
    chmod 700 "$SSH_DIR"

    # Check for existing keys
    if [ -f "$SSH_DIR/id_ed25519" ]; then
        echo "SSH key already exists: $SSH_DIR/id_ed25519"
    else
        echo "Generating new Ed25519 SSH key..."
        ssh-keygen -t ed25519 -f "$SSH_DIR/id_ed25519" -N "" -C "$(whoami)@$(hostname)"
        echo "Key generated: $SSH_DIR/id_ed25519"
    fi

    echo ""
    echo "Your public key (add to remote servers' authorized_keys):"
    echo "---"
    cat "$SSH_DIR/id_ed25519.pub"
    echo "---"
    echo ""
}

# ============================================================
# Enable SSH server (macOS)
# ============================================================
enable_ssh_macos() {
    echo "=== Enabling SSH on macOS ==="
    echo ""
    echo "On macOS, SSH is controlled via System Preferences:"
    echo "  1. Open System Preferences > Sharing"
    echo "  2. Enable 'Remote Login'"
    echo "  3. Choose 'All users' or specific users"
    echo ""

    # Check if Remote Login is enabled
    if systemsetup -getremotelogin 2>/dev/null | grep -q "On"; then
        echo "Remote Login is: ENABLED"
    else
        echo "Remote Login is: DISABLED"
        echo ""
        read -p "Enable Remote Login via command line? (requires sudo) [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            sudo systemsetup -setremotelogin on
            echo "Remote Login enabled"
        fi
    fi
    echo ""
}

# ============================================================
# Enable and configure SSH server (Linux)
# ============================================================
enable_ssh_linux() {
    echo "=== Enabling SSH on Linux ==="

    if ! command -v sshd &> /dev/null; then
        echo "OpenSSH server not installed."
        echo "Install with your package manager:"
        echo "  Debian/Ubuntu: sudo apt install openssh-server"
        echo "  RHEL/Fedora: sudo dnf install openssh-server"
        echo "  Arch: sudo pacman -S openssh"
        return 1
    fi

    if systemctl is-active sshd &>/dev/null || systemctl is-active ssh &>/dev/null; then
        echo "SSH server is: RUNNING"
    else
        echo "SSH server is: NOT RUNNING"
        read -p "Start SSH server? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            sudo systemctl enable ssh sshd 2>/dev/null || true
            sudo systemctl start ssh sshd 2>/dev/null || true
            echo "SSH server started"
        fi
    fi
    echo ""
}

# ============================================================
# Recommended sshd_config settings
# ============================================================
show_hardening_recommendations() {
    echo "=== SSH Hardening Recommendations ==="
    echo ""
    echo "Add these settings to $SSHD_CONFIG for improved security:"
    echo ""
    cat << 'EOF'
# Disable password authentication (use keys only)
PasswordAuthentication no
ChallengeResponseAuthentication no

# Disable root login
PermitRootLogin no

# Use only SSH protocol 2
Protocol 2

# Limit authentication attempts
MaxAuthTries 3

# Disable empty passwords
PermitEmptyPasswords no

# Disable X11 forwarding (unless needed)
X11Forwarding no

# Set idle timeout (optional)
ClientAliveInterval 300
ClientAliveCountMax 2

# Restrict to specific users (optional)
# AllowUsers your_username

# Use strong ciphers
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com
EOF
    echo ""
    echo "After editing, restart SSH:"
    if [ "$OS" = "macos" ]; then
        echo "  sudo launchctl stop com.openssh.sshd"
        echo "  sudo launchctl start com.openssh.sshd"
    else
        echo "  sudo systemctl restart sshd"
    fi
    echo ""
}

# ============================================================
# Setup authorized_keys
# ============================================================
setup_authorized_keys() {
    echo "=== Authorized Keys Setup ==="
    echo ""

    AUTH_KEYS="$HOME/.ssh/authorized_keys"

    if [ -f "$AUTH_KEYS" ]; then
        KEY_COUNT=$(wc -l < "$AUTH_KEYS" | tr -d ' ')
        echo "Found $KEY_COUNT key(s) in $AUTH_KEYS"
    else
        touch "$AUTH_KEYS"
        chmod 600 "$AUTH_KEYS"
        echo "Created empty $AUTH_KEYS"
    fi

    echo ""
    echo "To add a key from another machine:"
    echo "  1. On the remote machine, run: cat ~/.ssh/id_ed25519.pub"
    echo "  2. Copy the output"
    echo "  3. Add it to $AUTH_KEYS on this machine"
    echo ""
    echo "Or use ssh-copy-id from the remote machine:"
    echo "  ssh-copy-id -i ~/.ssh/id_ed25519.pub user@this-machine"
    echo ""
}

# ============================================================
# Test SSH connectivity
# ============================================================
test_ssh() {
    echo "=== SSH Connection Test ==="
    echo ""

    # Get IP addresses
    echo "This machine's addresses:"

    if [ "$OS" = "macos" ]; then
        echo "  Local IP: $(ipconfig getifaddr en0 2>/dev/null || echo 'N/A')"
    else
        echo "  Local IP: $(hostname -I 2>/dev/null | awk '{print $1}' || echo 'N/A')"
    fi

    if command -v tailscale &> /dev/null; then
        TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "N/A")
        echo "  Tailscale IP: $TAILSCALE_IP"
    fi

    echo ""
    echo "Test connection from another machine:"
    echo "  ssh $(whoami)@<ip-address>"
    echo "  mosh $(whoami)@<ip-address>"
    echo ""
}

# ============================================================
# Main
# ============================================================

setup_ssh_keys

if [ "$OS" = "macos" ]; then
    enable_ssh_macos
else
    enable_ssh_linux
fi

setup_authorized_keys
show_hardening_recommendations
test_ssh

echo "=== SSH Setup Complete ==="
