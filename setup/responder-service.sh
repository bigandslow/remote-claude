#!/bin/bash
#
# Responder Service Manager
# Manages the responder as a LaunchAgent with auto-restart
#
# Usage:
#   ./responder-service.sh install   # Install and start
#   ./responder-service.sh start     # Start service
#   ./responder-service.sh stop      # Stop service
#   ./responder-service.sh restart   # Restart service
#   ./responder-service.sh status    # Check status
#   ./responder-service.sh uninstall # Remove service
#   ./responder-service.sh logs      # View logs

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="com.remote-claude.responder"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_FILE="/tmp/rc-responder.log"
RESPONDER_SCRIPT="$PROJECT_DIR/hooks/responder.py"

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

    cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>${RESPONDER_SCRIPT}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>${LOG_FILE}</string>

    <key>StandardErrorPath</key>
    <string>${LOG_FILE}</string>

    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF

    info "Created plist at $PLIST_PATH"
}

do_install() {
    if [ -f "$PLIST_PATH" ]; then
        warn "Service already installed, reinstalling..."
        do_stop 2>/dev/null || true
    fi

    if [ ! -f "$RESPONDER_SCRIPT" ]; then
        error "Responder script not found: $RESPONDER_SCRIPT"
        exit 1
    fi

    create_plist
    do_start

    echo ""
    info "Responder service installed and started"
    info "It will auto-start on login and restart if it crashes"
    echo ""
    do_status
}

do_start() {
    if launchctl list | grep -q "$PLIST_NAME"; then
        warn "Service already running"
        return
    fi

    launchctl load "$PLIST_PATH" 2>/dev/null || true
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
    echo "Service: $PLIST_NAME"
    echo "Plist:   $PLIST_PATH"
    echo "Log:     $LOG_FILE"
    echo ""

    if launchctl list | grep -q "$PLIST_NAME"; then
        echo -e "Status:  ${GREEN}Running${NC}"

        # Get PID
        PID=$(launchctl list | grep "$PLIST_NAME" | awk '{print $1}')
        if [ "$PID" != "-" ] && [ -n "$PID" ]; then
            echo "PID:     $PID"
        fi

        # Test health endpoint
        echo ""
        echo "Health check:"
        if curl -s --connect-timeout 2 "http://127.0.0.1:8422/health" 2>/dev/null | grep -q "ok"; then
            echo -e "  localhost:8422  ${GREEN}OK${NC}"
        else
            echo -e "  localhost:8422  ${RED}FAILED${NC}"
        fi

        # Check Tailscale IP
        TAILSCALE_IP=$(ifconfig | grep -A1 utun | grep "inet " | head -1 | awk '{print $2}')
        if [ -n "$TAILSCALE_IP" ]; then
            if curl -s --connect-timeout 2 "http://${TAILSCALE_IP}:8422/health" 2>/dev/null | grep -q "ok"; then
                echo -e "  ${TAILSCALE_IP}:8422  ${GREEN}OK${NC}"
            else
                echo -e "  ${TAILSCALE_IP}:8422  ${RED}FAILED${NC}"
            fi
        fi
    else
        echo -e "Status:  ${RED}Stopped${NC}"
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
        warn "No log file found at $LOG_FILE"
    fi
}

# Main
case "${1:-status}" in
    install)
        do_install
        ;;
    start)
        do_start
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_restart
        ;;
    status)
        do_status
        ;;
    uninstall)
        do_uninstall
        ;;
    logs)
        do_logs
        ;;
    *)
        echo "Usage: $0 {install|start|stop|restart|status|uninstall|logs}"
        exit 1
        ;;
esac
