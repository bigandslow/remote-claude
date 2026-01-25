#!/bin/bash
# Remote Claude container entrypoint
# Starts Claude Code in YOLO mode with safety protections

set -e

# Set up git URL rewriting for deploy keys
# Note: Run from HOME, not workspace, to avoid worktree git path issues
setup_deploy_keys() {
    local registry_file="/home/claude/.deploy-keys-registry.json"

    if [ ! -f "$registry_file" ]; then
        return 0
    fi

    echo "Configuring git for deploy keys..."

    # Parse registry and set up git insteadOf rules for each repo
    python3 << 'EOF'
import json
import subprocess
from pathlib import Path

registry_file = Path("/home/claude/.deploy-keys-registry.json")
if not registry_file.exists():
    exit(0)

registry = json.loads(registry_file.read_text())

for repo, info in registry.get("repos", {}).items():
    alias = info["alias"]
    org, repo_name = repo.split("/", 1)

    # Set up URL rewriting so standard github.com URLs use the deploy key alias
    # git@github.com:org/repo.git -> git@github-alias:org/repo.git
    # https://github.com/org/repo.git -> git@github-alias:org/repo.git
    insteadof_ssh = f"git@github.com:{repo}.git"
    insteadof_https = f"https://github.com/{repo}.git"
    insteadof_https_no_ext = f"https://github.com/{repo}"
    new_url = f"git@github-{alias}:{repo}.git"

    # Run git config from HOME to avoid worktree path issues
    subprocess.run([
        "git", "-C", "/home/claude", "config", "--global",
        f"url.{new_url}.insteadOf", insteadof_ssh
    ], check=True)
    subprocess.run([
        "git", "-C", "/home/claude", "config", "--global",
        f"url.{new_url}.insteadOf", insteadof_https
    ], check=True)
    subprocess.run([
        "git", "-C", "/home/claude", "config", "--global",
        f"url.{new_url}.insteadOf", insteadof_https_no_ext
    ], check=True)

    print(f"  Configured: {repo} -> github-{alias}")
EOF
}

# Skip onboarding prompts (theme selection, login, etc.)
setup_onboarding_complete() {
    local claude_json="/home/claude/.claude.json"
    local host_claude_json="/home/claude/.claude-host.json"

    # Start with host's .claude.json if available (has oauthAccount for login bypass)
    # Otherwise create minimal file
    if [ -f "$host_claude_json" ] && command -v jq &> /dev/null; then
        # Copy host file and merge in required fields
        jq '{
            oauthAccount: .oauthAccount,
            hasCompletedOnboarding: true,
            lastOnboardingVersion: "99.0.0",
            numStartups: 1
        }' "$host_claude_json" > "$claude_json"
    else
        # Create minimal .claude.json to skip onboarding
        cat > "$claude_json" << 'EOJSON'
{
  "hasCompletedOnboarding": true,
  "lastOnboardingVersion": "99.0.0",
  "numStartups": 1
}
EOJSON
    fi
    chmod 600 "$claude_json"
}

# Set up Claude settings with safety hook
setup_safety_hook() {
    local claude_dir="/home/claude/.claude"
    local settings_file="$claude_dir/settings.json"
    local host_settings="/home/claude/.claude-host/settings.json"
    local host_credentials="/home/claude/.claude-host/.credentials.json"
    local hook_path="/home/claude/.rc-hooks/safety.py"

    # Create claude config directory
    mkdir -p "$claude_dir"

    # Copy credentials file if it exists (for subscription auth)
    if [ -f "$host_credentials" ]; then
        cp "$host_credentials" "$claude_dir/.credentials.json"
        chmod 600 "$claude_dir/.credentials.json"
    fi

    # Start with host settings if they exist, otherwise empty object
    if [ -f "$host_settings" ]; then
        cp "$host_settings" "$settings_file"
    else
        echo '{}' > "$settings_file"
    fi

    # Add safety hook if it exists and jq is available
    if [ -f "$hook_path" ] && command -v jq &> /dev/null; then
        local hook_cmd="python3 $hook_path"

        # Check if hook already configured
        if ! grep -q "safety.py" "$settings_file" 2>/dev/null; then
            # Add the PreToolUse hook for Bash commands
            local tmp_file=$(mktemp)
            jq --arg hook "$hook_cmd" '
                .hooks //= {} |
                .hooks.PreToolUse //= [] |
                .hooks.PreToolUse += [{
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": $hook}]
                }]
            ' "$settings_file" > "$tmp_file"
            mv "$tmp_file" "$settings_file"
        fi
    fi
}

# Configure deploy keys if enabled
if [ -n "$RC_USE_DEPLOY_KEYS" ]; then
    # The mounted gitconfig is read-only, so copy to writable location
    # and use GIT_CONFIG_GLOBAL to point git to the writable copy
    if [ -f /home/claude/.gitconfig ]; then
        cp /home/claude/.gitconfig /tmp/.gitconfig
        export GIT_CONFIG_GLOBAL=/tmp/.gitconfig
    fi
    setup_deploy_keys
fi

# Configure safety protections
setup_safety_hook

# Skip onboarding prompts
setup_onboarding_complete

# For setup mode, run once and exit (no loop)
# For normal mode, run in a loop so /exit triggers restart
if [ -n "$RC_SETUP_MODE" ]; then
    exec claude --dangerously-skip-permissions
fi

# Run Claude in a loop so /exit triggers a restart (picks up new MCP configs, etc.)
# After first run, use --continue to resume the conversation
first_run=true

while true; do
    if [ "$first_run" = true ]; then
        first_run=false
        if [ -n "$RC_PROMPT" ]; then
            claude --dangerously-skip-permissions -p "$RC_PROMPT"
        elif [ -n "$RC_CONTINUE" ]; then
            claude --dangerously-skip-permissions --continue
        else
            claude --dangerously-skip-permissions
        fi
    else
        # Restart: continue previous conversation
        echo ""
        echo "Restarting Claude (continuing previous session)..."
        echo ""
        claude --dangerously-skip-permissions --continue
    fi

    exit_code=$?

    # If Claude exited with error, show message
    if [ $exit_code -ne 0 ]; then
        echo ""
        echo "Claude exited with code $exit_code"
    fi

    echo ""
    echo "Claude exited. Restarting in 2s... (Ctrl+C twice to stop container)"
    sleep 2
done
