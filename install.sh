#!/bin/bash
#
# Install Remote Claude
# Creates symlink in /usr/local/bin for system-wide access
#
# Usage:
#   ./install.sh          # Install
#   ./install.sh remove   # Uninstall

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RC_SCRIPT="$SCRIPT_DIR/rc.py"
INSTALL_PATH="/usr/local/bin/rc"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

do_install() {
    # Check if rc.py exists
    if [ ! -f "$RC_SCRIPT" ]; then
        error "rc.py not found at $RC_SCRIPT"
        exit 1
    fi

    # Make sure rc.py is executable
    chmod +x "$RC_SCRIPT"

    # Check if /usr/local/bin exists
    if [ ! -d "/usr/local/bin" ]; then
        info "Creating /usr/local/bin..."
        sudo mkdir -p /usr/local/bin
    fi

    # Check if something already exists at install path
    if [ -e "$INSTALL_PATH" ]; then
        if [ -L "$INSTALL_PATH" ]; then
            current_target=$(readlink "$INSTALL_PATH")
            if [ "$current_target" = "$RC_SCRIPT" ]; then
                info "Already installed at $INSTALL_PATH"
                exit 0
            else
                warn "Existing symlink at $INSTALL_PATH points to $current_target"
                read -p "Replace it? [y/N] " confirm
                if [ "$confirm" != "y" ]; then
                    echo "Cancelled."
                    exit 0
                fi
                sudo rm "$INSTALL_PATH"
            fi
        else
            error "File already exists at $INSTALL_PATH (not a symlink)"
            error "Please remove it manually: sudo rm $INSTALL_PATH"
            exit 1
        fi
    fi

    # Create symlink
    info "Creating symlink: $INSTALL_PATH -> $RC_SCRIPT"
    sudo ln -s "$RC_SCRIPT" "$INSTALL_PATH"

    # Verify installation
    if command -v rc &> /dev/null; then
        info "Installation successful!"
        echo ""
        echo "Usage:"
        echo "  rc start ~/projects/myapp    # Start new session"
        echo "  rc teleport ~/projects/app   # Move existing session"
        echo "  rc list                      # List sessions"
        echo "  rc attach <session>          # Attach to session"
        echo ""
        echo "Run 'rc --help' for more options."
    else
        warn "Symlink created but 'rc' not found in PATH"
        warn "You may need to add /usr/local/bin to your PATH"
    fi
}

do_remove() {
    if [ -L "$INSTALL_PATH" ]; then
        info "Removing symlink: $INSTALL_PATH"
        sudo rm "$INSTALL_PATH"
        info "Uninstalled successfully"
    elif [ -e "$INSTALL_PATH" ]; then
        error "$INSTALL_PATH exists but is not a symlink"
        error "Please remove it manually if desired"
        exit 1
    else
        warn "Nothing to remove - not installed at $INSTALL_PATH"
    fi
}

# Main
case "${1:-install}" in
    install)
        do_install
        ;;
    remove|uninstall)
        do_remove
        ;;
    *)
        echo "Usage: $0 {install|remove}"
        exit 1
        ;;
esac
