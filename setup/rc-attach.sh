#!/bin/bash
# rc-attach: Quick attach to remote-claude tmux sessions
# Add to your .bashrc/.zshrc or use directly
#
# Usage:
#   rc-attach              # List sessions and attach interactively
#   rc-attach <name>       # Attach to specific session
#   rc-attach --auto       # Auto-attach to first available session

TMUX_SOCKET="remote-claude"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if already in tmux
if [ -n "$TMUX" ]; then
    echo -e "${YELLOW}Already in a tmux session${NC}"
    echo "Use Ctrl+b s to switch sessions, or detach first (Ctrl+b d)"
    exit 0
fi

# Get list of rc sessions
get_sessions() {
    tmux -L "$TMUX_SOCKET" list-sessions -F "#{session_name}|#{session_attached}|#{session_windows}" 2>/dev/null | grep "^rc-"
}

# Display sessions in a nice format
list_sessions() {
    echo -e "${BLUE}=== Remote Claude Sessions ===${NC}"
    echo ""

    SESSIONS=$(get_sessions)

    if [ -z "$SESSIONS" ]; then
        echo -e "${YELLOW}No active sessions found.${NC}"
        echo ""
        echo "Start a new session with:"
        echo "  rc start <workspace-path>"
        return 1
    fi

    echo -e "  ${GREEN}#${NC}  ${GREEN}Session${NC}              ${GREEN}Windows${NC}  ${GREEN}Status${NC}"
    echo "  --- -------------------- -------- --------"

    i=1
    while IFS='|' read -r name attached windows; do
        if [ "$attached" = "1" ]; then
            status="${GREEN}attached${NC}"
        else
            status="detached"
        fi
        printf "  %-3s %-20s %-8s %b\n" "$i" "$name" "$windows" "$status"
        ((i++))
    done <<< "$SESSIONS"

    echo ""
    return 0
}

# Attach to a session by number or name
attach_session() {
    local target="$1"

    # Check if target is a number
    if [[ "$target" =~ ^[0-9]+$ ]]; then
        # Get session name by line number
        SESSION=$(get_sessions | sed -n "${target}p" | cut -d'|' -f1)
        if [ -z "$SESSION" ]; then
            echo -e "${RED}Error: Invalid session number${NC}"
            return 1
        fi
    else
        # Match by name (partial match)
        MATCHES=$(get_sessions | grep "$target" | cut -d'|' -f1)
        MATCH_COUNT=$(echo "$MATCHES" | grep -c "^" 2>/dev/null || echo 0)

        if [ "$MATCH_COUNT" -eq 0 ] || [ -z "$MATCHES" ]; then
            echo -e "${RED}Error: No session matching '$target'${NC}"
            return 1
        elif [ "$MATCH_COUNT" -gt 1 ]; then
            echo -e "${YELLOW}Multiple sessions match '$target':${NC}"
            echo "$MATCHES"
            return 1
        fi
        SESSION="$MATCHES"
    fi

    echo -e "${GREEN}Attaching to: $SESSION${NC}"
    echo "Detach with: Ctrl+b d"
    echo ""
    sleep 0.5
    exec tmux -L "$TMUX_SOCKET" attach-session -t "$SESSION"
}

# Auto-attach to first available session
auto_attach() {
    FIRST_SESSION=$(get_sessions | head -1 | cut -d'|' -f1)

    if [ -z "$FIRST_SESSION" ]; then
        echo -e "${YELLOW}No sessions available for auto-attach${NC}"
        return 1
    fi

    attach_session "$FIRST_SESSION"
}

# Interactive session selection
interactive_select() {
    list_sessions || return 1

    echo -n "Enter session number or name (q to quit): "
    read -r selection

    if [ "$selection" = "q" ] || [ "$selection" = "Q" ]; then
        return 0
    fi

    if [ -n "$selection" ]; then
        attach_session "$selection"
    fi
}

# ============================================================
# Main
# ============================================================

case "${1:-}" in
    --auto|-a)
        auto_attach
        ;;
    --list|-l)
        list_sessions
        ;;
    --help|-h)
        echo "Usage: rc-attach [options] [session]"
        echo ""
        echo "Options:"
        echo "  --auto, -a     Auto-attach to first available session"
        echo "  --list, -l     List sessions without attaching"
        echo "  --help, -h     Show this help"
        echo ""
        echo "Examples:"
        echo "  rc-attach              # Interactive selection"
        echo "  rc-attach 1            # Attach to session #1"
        echo "  rc-attach myproject    # Attach to session matching 'myproject'"
        echo "  rc-attach --auto       # Auto-attach to first session"
        ;;
    "")
        interactive_select
        ;;
    *)
        attach_session "$1"
        ;;
esac
