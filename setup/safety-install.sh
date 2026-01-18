#!/bin/bash
#
# Install safety protections for YOLO mode
#
# This script:
# 1. Configures git to prevent accidental force pushes
# 2. Adds the safety hook to Claude Code settings
# 3. Verifies the installation
#
# Usage:
#   ./setup/safety-install.sh          # Install
#   ./setup/safety-install.sh remove   # Uninstall

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SAFETY_HOOK="$PROJECT_DIR/hooks/safety.py"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
header() { echo -e "\n${BLUE}=== $1 ===${NC}"; }

configure_git() {
    header "Configuring Git Safety Settings"

    # Prevent accidental force push
    current_push_default=$(git config --global push.default 2>/dev/null || echo "")
    if [ "$current_push_default" != "simple" ]; then
        info "Setting push.default to 'simple'"
        git config --global push.default simple
    else
        info "push.default already set to 'simple'"
    fi

    # Require explicit force push
    info "Git configured to require explicit force push"
    echo ""
    echo "Recommended: Add these aliases for safer operations:"
    echo "  git config --global alias.force-push 'push --force-with-lease'"
    echo "  git config --global alias.hard-reset 'reset --hard'"
}

install_hook() {
    header "Installing Safety Hook"

    # Check if safety.py exists
    if [ ! -f "$SAFETY_HOOK" ]; then
        error "safety.py not found at $SAFETY_HOOK"
        exit 1
    fi

    # Make executable
    chmod +x "$SAFETY_HOOK"

    # Create Claude settings directory if needed
    mkdir -p "$HOME/.claude"

    # Check if settings.json exists
    if [ ! -f "$CLAUDE_SETTINGS" ]; then
        info "Creating new Claude settings file"
        echo '{}' > "$CLAUDE_SETTINGS"
    fi

    # Check if jq is available for JSON manipulation
    if command -v jq &> /dev/null; then
        # Use jq to safely modify JSON
        HOOK_CMD="python3 $SAFETY_HOOK"

        # Check if hook already exists
        existing=$(jq -r '.hooks.PreToolUse // [] | .[] | select(.matcher == "Bash") | .hooks[]?' "$CLAUDE_SETTINGS" 2>/dev/null || echo "")
        if echo "$existing" | grep -q "safety.py"; then
            info "Safety hook already installed"
        else
            info "Adding safety hook to Claude settings"

            # Create the hook entry
            tmp_file=$(mktemp)
            jq --arg hook "$HOOK_CMD" '
                .hooks //= {} |
                .hooks.PreToolUse //= [] |
                .hooks.PreToolUse += [{
                    "matcher": "Bash",
                    "hooks": [$hook]
                }]
            ' "$CLAUDE_SETTINGS" > "$tmp_file"

            mv "$tmp_file" "$CLAUDE_SETTINGS"
            info "Hook added successfully"
        fi
    else
        warn "jq not found - please manually add the hook to $CLAUDE_SETTINGS"
        echo ""
        echo "Add this to your hooks configuration:"
        echo '{'
        echo '  "hooks": {'
        echo '    "PreToolUse": ['
        echo '      {'
        echo '        "matcher": "Bash",'
        echo "        \"hooks\": [\"python3 $SAFETY_HOOK\"]"
        echo '      }'
        echo '    ]'
        echo '  }'
        echo '}'
    fi
}

remove_hook() {
    header "Removing Safety Hook"

    if [ ! -f "$CLAUDE_SETTINGS" ]; then
        info "No Claude settings file found"
        return
    fi

    if command -v jq &> /dev/null; then
        # Remove hook entries containing safety.py
        tmp_file=$(mktemp)
        jq '
            if .hooks.PreToolUse then
                .hooks.PreToolUse |= map(select(.hooks | any(contains("safety.py")) | not))
            else
                .
            end
        ' "$CLAUDE_SETTINGS" > "$tmp_file"

        mv "$tmp_file" "$CLAUDE_SETTINGS"
        info "Safety hook removed from Claude settings"
    else
        warn "jq not found - please manually remove the hook from $CLAUDE_SETTINGS"
    fi
}

verify_installation() {
    header "Verifying Installation"

    # Check safety.py
    if [ -x "$SAFETY_HOOK" ]; then
        info "safety.py is executable"
    else
        error "safety.py is not executable"
    fi

    # Check Claude settings
    if [ -f "$CLAUDE_SETTINGS" ]; then
        if grep -q "safety.py" "$CLAUDE_SETTINGS"; then
            info "Hook is configured in Claude settings"
        else
            warn "Hook not found in Claude settings"
        fi
    fi

    # Test the hook
    info "Testing hook with sample command..."
    echo '{"tool_name": "Bash", "tool_input": {"command": "ls -la"}}' | python3 "$SAFETY_HOOK"
    if [ $? -eq 0 ]; then
        info "Hook allows safe commands"
    fi

    # Test blocking
    result=$(echo '{"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}' | python3 "$SAFETY_HOOK" 2>&1 || true)
    if echo "$result" | grep -q "block"; then
        info "Hook blocks dangerous commands"
    else
        warn "Hook may not be blocking correctly"
    fi

    echo ""
    info "Installation complete!"
    echo ""
    echo "Protected operations (BLOCKED):"
    echo "  - git push --force"
    echo "  - git reset --hard"
    echo "  - rm -rf /, ~, *"
    echo "  - DROP DATABASE, TRUNCATE"
    echo "  - gcloud/aws destructive commands"
    echo "  - Modifying .claude/settings.json"
    echo "  - Modifying .git/hooks/*"
    echo "  - Modifying safety.py itself"
    echo ""
    echo "Escalated operations (asks user):"
    echo "  - pulumi *"
    echo "  - terraform apply/destroy"
    echo "  - kubectl delete"
    echo "  - Database migrations"
    echo ""
    echo "Logs: /tmp/rc-safety-logs/"
}

show_status() {
    header "Safety Protection Status"

    echo "Git Settings:"
    echo "  push.default: $(git config --global push.default 2>/dev/null || echo 'not set')"
    echo ""

    echo "Claude Settings:"
    if [ -f "$CLAUDE_SETTINGS" ]; then
        if grep -q "safety.py" "$CLAUDE_SETTINGS"; then
            echo "  Safety hook: INSTALLED"
        else
            echo "  Safety hook: NOT INSTALLED"
        fi
    else
        echo "  Settings file: NOT FOUND"
    fi
    echo ""

    echo "Log Directory:"
    if [ -d "/tmp/rc-safety-logs" ]; then
        count=$(ls -1 /tmp/rc-safety-logs/*.log 2>/dev/null | wc -l)
        echo "  Location: /tmp/rc-safety-logs/"
        echo "  Log files: $count"
    else
        echo "  No logs yet"
    fi
}

# Main
case "${1:-install}" in
    install)
        configure_git
        install_hook
        verify_installation
        ;;
    remove|uninstall)
        remove_hook
        info "Git settings left unchanged (manual reset if needed)"
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 {install|remove|status}"
        exit 1
        ;;
esac
