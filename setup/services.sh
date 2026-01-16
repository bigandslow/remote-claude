#!/bin/bash
#
# Remote Claude Services Manager
# Manages both responder and watcher services
#
# Usage:
#   ./services.sh install   # Install and start all services
#   ./services.sh start     # Start all services
#   ./services.sh stop      # Stop all services
#   ./services.sh restart   # Restart all services
#   ./services.sh status    # Check status of all services
#   ./services.sh uninstall # Remove all services

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

header() { echo -e "\n${BLUE}=== $1 ===${NC}\n"; }

case "${1:-status}" in
    install)
        header "Installing Responder Service"
        "$SCRIPT_DIR/responder-service.sh" install

        header "Installing Watcher Service"
        "$SCRIPT_DIR/watcher-service.sh" install

        header "All Services Installed"
        echo "Both services will auto-start on login."
        ;;
    start)
        header "Starting Services"
        "$SCRIPT_DIR/responder-service.sh" start
        "$SCRIPT_DIR/watcher-service.sh" start
        ;;
    stop)
        header "Stopping Services"
        "$SCRIPT_DIR/responder-service.sh" stop
        "$SCRIPT_DIR/watcher-service.sh" stop
        ;;
    restart)
        header "Restarting Services"
        "$SCRIPT_DIR/responder-service.sh" restart
        "$SCRIPT_DIR/watcher-service.sh" restart
        ;;
    status)
        header "Responder Service"
        "$SCRIPT_DIR/responder-service.sh" status

        header "Watcher Service"
        "$SCRIPT_DIR/watcher-service.sh" status
        ;;
    uninstall)
        header "Uninstalling Services"
        "$SCRIPT_DIR/responder-service.sh" uninstall
        "$SCRIPT_DIR/watcher-service.sh" uninstall
        ;;
    *)
        echo "Remote Claude Services Manager"
        echo ""
        echo "Usage: $0 {install|start|stop|restart|status|uninstall}"
        echo ""
        echo "Commands:"
        echo "  install   - Install and start all services (responder + watcher)"
        echo "  start     - Start all services"
        echo "  stop      - Stop all services"
        echo "  restart   - Restart all services"
        echo "  status    - Check status of all services"
        echo "  uninstall - Remove all services"
        exit 1
        ;;
esac
