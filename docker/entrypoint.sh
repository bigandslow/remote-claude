#!/bin/bash
# Remote Claude container entrypoint
# Starts Claude Code in YOLO mode with optional initial prompt

set -e

# Ensure we're in the workspace
cd /workspace

# If a prompt was passed, use it; otherwise start interactive
if [ -n "$RC_PROMPT" ]; then
    exec claude --dangerously-skip-permissions -p "$RC_PROMPT"
elif [ -n "$RC_CONTINUE" ]; then
    exec claude --dangerously-skip-permissions --continue
else
    exec claude --dangerously-skip-permissions
fi
