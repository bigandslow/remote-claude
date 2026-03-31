#!/bin/bash
# Remote Claude container entrypoint
# Starts Claude Code in YOLO mode with safety protections

set -e

# Ensure ~/.local/bin is on PATH for all processes (including git hook subprocesses).
# pip installs tools here (mypy, ruff, etc.) and they must be reachable by
# non-login shells like those spawned by subprocess.run() in pre-commit hooks.
export PATH="/home/claude/.local/bin:$PATH"
echo 'export PATH="/home/claude/.local/bin:$PATH"' >> /home/claude/.bashrc
echo 'export PATH="/home/claude/.local/bin:$PATH"' >> /home/claude/.profile

# Set up worktree .git linkage for container-internal paths
# When a workspace is a git worktree, docker_manager mounts the commondir at
# /commondir/.git with isolation (read-only base, read-write overlays).
# This function rewrites the .git file and worktree metadata to use
# container-internal paths instead of host paths.
setup_worktree() {
    if [ -z "$RC_WORKTREE_NAME" ]; then
        return 0
    fi

    local wt_gitdir="/commondir/.git/worktrees/$RC_WORKTREE_NAME"

    # Copy host worktree state (HEAD, index, etc.) from read-only staging
    # mount into the writable tmpfs. This isolates container writes from host.
    if [ -d /tmp/rc-worktree-host ]; then
        cp -r /tmp/rc-worktree-host/. "$wt_gitdir/"
    fi

    # Fix commondir to use absolute container path instead of relative
    # (relative "../.." would resolve wrong with the new mount layout)
    echo "/commondir/.git" > "$wt_gitdir/commondir"

    # Update gitdir backlink to container-internal worktree path
    # (The .git file at /workspace/.git is already set via a read-only
    # bind mount from docker_manager, so we don't write to it here.)
    echo "/workspace/.git" > "$wt_gitdir/gitdir"
}

setup_worktree

# Fix SSH config paths for container environment
# The SSH config is generated on the host with host paths, but we need container paths
fix_ssh_config_paths() {
    local ssh_config="/home/claude/.ssh/config"

    if [ ! -f "$ssh_config" ]; then
        return 0
    fi

    # Copy to writable location and fix paths
    cp "$ssh_config" /tmp/.ssh-config
    # Replace any host path patterns with container path
    # Host paths look like: /Users/*/.../.ssh/ or /home/*/.../.ssh/
    sed -i 's|IdentityFile .*/\.ssh/|IdentityFile /home/claude/.ssh/|g' /tmp/.ssh-config

    # Use the fixed config
    mkdir -p /home/claude/.ssh-fixed
    cp /tmp/.ssh-config /home/claude/.ssh-fixed/config
    chmod 600 /home/claude/.ssh-fixed/config

    # Point SSH to use the fixed config - persist to profile and bashrc for all shell types
    export GIT_SSH_COMMAND="ssh -F /home/claude/.ssh-fixed/config"
    echo 'export GIT_SSH_COMMAND="ssh -F /home/claude/.ssh-fixed/config"' >> /home/claude/.bashrc
    echo 'export GIT_SSH_COMMAND="ssh -F /home/claude/.ssh-fixed/config"' >> /home/claude/.profile
}

# Set up git URL rewriting for deploy keys
# Note: Run from HOME, not workspace, to avoid worktree git path issues
setup_deploy_keys() {
    local registry_file="/home/claude/.deploy-keys-registry.json"

    if [ ! -f "$registry_file" ]; then
        return 0
    fi

    # Skip if no repos specified (projects must opt in via .rc/project.yaml)
    if [ -z "$RC_DEPLOY_KEY_REPOS" ]; then
        return 0
    fi

    # Fix SSH config paths first
    fix_ssh_config_paths

    echo "Configuring git for deploy keys..."

    # Parse registry and set up git insteadOf rules for matching repos.
    # RC_DEPLOY_KEY_REPOS (comma-separated) scopes which repos get configured.
    python3 << 'EOF'
import json
import os
import subprocess
from pathlib import Path

registry_file = Path("/home/claude/.deploy-keys-registry.json")
if not registry_file.exists():
    exit(0)

# Only configure repos listed in RC_DEPLOY_KEY_REPOS
allowed = os.environ.get("RC_DEPLOY_KEY_REPOS", "")
if not allowed:
    exit(0)
allowed_repos = set(r.strip() for r in allowed.split(",") if r.strip())

registry = json.loads(registry_file.read_text())

for repo, info in registry.get("repos", {}).items():
    if repo not in allowed_repos:
        continue

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
    # Use --add to allow multiple insteadOf values for the same URL
    subprocess.run([
        "git", "-C", "/home/claude", "config", "--global", "--add",
        f"url.{new_url}.insteadOf", insteadof_ssh
    ], check=True)
    subprocess.run([
        "git", "-C", "/home/claude", "config", "--global", "--add",
        f"url.{new_url}.insteadOf", insteadof_https
    ], check=True)
    subprocess.run([
        "git", "-C", "/home/claude", "config", "--global", "--add",
        f"url.{new_url}.insteadOf", insteadof_https_no_ext
    ], check=True)

    print(f"  Configured: {repo} -> github-{alias}")
EOF
}


