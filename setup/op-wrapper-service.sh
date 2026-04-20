#!/bin/bash
#
# op-wrapper-service - LaunchAgent manager for the 1Password proxy daemon
#
# Keeps op-wrapper-daemon running persistently so containers can access
# 1Password secrets via the TCP bridge (RC_OP_WRAPPER_HOST/PORT).
#
# Usage:
#   ./op-wrapper-service.sh install   # Install and start
#   ./op-wrapper-service.sh start     # Start service
#   ./op-wrapper-service.sh stop      # Stop service
#   ./op-wrapper-service.sh restart   # Restart service
#   ./op-wrapper-service.sh status    # Check status
#   ./op-wrapper-service.sh uninstall # Remove service
#   ./op-wrapper-service.sh logs      # Follow daemon log

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DAEMON_SCRIPT="$SCRIPT_DIR/op-wrapper-daemon.py"
PLIST_NAME="com.remote-claude.op-wrapper-daemon"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_FILE="$HOME/.op-wrapper/daemon.log"
SOCKET_PATH="$HOME/.op-wrapper/sock/daemon.sock"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

create_plist() {
    mkdir -p "$HOME/Library/LaunchAgents"
    mkdir -p "$HOME/.op-wrapper/sock"

    # Locate python3 — needed to run the daemon
    local python_bin
    if command -v python3 &>/dev/null; then
        python_bin="$(command -v python3)"
    else
        error "python3 not found. Install Python 3 before running this service."
        exit 1
    fi

    # The daemon runs `op read` — make sure op is on the LaunchAgent's PATH
    local op_dir
    if command -v op &>/dev/null; then
        op_dir="$(dirname "$(command -v op)")"
    else
        op_dir="/usr/local/bin"
    fi

    cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${python_bin}</string>
        <string>${DAEMON_SCRIPT}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>

    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${op_dir}:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>

    <key>ThrottleInterval</key>
    <integer>5</integer>
</dict>
</plist>
EOF

    info "Created plist: $PLIST_PATH"
}

do_install() {
    if [ ! -f "$DAEMON_SCRIPT" ]; then
        error "Daemon script not found: $DAEMON_SCRIPT"
        error "Clone or copy the 1password_wrapper utilities first."
        exit 1
    fi

    if [ -f "$PLIST_PATH" ]; then
        warn "Service already installed, reinstalling..."
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
    fi

    create_plist
    launchctl load "$PLIST_PATH"
    sleep 1

    echo ""
    info "op-wrapper-daemon installed and started"
    info "Will auto-start at login and restart on crash"
    echo ""
    do_status
}

do_start() {
    if launchctl list | grep -q "$PLIST_NAME"; then
        warn "Service already running"
        return
    fi

    if [ ! -f "$PLIST_PATH" ]; then
        error "Plist not found. Run: $0 install"
        exit 1
    fi

    launchctl load "$PLIST_PATH"
    info "Service started"
}

do_stop() {
    if ! launchctl list | grep -q "$PLIST_NAME"; then
        warn "Service not running"
        return
    fi

    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    info "Service stopped"
}

do_restart() {
    do_stop 2>/dev/null || true
    sleep 1
    do_start
    info "Service restarted"
}

do_status() {
    echo "Service:  $PLIST_NAME"
    echo "Daemon:   $DAEMON_SCRIPT"
    echo "Log:      $LOG_FILE"
    echo "Socket:   $SOCKET_PATH"
    echo ""

    # LaunchAgent status
    if launchctl list | grep -q "$PLIST_NAME"; then
        local pid
        pid=$(launchctl list | grep "$PLIST_NAME" | awk '{print $1}')
        if [ "$pid" != "-" ] && [ -n "$pid" ]; then
            echo -e "Daemon:   ${GREEN}Running${NC} (PID $pid)"
        else
            echo -e "Daemon:   ${YELLOW}Loaded (no PID — may have exited)${NC}"
        fi
    else
        echo -e "Daemon:   ${RED}Not loaded${NC}"
    fi

    # Socket health
    if [ -S "$SOCKET_PATH" ]; then
        # Send an unknown command — daemon responds instantly with ERROR (no op call needed)
        local response
        response=$(echo "PING" | nc -U -w2 "$SOCKET_PATH" 2>/dev/null || true)
        if [ -n "$response" ]; then
            echo -e "Socket:   ${GREEN}Responding${NC}"
        else
            echo -e "Socket:   ${YELLOW}Exists but not responding${NC}"
        fi
    else
        echo -e "Socket:   ${RED}Not found${NC}"
    fi

    # TCP bridge (port 2626)
    if lsof -iTCP:2626 -sTCP:LISTEN &>/dev/null; then
        echo -e "TCP:      ${GREEN}Bridge listening on :2626${NC}"
    else
        echo -e "TCP:      ${YELLOW}Bridge not running (starts automatically with rc start)${NC}"
    fi

    # Recent log entries
    if [ -f "$LOG_FILE" ]; then
        echo ""
        echo "Recent log:"
        tail -5 "$LOG_FILE" 2>/dev/null | sed 's/^/  /'
    fi
}

do_uninstall() {
    do_stop 2>/dev/null || true

    if [ -f "$PLIST_PATH" ]; then
        rm "$PLIST_PATH"
        info "Removed $PLIST_PATH"
    fi

    info "Service uninstalled"
}

do_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        warn "No log file at $LOG_FILE"
        warn "Service may not have been started yet."
    fi
}

case "${1:-status}" in
    install)   do_install ;;
    start)     do_start ;;
    stop)      do_stop ;;
    restart)   do_restart ;;
    status)    do_status ;;
    uninstall) do_uninstall ;;
    logs)      do_logs ;;
    *)
        echo "Usage: $0 {install|start|stop|restart|status|uninstall|logs}"
        exit 1
        ;;
esac
