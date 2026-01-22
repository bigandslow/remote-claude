#!/bin/bash
# Remote Claude container entrypoint
# Starts Claude Code in YOLO mode with safety protections

set -e

# Ensure we're in the workspace
cd /workspace

# Set up git URL rewriting for deploy keys
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

    subprocess.run([
        "git", "config", "--global",
        f"url.{new_url}.insteadOf", insteadof_ssh
    ], check=True)
    subprocess.run([
        "git", "config", "--global",
        f"url.{new_url}.insteadOf", insteadof_https
    ], check=True)
    subprocess.run([
        "git", "config", "--global",
        f"url.{new_url}.insteadOf", insteadof_https_no_ext
    ], check=True)

    print(f"  Configured: {repo} -> github-{alias}")
EOF
}

# Set up Claude settings with safety hook
setup_safety_hook() {
    local claude_dir="/home/claude/.claude"
    local settings_file="$claude_dir/settings.json"
    local host_settings="/home/claude/.claude-host/settings.json"
    local hook_path="/home/claude/.rc-hooks/safety.py"

    # Create claude config directory
    mkdir -p "$claude_dir"

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
                    "hooks": [$hook]
                }]
            ' "$settings_file" > "$tmp_file"
            mv "$tmp_file" "$settings_file"
        fi
    fi
}

# Configure deploy keys if enabled
if [ -n "$RC_USE_DEPLOY_KEYS" ]; then
    setup_deploy_keys
fi

# Configure safety protections
setup_safety_hook

# If a prompt was passed, use it; otherwise start interactive
if [ -n "$RC_PROMPT" ]; then
    exec claude --dangerously-skip-permissions -p "$RC_PROMPT"
elif [ -n "$RC_CONTINUE" ]; then
    exec claude --dangerously-skip-permissions --continue
else
    exec claude --dangerously-skip-permissions
fi