# Set up Claude settings with safety hook
# Uses settings.local.json to avoid polluting host's settings.json
setup_safety_hook() {
    local claude_dir="/home/claude/.claude"
    local settings_local="$claude_dir/settings.local.json"
    local hook_path="/home/claude/.rc-hooks/safety.py"

    # Ensure claude config directory exists
    mkdir -p "$claude_dir"

    # Add safety hook to settings.local.json (not the main settings.json)
    # This avoids polluting the host's settings when ~/.claude is mounted
    if [ -f "$hook_path" ] && command -v jq &> /dev/null; then
        local hook_cmd="python3 $hook_path"

        # Create or update settings.local.json with the hook
        if [ -f "$settings_local" ]; then
            # Check if hook already configured
            if ! grep -q "safety.py" "$settings_local" 2>/dev/null; then
                local tmp_file=$(mktemp)
                jq --arg hook "$hook_cmd" '
                    .hooks //= {} |
                    .hooks.PreToolUse //= [] |
                    .hooks.PreToolUse += [{
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": $hook}]
                    }]
                ' "$settings_local" > "$tmp_file"
                mv "$tmp_file" "$settings_local"
            fi
        else
            # Create new settings.local.json with hook
            cat > "$settings_local" << EOJSON
{
  "skipDangerousModePermissionPrompt": true,
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "$hook_cmd"}]
      }
    ]
  }
}
EOJSON
        fi

        # Ensure skipDangerousModePermissionPrompt is set (may be missing from existing file)
        if ! grep -q "skipDangerousModePermissionPrompt" "$settings_local" 2>/dev/null; then
            local tmp_file=$(mktemp)
            jq '.skipDangerousModePermissionPrompt = true' "$settings_local" > "$tmp_file"
            mv "$tmp_file" "$settings_local"
        fi
    fi
}

# Set up git config - default to mounted config, or writable copy for deploy keys
if [ -n "$RC_USE_DEPLOY_KEYS" ]; then
    # Deploy keys need a writable config to add insteadOf rules
    if [ -f /home/claude/.gitconfig ]; then
        cp /home/claude/.gitconfig /tmp/.gitconfig
    fi
    export GIT_CONFIG_GLOBAL=/tmp/.gitconfig
    echo 'export GIT_CONFIG_GLOBAL=/tmp/.gitconfig' >> /home/claude/.bashrc
    echo 'export GIT_CONFIG_GLOBAL=/tmp/.gitconfig' >> /home/claude/.profile
    setup_deploy_keys
elif [ -f /home/claude/.gitconfig ]; then
    # Use mounted gitconfig directly
    export GIT_CONFIG_GLOBAL=/home/claude/.gitconfig
    echo 'export GIT_CONFIG_GLOBAL=/home/claude/.gitconfig' >> /home/claude/.bashrc
    echo 'export GIT_CONFIG_GLOBAL=/home/claude/.gitconfig' >> /home/claude/.profile
fi

# For worktree containers, the workspace and commondir are owned by the host
# UID, not the container claude user. Mark them as safe for git.
# This must run after git config setup above so GIT_CONFIG_GLOBAL is writable.
if [ -n "$RC_WORKTREE_NAME" ]; then
    git config --global --add safe.directory /workspace
    git config --global --add safe.directory /commondir/.git
fi

# Fix plugin paths: host plugins are mounted read-only at plugins-host/.
# Copy to the real plugins/ location with host paths rewritten to container paths.
if [ -d /home/claude/.claude/plugins-host ]; then
    mkdir -p /home/claude/.claude/plugins
    cp -rT /home/claude/.claude/plugins-host /home/claude/.claude/plugins
    if [ -n "$RC_HOST_CLAUDE_DIR" ]; then
        # Rewrite host paths in all plugin JSON files
        find /home/claude/.claude/plugins -name "*.json" -exec \
            sed -i "s|$RC_HOST_CLAUDE_DIR|/home/claude/.claude|g" {} +
    fi
fi

# Configure safety protections
setup_safety_hook

# Run project setup commands passed via environment variable
if [ -n "$RC_SETUP_SCRIPT" ]; then
    echo "Running project setup commands..."
    bash -c "$RC_SETUP_SCRIPT"
fi

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
